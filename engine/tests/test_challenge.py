import time

import pytest
from fastapi.testclient import TestClient

from app import challenge, store
from app.main import app
from helpers import AUTH, live_scan, scan_to_b64
from model_guard import requires_models

client = TestClient(app, headers=AUTH)


@pytest.fixture(autouse=True)
def _clean():
    store.reset()
    challenge.reset()
    yield
    store.reset()
    challenge.reset()


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


@pytest.fixture
def enrolled(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    assert r.status_code == 200, r.json()
    return "alice"


def frozen_clock(monkeypatch, at_ms: int) -> None:
    monkeypatch.setattr(challenge, "_now_ms", lambda: at_ms)


def just_expired() -> int:
    return int(time.time() * 1000) + challenge.EXPIRY_MS + 1


def test_challenge_endpoint_shape():
    body = client.get("/v1/challenge").json()
    assert set(body) == {"nonce", "action", "params", "issued_ms", "expires_ms"}
    assert len(body["nonce"]) == 2 * challenge.NONCE_BYTES
    int(body["nonce"], 16)
    assert body["action"] in challenge.ISSUABLE_ACTIONS
    assert body["expires_ms"] - body["issued_ms"] == challenge.EXPIRY_MS


def test_every_challenge_is_a_new_nonce():
    nonces = {client.get("/v1/challenge").json()["nonce"] for _ in range(25)}
    assert len(nonces) == 25
    assert challenge.outstanding() == 25


def test_challenge_requires_the_engine_key():
    r = TestClient(app).get("/v1/challenge")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_blink_twice_is_never_issued():
    assert challenge.BLINK_TWICE in challenge.ACTIONS
    assert challenge.BLINK_TWICE not in challenge.ISSUABLE_ACTIONS
    actions = {client.get("/v1/challenge").json()["action"] for _ in range(60)}
    assert challenge.BLINK_TWICE not in actions
    assert actions == set(challenge.ISSUABLE_ACTIONS), (
        "every issuable action must occur"
    )


def test_head_sequence_is_what_the_engine_asks_for():
    assert challenge.ISSUABLE_ACTIONS == (challenge.HEAD_SEQUENCE,)
    assert {challenge.LOOK_LEFT, challenge.LOOK_RIGHT} <= set(challenge.ACTIONS)
    assert client.get("/v1/challenge").json()["action"] == challenge.HEAD_SEQUENCE


def test_a_freshly_issued_nonce_checks_out():
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is True
    assert verdict.reason is None
    assert verdict.action == issued["action"]
    assert verdict.consumed is True


def test_a_nonce_is_single_use():
    issued = challenge.issue()
    assert challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    ).ok
    second = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert second.ok is False
    assert second.reason == challenge.REUSED_NONCE


def test_an_unknown_nonce_is_rejected():
    verdict = challenge.check(
        "00" * challenge.NONCE_BYTES, challenge.LOOK_LEFT, spend=True
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.UNKNOWN_NONCE
    assert verdict.action is None, "no record, so nothing to say about the action"


def test_a_missing_nonce_is_rejected_not_waved_through():
    verdict = challenge.check("", None, spend=True)
    assert verdict.ok is False
    assert verdict.reason == challenge.MISSING_NONCE


def test_an_expired_nonce_is_rejected(monkeypatch):
    issued = challenge.issue()
    frozen_clock(monkeypatch, issued["expires_ms"] + 1)
    verdict = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.EXPIRED_NONCE


def test_a_nonce_is_still_valid_on_its_last_millisecond(monkeypatch):
    issued = challenge.issue()
    frozen_clock(monkeypatch, issued["expires_ms"])
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=True,
            presented_params=issued["params"],
        ).ok
        is True
    )


