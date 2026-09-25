"""Replay historical head-gate traces through the synced VPS trial validator.

The historical fixture stays unchanged. Expected movement outcomes below reflect
the authorized September 22 trial; these are not full PAD/identity replays.

The fixture stores the recorded five-point yaw ratio pairs, not landmarks. Each
pair is turned back into a synthetic five-point layout that the real `yaw()`
maps to that same pair, so the replay exercises the shipped function rather than
a stand-in for it. See `tools/hardening_head_gate_fixture.py` for provenance.
"""

import json
from pathlib import Path

import pytest

from app import head_sequence

FIXTURE = Path(__file__).resolve().parent / "fixtures/head_gate_traces.json"
TRACES = json.loads(FIXTURE.read_text(encoding="utf-8"))["traces"]
V2_SLOW = [t for t in TRACES if t["policy"] == "pad-sequence-v2-slow"]

# Reconstruction geometry. Any eye separation and mouth width satisfying
# yaw()'s own minimums work; these are arbitrary and cancel out.
EYE_SPAN = 100.0
MOUTH_SPAN = 60.0


def landmarks_for(eye_nose_x4: float, mouth_nose_x4: float) -> list[list[float]]:
    """Five points whose `yaw()` is (eye_nose_x4, mouth_nose_x4).

    yaw() projects onto the eye axis, so place the eyes on the x axis and
    invert its two ratios directly.
    """
    nose_x = EYE_SPAN / 2 + EYE_SPAN * eye_nose_x4 / 4
    mouth_centre = nose_x - MOUTH_SPAN * mouth_nose_x4 / 4
    return [
        [0.0, 0.0],
        [EYE_SPAN, 0.0],
        [nose_x, 60.0],
        [mouth_centre - MOUTH_SPAN / 2, 120.0],
        [mouth_centre + MOUTH_SPAN / 2, 120.0],
    ]


def replay(trace: dict) -> dict:
    frames = [{"ts_ms": ts} for ts in trace["ts_rel_ms"]]
    detections = [
        None if pair is None else {"landmarks_5": landmarks_for(*pair)}
        for pair in trace["yaw"]
    ]
    return head_sequence.validate(frames, detections, trace["params"])


def test_fixture_covers_the_nine_v2_slow_traces():
    assert [t["id"] for t in V2_SLOW] == list(range(13, 22))


@pytest.mark.parametrize("pair", [(0.0, 0.0), (0.1928, 0.2717), (-1.1787, -1.0921)])
def test_landmark_reconstruction_round_trips_through_the_real_yaw(pair):
    rebuilt = head_sequence.yaw({"landmarks_5": landmarks_for(*pair)})
    assert rebuilt == pytest.approx(pair, abs=1e-9)


@pytest.mark.parametrize("trace", V2_SLOW, ids=lambda t: f"scan{t['id']}")
def test_historical_trace_has_expected_trial_movement_verdict(trace):
    result = replay(trace)
    recorded = trace["recorded"]
    # 16/18/19 fit the relaxed settle limits; 20 fits the one-frame switch grace.
    # 14 clears settling but still lacks sufficient ordered rotation.
    expected_reason = {14: "action_order_or_rotation"}.get(trace["id"])
    assert result["ok"] is (expected_reason is None)
    assert result["reason"] == expected_reason
    assert result["phases"] == recorded["phases"]
    assert result["span_ms"] == recorded["span_ms"]


def test_recorded_outcomes_are_the_four_pass_five_fail_baseline():
    """The measured starting point the new policy will be compared against."""
    passed = [t["id"] for t in V2_SLOW if t["recorded"]["ok"]]
    reasons = {
        t["id"]: t["recorded"]["reason"] for t in V2_SLOW if not t["recorded"]["ok"]
    }
    assert passed == [13, 15, 17, 21]
    assert reasons == {
        14: "not_frontal_and_still",
        16: "not_frontal_and_still",
        18: "not_frontal_and_still",
        19: "not_frontal_and_still",
        20: "opposite_action_in_window",
    }


def test_fixture_carries_no_biometric_payload():
    """Rule: no frames, scan bytes, embeddings or real subject ids in the workspace."""
    allowed = {
        "id",
        "subject",
        "label",
        "endpoint",
        "policy",
        "build_id",
        "action",
        "params",
        "ts_rel_ms",
        "yaw",
        "recorded",
    }
    for trace in TRACES:
        assert set(trace) == allowed
        assert trace["subject"] in {f"S{n}" for n in range(1, 6)}
    # Scan the traces themselves; the document header describes the exclusions.
    body = json.dumps(TRACES)
    for token in ("jpeg", "msgpack", "embedding", "base64", "scan_sha256"):
        assert token not in body
