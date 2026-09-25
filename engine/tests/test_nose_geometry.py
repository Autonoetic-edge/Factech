"""PAD_GEOMETRY nose check and pad-single-v1 cadence (docs/LIVENESS_UPGRADE_PLAN.md Phase 2).

Geometry is log only: it writes a trace record and never decides. Under pad-single-v1 the
duplicate and motion signals are read at 500 ms cadence; every other policy is unchanged.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from app import anti_spoof, challenge, detect, geometry, liveness, main, trace

SINGLE = anti_spoof.SINGLE_POLICY_VERSION
LIVE_LEFT = Path(__file__).parent / "fixtures" / "live_left"

# Eyes, nose, mouth corners of a head model (x right, y down, z towards the camera).
HEAD = np.array(
    [
        [-0.5, 0.0, 0.0],
        [0.5, 0.0, 0.0],
        [0.0, 0.45, 0.3],
        [-0.4, 0.9, 0.0],
        [0.4, 0.9, 0.0],
    ]
)


def keypoints(yaw_rad, *, flat=False, tilt_rad=0.0, jitter=0.0, rng=None, distance=4.0):
    head = HEAD.copy()
    if flat:
        head[:, 2] = 0.0
    c, s = np.cos(yaw_rad), np.sin(yaw_rad)
    ct, st = np.cos(tilt_rad), np.sin(tilt_rad)
    rot_y = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    rot_x = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]])
    world = head @ (rot_x @ rot_y).T
    depth = distance - world[:, 2]
    image = 320 + 400 * world[:, :2] / depth[:, None]
    if jitter:
        image = image + rng.normal(0, jitter, image.shape)
    return {"landmarks_5": image.tolist(), "bbox": [0, 0, 1, 1], "det_score": 0.9}


def scan_of(yaws, **kw):
    detections = [keypoints(y, **kw) for y in yaws]
    return detections, [i * 500 for i in range(len(yaws))]


TURN = [0.0] * 4 + [0.05, 0.1, 0.15, 0.2, 0.2, 0.2, 0.2, 0.2]
# A print has to be swung hard and close to the camera before its nose reads as a turn.
FLAT_TURN = [0.0] * 4 + [0.2, 0.4, 0.5, 0.6, 0.7, 0.7, 0.7, 0.7]
FLAT = {"flat": True, "distance": 1.6}


def test_real_head_turn_scores_above_flat_surface():
    detections, ts = scan_of(TURN)
    real = geometry.nose_residual(detections, ts, 1500)
    assert real["outcome"] == "scored"
    assert real["score"] > 0.04
    assert real["settle_frames"] == 4 and real["turned_frames"] >= 3
    assert all(s["residual"] > 0 and s["yaw"] > 0 for s in real["samples"])
    # The residual grows with the turn.
    ordered = sorted(real["samples"], key=lambda s: s["yaw"])
    assert ordered[-1]["residual"] > ordered[0]["residual"]
    json.dumps(real)


@pytest.mark.parametrize("tilt", [0.0, 0.3])
def test_flat_surface_turned_or_tilted_stays_near_zero(tilt):
    detections, ts = scan_of(FLAT_TURN, tilt_rad=tilt, **FLAT)
    flat = geometry.nose_residual(detections, ts, 1500)
    assert flat["outcome"] == "scored"
    assert flat["score"] < 0.005


def test_detector_noise_keeps_the_two_apart():
    rng = np.random.default_rng(3)
    real = geometry.nose_residual(*scan_of(TURN, jitter=0.5, rng=rng), 1500)
    flat = geometry.nose_residual(
        *scan_of(FLAT_TURN, jitter=0.5, rng=rng, **FLAT), 1500
    )
    assert real["score"] > 2 * flat["score"]


def test_too_few_turned_frames_is_inconclusive_not_a_failure():
    detections, ts = scan_of([0.0] * 10 + [0.2, 0.2])
    record = geometry.nose_residual(detections, ts, 1500)
    assert record["outcome"] == "inconclusive" and record["reason"] == "turned_frames"
    assert record["score"] is None


def test_too_few_settle_frames_or_no_face_is_inconclusive():
    detections, ts = scan_of(TURN)
    assert geometry.nose_residual(detections, ts, 0)["reason"] == "settle_frames"
    assert geometry.nose_residual([None] * 12, ts, 1500)["reason"] == "no_face"


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        (None, "off"),
        ("", "off"),
        ("off", "off"),
        ("log", "log"),
        ("LOG", "log"),
        ("on", "log"),
    ],
)
def test_mode_default_off_and_on_never_rejects_yet(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv(geometry.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(geometry.MODE_ENV, value)
    assert geometry.mode() == mode


def verdict(settle_ms=1500):
    params = {"settle_ms": settle_ms, "target": 0.2}
    return challenge.ChallengeVerdict(
        True, None, challenge.LOOK_LEFT, challenge.LOOK_LEFT, params, "t", True
    )


def test_log_geometry_writes_the_trace_only_when_on(monkeypatch):
    detections, ts = scan_of(TURN)
    frames = [{"ts_ms": t} for t in ts]
    t, token = trace.start("verify", "req-geometry-1")
    try:
        monkeypatch.delenv(geometry.MODE_ENV, raising=False)
        main._log_geometry(frames, detections, verdict())
        assert "geometry" not in t.line(200) and "geometry" not in t.timing_ms
        monkeypatch.setenv(geometry.MODE_ENV, "log")
        main._log_geometry(frames, detections, verdict())
        line = t.line(200)
        assert line["geometry"]["mode"] == "log"
        assert line["geometry"]["outcome"] == "scored"
        json.dumps(line)
        monkeypatch.setattr(geometry, "nose_residual", lambda *a: 1 / 0)
        main._log_geometry(frames, detections, verdict())
        assert t.line(200)["geometry"] == {
            "mode": "log",
            "outcome": "error",
            "reason": "ZeroDivisionError",
        }
    finally:
        trace.reset(token)


def test_precheck_is_recorded_bounded_and_absent_by_default():
    t, token = trace.start("verify", "req-precheck-1")
    try:
        t.precheck(None)
        assert "precheck" not in t.line(200)
        t.precheck(
            '{"luma":81.5,"frame_luma":140.2,"backlight":58.7,"sharp":33.1,'
            '"hint":null,"capped":false,"extra":"dropped"}'
        )
        assert t.line(200)["precheck"] == {
            "luma": 81.5,
            "frame_luma": 140.2,
            "backlight": 58.7,
            "sharp": 33.1,
            "hint": None,
            "capped": False,
        }
        t.precheck("{not json")
        assert t.line(200)["precheck"] == {"unreadable": True}
        t.precheck("x" * 300)
        assert t.line(200)["precheck"] == {"unreadable": True}
        t.precheck('{"luma":{"a":1},"hint":"light"}')
        assert t.line(200)["precheck"]["luma"] is None
        assert t.line(200)["precheck"]["hint"] == "light"
    finally:
        trace.reset(token)


# --- pad-single-v1 at 500 ms: 4 still hold frames plus a turn -----------------


def still_hold_then_turn(still=4):
    jpegs = [p.read_bytes() for p in sorted(LIVE_LEFT.glob("*.jpg"))]
    jpegs = [jpegs[0]] * still + jpegs[still:]
    frames = [{"jpeg_bytes": j, "ts_ms": i * 500} for i, j in enumerate(jpegs)]
    detections = [(detect.detect_faces(j) or [None])[0] for j in jpegs]
    return frames, detections


@pytest.mark.models(det=True, emb=False)
def test_four_still_hold_frames_plus_a_turn_pass_under_pad_single_v1():
    frames, detections = still_hold_then_turn(4)
    result = liveness.check_liveness(frames, detections, verdict(), SINGLE)
    assert result.live, result.as_dict()
    duplicates = result.signals["duplicates"]
    assert duplicates.detail["largest_identical_group"] == 4 and duplicates.ok
    assert result.signals["motion"].ok
    assert result.signals["motion"].detail["pair_stride"] == 3
    # Same scan without the policy: today's duplicate rule still refuses it.
    today = liveness.check_liveness(frames, detections, verdict())
    assert not today.live and "duplicates" in today.failed


@pytest.mark.models(det=True, emb=False)
def test_single_policy_still_refuses_a_hold_longer_than_the_settle_window():
    frames, detections = still_hold_then_turn(6)
    result = liveness.check_liveness(frames, detections, verdict(), SINGLE)
    assert not result.signals["duplicates"].ok


def test_single_policy_still_refuses_a_replayed_still():
    crops = [np.zeros((112, 112), np.float32)] * 12
    frames = [{"jpeg_bytes": b"same", "ts_ms": i * 500} for i in range(12)]
    assert not liveness._duplicate_signal(frames, [None] * 12, crops, True).ok
    assert not liveness._motion_signal(crops, True).ok


def test_motion_stride_reads_500_ms_like_1400_ms():
    # A slow, steady drift: small between 500 ms neighbours, enough over 1500 ms.
    crops = [np.full((112, 112), 0.4 * i, np.float32) for i in range(12)]
    assert not liveness._motion_signal(crops).ok
    assert liveness._motion_signal(crops, True).ok


@pytest.mark.models(det=True, emb=False)
@pytest.mark.parametrize("still", [0, 4])
def test_other_policies_are_unchanged(still):
    frames, detections = still_hold_then_turn(still)
    for policy in (None, anti_spoof.POLICY_VERSION, "unknown"):
        body = liveness.check_liveness(frames, detections, verdict(), policy).as_dict()
        assert "max_identical_frames" not in body["signals"]["duplicates"]
        assert "pair_stride" not in body["signals"]["motion"]
    default = liveness.check_liveness(frames, detections, verdict()).as_dict()
    v2 = liveness.check_liveness(
        frames, detections, verdict(), anti_spoof.POLICY_VERSION
    ).as_dict()
    assert json.dumps(default, sort_keys=True) == json.dumps(v2, sort_keys=True)