def test_a_mismatched_action_is_rejected():
    issued = challenge.issue()
    other = (
        challenge.LOOK_LEFT
        if issued["action"] != challenge.LOOK_LEFT
        else challenge.LOOK_RIGHT
    )
    verdict = challenge.check(
        issued["nonce"], other, spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.ACTION_MISMATCH
    assert verdict.action == issued["action"]
    assert verdict.presented_action == other


def test_an_action_outside_the_vocabulary_is_rejected():
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"], "LOOK_UP", spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.UNKNOWN_ACTION


def test_a_failed_check_still_spends_the_nonce():
    issued = challenge.issue()
    assert (
        challenge.check(
            issued["nonce"], "LOOK_UP", spend=True, presented_params=issued["params"]
        ).ok
        is False
    )
    again = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert again.reason == challenge.REUSED_NONCE


def test_inspect_does_not_spend():
    issued = challenge.issue()
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=False,
            presented_params=issued["params"],
        ).ok
        is True
    )
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=False,
            presented_params=issued["params"],
        ).ok
        is True
    )
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=True,
            presented_params=issued["params"],
        ).ok
        is True
    )
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=True,
            presented_params=issued["params"],
        ).reason
        == challenge.REUSED_NONCE
    )


def test_long_expired_records_are_evicted(monkeypatch):
    issued = challenge.issue()
    assert challenge.outstanding() == 1
    frozen_clock(
        monkeypatch, issued["expires_ms"] + challenge.RETAIN_AFTER_EXPIRY_MS + 1
    )
    challenge.issue()
    assert challenge.outstanding() == 1
    verdict = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert verdict.reason == challenge.UNKNOWN_NONCE, "evicted, so unknown now"


def test_the_store_is_capped(monkeypatch):
    monkeypatch.setattr(challenge, "MAX_ISSUED_NONCES", 8)
    issued = [challenge.issue() for _ in range(50)]
    assert challenge.outstanding() <= 8

    assert challenge.check(
        issued[-1]["nonce"],
        issued[-1]["action"],
        spend=True,
        presented_params=issued[-1]["params"],
    ).ok
    assert (
        challenge.check(
            issued[0]["nonce"],
            issued[0]["action"],
            spend=True,
            presented_params=issued[0]["params"],
        ).reason
        == challenge.UNKNOWN_NONCE
    )


def test_issued_params_are_in_band():
    for _ in range(50):
        params = challenge.issue()["params"]
        assert params["settle_ms"] == 3200
        assert params["switch_ms"] in (9000, 9200, 9400)
        assert params["target"] == 0.18
        assert params["first_sign"] in (-1, 1)


def test_issued_params_vary_between_nonces():
    drawn = {
        (p["first_sign"], p["switch_ms"])
        for p in (challenge.issue()["params"] for _ in range(60))
    }
    assert len(drawn) > 1, "params are not being redrawn per nonce"


def test_params_are_stored_and_echoed_in_the_verdict():
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"],
        issued["action"],
        spend=True,
        presented_params=issued["params"],
    )
    assert verdict.ok is True
    assert verdict.params == issued["params"]
    assert verdict.as_dict()["params"] == issued["params"]


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda p: {**p, "settle_ms": p["settle_ms"] + 100}, id="settle"),
        pytest.param(lambda p: {**p, "target": p["target"] + 0.05}, id="target"),
        pytest.param(lambda p: {"settle_ms": p["settle_ms"]}, id="no-target"),
        pytest.param(lambda p: None, id="absent"),
    ],
)
def test_params_that_are_not_the_issued_ones_are_rejected(mangle):
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"],
        issued["action"],
        spend=True,
        presented_params=mangle(issued["params"]),
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.PARAMS_MISMATCH


def test_sequence_parameters_must_match_exactly():
    issued = challenge.issue()
    noisy = {**issued["params"], "target": issued["params"]["target"] + 1e-9}
    assert not challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=noisy
    ).ok


def test_lowering_the_cap_converges(monkeypatch):
    monkeypatch.setattr(challenge, "MAX_ISSUED_NONCES", 20)
    for _ in range(19):
        challenge.issue()
    monkeypatch.setattr(challenge, "MAX_ISSUED_NONCES", 4)
    challenge.issue()
    assert challenge.outstanding() <= 4


@requires_models
def test_enroll_rejects_a_bad_nonce_and_enrolls_nothing(low_det_thresh):
    scan = live_scan("face_like.jpg")
    scan["challenge"]["nonce"] = "ab" * challenge.NONCE_BYTES
    r = client.post(
        "/v1/enroll", json={"user_id": "mallory", "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert not store.has_user("mallory")
