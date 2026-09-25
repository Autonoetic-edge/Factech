"""CHALLENGE_POLICY=single-turn-v1 (docs/LIVENESS_UPGRADE_PLAN.md Phase 1).

Flag off must stay today's HEAD_SEQUENCE; flag on issues one LEFT/RIGHT turn,
verifies it at 500 ms cadence, binds it to its issue time and judges PAD under
pad-single-v1.
"""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import anti_spoof as pad
from app import challenge, liveness, main, store
from helpers import AUTH, build_scan, scan_to_b64
from test_secure_pad import FakePAD, detection, vector

ON = challenge.POLICY_SINGLE_TURN_V1


@pytest.fixture
def single_turn(monkeypatch):
    monkeypatch.setenv(challenge.POLICY_ENV, ON)


@pytest.mark.parametrize("value", [None, "", "head-sequence", "single-turn", "SINGLE-TURN-V1"])
def test_flag_off_or_unknown_keeps_head_sequence(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(challenge.POLICY_ENV, raising=False)
    else:
        monkeypatch.setenv(challenge.POLICY_ENV, value)
    assert challenge.selected_policy() == challenge.POLICY_HEAD_SEQUENCE
    assert challenge.issuable_actions() == (challenge.HEAD_SEQUENCE,)
    issued = challenge.issue("verify", "test")
    assert issued["action"] == challenge.HEAD_SEQUENCE
    assert set(issued["params"]) == {"settle_ms", "switch_ms", "first_sign", "target"}


def test_flag_on_issues_one_random_turn_with_narrow_settle(single_turn):
    seen = set()
    for _ in range(200):
        issued = challenge.issue("verify", "test")
        seen.add(issued["action"])
        params = issued["params"]
        assert set(params) == {"settle_ms", "target"}
        assert 1200 <= params["settle_ms"] <= 2000
        assert liveness.YAW_DELTA_FLOOR <= params["target"] <= 0.25
    assert seen == {challenge.LOOK_LEFT, challenge.LOOK_RIGHT}


def look_scan(action, *, age_ms=6000, cadence=500, params=None):
    issued = challenge.issue("verify", "test")
    record = challenge._issued[issued["nonce"]]
    record["action"] = action
    record["params"] = dict(params or {"settle_ms": 1500, "target": 0.2})
    record["issued_ms"] -= age_ms
    scan = build_scan()
    scan["challenge"] = {**issued, "action": action, "params": dict(record["params"])}
    for i, f in enumerate(scan["frames"]):
        f["ts_ms"] = i * cadence
    return scan


@pytest.mark.parametrize("action", [challenge.LOOK_LEFT, challenge.LOOK_RIGHT])
def test_capture_before_issue_now_binds_single_turns(single_turn, action):
    fresh = look_scan(action, age_ms=0)
    verdict = challenge.consume_from_scan(fresh, ("verify", "test"))
    assert not verdict.ok and verdict.reason == "capture_before_issue"
    aged = look_scan(action, age_ms=6000)
    assert challenge.consume_from_scan(aged, ("verify", "test")).ok


def turn(action, offsets, settle_ms=1500):
    frames = [{"ts_ms": i * 500} for i in range(12)]
    params = {"settle_ms": settle_ms, "target": 0.2}
    verdict = challenge.ChallengeVerdict(True, None, action, action, params, "t", True)
    return liveness._challenge_signal(frames, [detection(o) for o in offsets], verdict)


SIGN = {challenge.LOOK_LEFT: 1, challenge.LOOK_RIGHT: -1}


@pytest.mark.parametrize("action", [challenge.LOOK_LEFT, challenge.LOOK_RIGHT])
def test_single_turn_verifier_at_500_ms(action):
    s = SIGN[action]
    still_then_turn = [0] * 4 + [3 * s] * 8
    assert turn(action, still_then_turn).score >= liveness.LIVENESS_THRESHOLD
    assert turn(action, [0] * 4 + [-3 * s] * 8).score < 0.5  # wrong way
    assert turn(action, [0] * 12).score < 0.5  # never turned
    assert turn(action, [0, 3 * s, 0, 3 * s] + [3 * s] * 8).score < 0.5  # moved in the hold


def test_single_turn_timing_signal_expects_500_ms():
    frames = [{"ts_ms": i * 500} for i in range(12)]
    verdict = challenge.ChallengeVerdict(
        True, None, challenge.LOOK_LEFT, challenge.LOOK_LEFT, {}, "t", True
    )
    timing = liveness._timing_signal(frames, verdict)
    assert timing.score >= 0.5 and timing.detail["expected_cadence_ms"] == 500


@pytest.fixture
def fast_samples(monkeypatch):
    monkeypatch.setattr(pad, "get_pad", lambda: FakePAD())
    frames = build_scan()["frames"]
    for i, f in enumerate(frames):
        f["ts_ms"] = i * 500
    return frames, [[detection()] for _ in frames]


def evaluate_single(samples):
    return pad.evaluate(
        *samples,
        lambda f, d: vector(),
        lambda b: np.zeros((64, 64, 3), np.uint8),
        pad.SINGLE_POLICY_VERSION,
    )[0]


def test_pad_single_policy_named_and_gap_limit(fast_samples):
    result = evaluate_single(fast_samples)
    assert result["outcome"] == "live" and result["policy"] == "pad-single-v1"
    fast_samples[1][5] = []
    assert evaluate_single(fast_samples)["outcome"] == "live"
    fast_samples[1][6] = []
    assert evaluate_single(fast_samples)["reason"] == "coverage_gap"


def test_policy_for_actions():
    assert pad.policy_for(challenge.HEAD_SEQUENCE) == pad.POLICY_VERSION
    assert pad.policy_for(challenge.LOOK_LEFT) == pad.SINGLE_POLICY_VERSION
    assert pad.policy_for(challenge.LOOK_RIGHT) == pad.SINGLE_POLICY_VERSION
    assert pad.policy_for(None) == pad.POLICY_VERSION


def wire(monkeypatch, seen):
    monkeypatch.setattr(
        main, "_scan_detections", lambda s: [[detection()] for _ in range(12)]
    )

    def fake_evaluate(*args):
        seen.append(args[4] if len(args) > 4 else pad.POLICY_VERSION)
        return {"outcome": "live", "usable": 12, "minimum_detection_score": 0.99}, vector()

    monkeypatch.setattr(pad, "evaluate", fake_evaluate)
    good = liveness.LivenessResult(
        0.8, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *args: good)


def post_verify(client, scan):
    return client.post(
        "/v1/verify", json={"user_id": "test", "facescan": scan_to_b64(scan)}
    )


def test_secure_endpoints_follow_the_flag(monkeypatch):
    store.reset()
    store.enroll("test", vector())
    seen = []
    wire(monkeypatch, seen)
    with TestClient(main.app, headers=AUTH) as client:
        monkeypatch.delenv(challenge.POLICY_ENV, raising=False)
        refused = post_verify(client, look_scan(challenge.LOOK_LEFT))
        assert refused.status_code == 422
        assert "current head sequence required" in refused.text

        monkeypatch.setenv(challenge.POLICY_ENV, ON)
        for action in (challenge.LOOK_LEFT, challenge.LOOK_RIGHT):
            accepted = post_verify(client, look_scan(action))
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["pad"] == {"outcome": "live", "policy": "pad-single-v1"}
            assert accepted.json()["match"] is True
        assert seen == ["pad-single-v1", "pad-single-v1"]

        sequence = look_scan(challenge.HEAD_SEQUENCE, params={
            "settle_ms": 3200, "switch_ms": 9000, "first_sign": 1, "target": 0.18,
        })
        stale = post_verify(client, sequence)
        assert stale.status_code == 422 and "current single turn required" in stale.text
