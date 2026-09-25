"""Soft colour glow, FLASH_CHECK (docs/LIVENESS_UPGRADE_PLAN.md Phase 4, engine part).

Frames are synthetic and non-biometric: a flat skin-coloured patch lit by ambient light
plus the screen's glow, with fixed synthetic keypoints. Too bright or unmeasurable is
`inconclusive`, never `fail`; `log` never refuses; flag off never runs the check.
"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import anti_spoof as pad
from app import challenge, flash, liveness, main, store, trace
from helpers import AUTH, build_scan, scan_to_b64
from test_secure_pad import vector

GLOW = challenge.POLICY_SINGLE_TURN_GLOW_V1
WIDTH, HEIGHT = 320, 240
LANDMARKS = [[120, 100], [200, 100], [160, 135], [130, 170], [190, 170]]
FACE = {"bbox": [80, 20, 240, 220], "det_score": 0.99, "landmarks_5": LANDMARKS}
ALBEDO = np.array([0.85, 0.70, 0.60])
CADENCE = 500

# Frame 0 (t=0) is the pre-glow baseline; glow frames 1-6; frame 7 back to neutral.
SCHEDULE = {
    "version": flash.VERSION,
    "neutral": [255, 255, 255],
    "fade_ms": 400,
    "steps": [
        {"colour": "blue", "rgb": list(flash.PALETTE["blue"]), "start_ms": 400},
        {"colour": "amber", "rgb": list(flash.PALETTE["amber"]), "start_ms": 1000},
        {"colour": "green", "rgb": list(flash.PALETTE["green"]), "start_ms": 1550},
        {"colour": "magenta", "rgb": list(flash.PALETTE["magenta"]), "start_ms": 2150},
    ],
    "end_ms": 2750,
}
OTHER = {
    **SCHEDULE,
    "steps": [
        {"colour": "magenta", "rgb": list(flash.PALETTE["magenta"]), "start_ms": 450},
        {"colour": "green", "rgb": list(flash.PALETTE["green"]), "start_ms": 1100},
        {"colour": "blue", "rgb": list(flash.PALETTE["blue"]), "start_ms": 1650},
        {"colour": "amber", "rgb": list(flash.PALETTE["amber"]), "start_ms": 2200},
    ],
}


def render(t_ms, schedule=SCHEDULE, *, ambient=0.55, glow=0.25, delay_ms=0, seed=0):
    """BGR frame: grey background, a flat patch lit by ambient + glow, pixel noise."""
    light = ambient + glow * flash.displayed(schedule, t_ms - delay_ms) / 255.0
    rgb = np.full((HEIGHT, WIDTH, 3), 60.0)
    rgb[20:220, 80:240] = ALBEDO * light * 255.0
    rng = np.random.default_rng(seed)
    rgb = np.clip(rgb + rng.normal(0, 1.5, rgb.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def frames_of(count=12, **kw):
    images = [render(i * CADENCE, seed=i, **kw) for i in range(count)]
    frames = [{"ts_ms": 10_000 + i * CADENCE, "jpeg_bytes": i} for i in range(count)]
    return frames, [dict(FACE) for _ in range(count)], images.__getitem__


def run(schedule=SCHEDULE, **kw):
    frames, dets, decode = frames_of(**kw)
    return flash.check(frames, dets, schedule, decode)


@pytest.mark.parametrize(
    "value, mode",
    [
        (None, "off"),
        ("", "off"),
        ("off", "off"),
        ("LOG", "log"),
        (" on ", "on"),
        ("yes", "off"),
        ("true", "off"),
    ],
)
def test_mode_defaults_to_off(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv(flash.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(flash.MODE_ENV, value)
    assert flash.mode() == mode


def test_drawn_schedules_are_soft_and_slow():
    for _ in range(300):
        s = flash.draw_schedule()
        steps = s["steps"]
        assert flash.COLOURS_MIN <= len(steps) <= flash.COLOURS_MAX
        assert s["fade_ms"] == 400 and s["neutral"] == [255, 255, 255]
        assert 300 <= steps[0]["start_ms"] <= 500
        starts = [x["start_ms"] for x in steps] + [s["end_ms"]]
        assert all(b - a >= 500 for a, b in zip(starts, starts[1:]))  # <= 2 per second
        assert s["end_ms"] <= 500 + 4 * 700
        for a, b in zip(steps, steps[1:]):
            assert a["colour"] != b["colour"]
        for x in steps:
            assert tuple(x["rgb"]) == flash.PALETTE[x["colour"]]
            r, g, b = x["rgb"]
            assert not (r > 200 and g < 100 and b < 100)  # no saturated red


def test_displayed_colour_follows_the_fades():
    blue = np.array(flash.PALETTE["blue"], float)
    white = np.array([255, 255, 255], float)
    assert np.allclose(flash.displayed(SCHEDULE, 0), white)
    assert np.allclose(flash.displayed(SCHEDULE, 600), white + (blue - white) * 0.5)
    assert np.allclose(flash.displayed(SCHEDULE, 900), blue)
    assert np.allclose(flash.displayed(SCHEDULE, 3200), white)


def test_with_colour_passes():
    record = run()
    assert record["outcome"] == "pass", record
    assert record["score"] >= flash.FLASH_MIN and record["lag"] == 0
    assert record["baseline_frames"] == 1 and record["glow_frames"] == 7
    assert record["gain"] > 0


def test_camera_one_frame_late_still_passes():
    record = run(delay_ms=CADENCE)
    assert record["outcome"] == "pass", record
    assert record["lag"] == 1


def test_two_frames_late_fails_on_lag():
    record = run(delay_ms=2 * CADENCE)
    assert record["outcome"] == "fail" and record["reason"] == "lag", record


def test_without_colour_fails():
    record = run(glow=0.0)
    assert record["outcome"] == "fail", record
    assert record["reason"] == "low_correlation"
    assert record["score"] < flash.FLASH_MIN


def test_a_replay_lit_by_another_challenge_fails():
    frames, dets, _ = frames_of()
    images = [render(i * CADENCE, OTHER, seed=i) for i in range(12)]
    record = flash.check(frames, dets, SCHEDULE, images.__getitem__)
    assert record["outcome"] == "fail", record


@pytest.mark.parametrize(
    "ambient, glow",
    [(1.12, 0.02), (1.6, 0.25)],  # face luma > 200; face clipped
)
def test_too_much_ambient_light_is_inconclusive_not_a_failure(ambient, glow):
    record = run(ambient=ambient, glow=glow)
    assert record["outcome"] == "inconclusive", record
    assert record["reason"] == "too_bright"


def test_unmeasurable_is_inconclusive():
    frames, dets, decode = frames_of()
    assert flash.check(frames, dets, None, decode)["reason"] == "no_glow_issued"
    no_face = [None] + dets[1:]
    assert flash.check(frames, no_face, SCHEDULE, decode)["reason"] == "no_baseline"
    few = dets[:3] + [None] * 9
    assert flash.check(frames, few, SCHEDULE, decode)["reason"] == "too_few_frames"
    tiny = [
        {**d, "landmarks_5": [[p[0] / 10, p[1] / 10] for p in LANDMARKS]} for d in dets
    ]
    assert flash.check(frames, tiny, SCHEDULE, decode)["outcome"] == "inconclusive"
    assert (
        flash.check(frames, dets, SCHEDULE, lambda b: None)["outcome"] == "inconclusive"
    )


# --- challenge binding -------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [None, "head-sequence", challenge.POLICY_SINGLE_TURN_V1]
)
def test_no_glow_outside_the_glow_policy(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(challenge.POLICY_ENV, raising=False)
    else:
        monkeypatch.setenv(challenge.POLICY_ENV, value)
    issued = challenge.issue("verify", "test")
    assert set(issued) == {"nonce", "action", "params", "issued_ms", "expires_ms"}
    assert "glow" not in challenge._issued[issued["nonce"]]


def test_glow_policy_issues_a_single_turn_with_a_bound_schedule(monkeypatch):
    monkeypatch.setenv(challenge.POLICY_ENV, GLOW)
    assert challenge.selected_policy() == GLOW
    assert challenge.issuable_actions() == challenge.SINGLE_TURN_ACTIONS
    issued = challenge.issue("verify", "test")
    assert issued["action"] in challenge.SINGLE_TURN_ACTIONS
    assert 1200 <= issued["params"]["settle_ms"] <= 2000
    assert issued["glow"] == challenge._issued[issued["nonce"]]["glow"]
    scan = {
        "challenge": {
            **issued,
            "glow": {"steps": []},  # a tampered echo is ignored
        },
        "frames": [{"ts_ms": 0}],
    }
    verdict = challenge.consume_from_scan(scan, ("verify", "test"))
    assert verdict.ok and verdict.glow == issued["glow"]


# --- endpoint ----------------------------------------------------------------------


def jpeg(image):
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return buf.tobytes()


def glow_scan(**render_kw):
    issued = challenge.issue("verify", "test")
    record = challenge._issued[issued["nonce"]]
    record["issued_ms"] -= 7000
    schedule = record["glow"]
    jpegs = [
        jpeg(render(i * CADENCE, schedule, seed=i, **render_kw)) for i in range(12)
    ]
    scan = build_scan(jpegs, ts_ms=[5000 + i * CADENCE for i in range(12)])
    scan["challenge"] = {k: v for k, v in issued.items() if k != "glow"}
    return scan


@pytest.fixture
def glow_engine(monkeypatch):
    store.reset()
    store.enroll("test", vector())
    monkeypatch.setenv(challenge.POLICY_ENV, GLOW)
    monkeypatch.setattr(
        main, "_scan_detections", lambda s: [[dict(FACE)] for _ in range(12)]
    )
    monkeypatch.setattr(
        pad,
        "evaluate",
        lambda *a: (
            {"outcome": "live", "usable": 12, "minimum_detection_score": 0.99},
            vector(),
        ),
    )
    good = liveness.LivenessResult(
        0.8, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *args: good)
    lines = []
    monkeypatch.setattr(
        trace.Trace, "emit", lambda self, status: lines.append(self.line(status))
    )
    return lines


def post(client, scan):
    return client.post(
        "/v1/verify", json={"user_id": "test", "facescan": scan_to_b64(scan)}
    )


def test_flag_off_never_runs_the_check(monkeypatch, glow_engine):
    monkeypatch.delenv(flash.MODE_ENV, raising=False)
    monkeypatch.setattr(flash, "check", lambda *a: pytest.fail("ran while off"))
    with TestClient(main.app, headers=AUTH) as client:
        response = post(client, glow_scan(glow=0.0))
    assert response.status_code == 200 and response.json()["match"] is True
    assert "flash" not in glow_engine[-1]
    assert "flash" not in glow_engine[-1]["timing_ms"]


def test_log_mode_writes_the_trace_and_never_refuses(monkeypatch, glow_engine):
    monkeypatch.setenv(flash.MODE_ENV, "log")
    with TestClient(main.app, headers=AUTH) as client:
        live = post(client, glow_scan())
        replay = post(client, glow_scan(glow=0.0))
    assert live.status_code == replay.status_code == 200
    assert replay.json()["match"] is True
    first, second = glow_engine[-2]["flash"], glow_engine[-1]["flash"]
    assert first["mode"] == "log" and first["enforced"] is False
    assert first["outcome"] == "pass", first  # survives JPEG
    assert second["outcome"] == "fail"
    assert "flash" in glow_engine[-1]["timing_ms"]


def test_on_mode_refuses_only_a_fail(monkeypatch, glow_engine):
    monkeypatch.setenv(flash.MODE_ENV, "on")
    with TestClient(main.app, headers=AUTH) as client:
        live = post(client, glow_scan())
        replay = post(client, glow_scan(glow=0.0))
        sunny = post(client, glow_scan(ambient=1.12, glow=0.02))
    assert live.status_code == 200 and live.json()["match"] is True
    assert replay.status_code == 422
    assert replay.json()["error"]["code"] == "LIVENESS_FAIL"
    assert "pad_flash" in replay.json()["error"]["message"]
    assert glow_engine[-2]["flash"]["enforced"] is True
    assert sunny.status_code == 200, sunny.text
    assert glow_engine[-1]["flash"]["outcome"] == "inconclusive"


def test_an_error_in_the_check_is_logged_not_decided(monkeypatch, glow_engine):
    monkeypatch.setenv(flash.MODE_ENV, "on")
    monkeypatch.setattr(flash, "check", lambda *a: 1 / 0)
    with TestClient(main.app, headers=AUTH) as client:
        response = post(client, glow_scan())
    assert response.status_code == 200
    assert glow_engine[-1]["flash"]["outcome"] == "error"
