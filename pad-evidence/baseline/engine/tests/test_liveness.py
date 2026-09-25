import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import challenge, liveness, store
from app.liveness import (
    MAX_IDENTICAL_FRAMES,
    MIN_USABLE_FRAMES,
    check_liveness,
)
from app.main import REFERENCE_FRAMES, app
from helpers import (
    AUTH,
    CAPTURE_MS,
    FIXTURE_PARAMS,
    FIXTURE_SETTLE_MS,
    FIXTURES,
    build_scan,
    flat_scan,
    issue_for,
    live_scan,
    make_jpeg,
    make_live_jpegs,
    replayed_scan,
    scan_to_b64,
    settle_frames,
)
from model_guard import requires_models

client = TestClient(app, headers=AUTH)


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


def detection(cx: float = 320.0, cy: float = 240.0, size: float = 160.0) -> dict:
    half = size / 2
    return {
        "bbox": [cx - half, cy - half, cx + half, cy + half],
        "landmarks_5": [
            [cx - 0.25 * size, cy - 0.15 * size],
            [cx + 0.25 * size, cy - 0.15 * size],
            [cx, cy + 0.02 * size],
            [cx - 0.18 * size, cy + 0.22 * size],
            [cx + 0.18 * size, cy + 0.22 * size],
        ],
        "det_score": 0.9,
    }


def yawed_detection(
    action: str, excursion: float, base: dict | None = None, size: float = 160.0
) -> dict:
    half = 0.25 * size
    ratio = float(np.exp(excursion if action == challenge.LOOK_LEFT else -excursion))
    offset = half * (ratio - 1.0) / (ratio + 1.0)
    det = base if base is not None else detection(size=size)
    det["landmarks_5"][2][0] += offset
    return det


HOLD_FRAMES = settle_frames(FIXTURE_SETTLE_MS)


def _fraction(i: int, count: int, hold: int = HOLD_FRAMES) -> float:
    return min(1.0, max(0.0, (i - hold) / max(1, count - hold - 2)))


def turned_detections(
    action: str = challenge.LOOK_LEFT, count: int = 12, excursion: float = 0.5
) -> list[dict]:
    return [
        yawed_detection(action, excursion * _fraction(i, count), base=base)
        for i, base in enumerate(jittered_detections(count))
    ]


def approaching_detections(
    count: int = 12,
    ratio: float = 1.5,
    start: float = 160.0,
    amplitude: float = 3.0,
    nose_gain: float = 0.0,
    hold: int = HOLD_FRAMES,
) -> list[dict]:
    rng = np.random.default_rng(11)
    detections = []
    for i in range(count):
        fraction = _fraction(i, count, hold)
        size = start * (1.0 + (ratio - 1.0) * fraction)
        det = detection(
            320.0 + rng.uniform(-amplitude, amplitude),
            240.0 + rng.uniform(-amplitude, amplitude),
            size=size,
        )
        det["landmarks_5"][2][1] += nose_gain * fraction * size
        detections.append(det)
    return detections


def verdict(action: str = challenge.MOVE_CLOSER, params: dict | None = FIXTURE_PARAMS):
    issued = issue_for(action, params)
    return challenge.check(
        issued["nonce"], action, spend=False, presented_params=issued["params"]
    )


def jittered_detections(count: int = 12, amplitude: float = 3.0) -> list[dict]:
    rng = np.random.default_rng(11)
    return [
        detection(
            320.0 + rng.uniform(-amplitude, amplitude),
            240.0 + rng.uniform(-amplitude, amplitude),
        )
        for _ in range(count)
    ]


def cadence(count: int = 12, gap: int = CAPTURE_MS) -> list[int]:
    return [1000 + i * gap for i in range(count)]


def captured_frames(name: str = "face_like.jpg") -> list[dict]:
    jpegs = make_live_jpegs((FIXTURES / name).read_bytes())
    return build_scan(jpegs=jpegs, ts_ms=cadence(len(jpegs)))["frames"]


def replayed_frames(name: str = "face_like.jpg") -> list[dict]:
    return replayed_scan(name)["frames"]


def test_replayed_scan_is_not_live():
    frames = replayed_frames()
    result = check_liveness(frames, [detection()] * 12, verdict())
    assert result.live is False
    assert result.score < liveness.LIVENESS_THRESHOLD

    assert set(result.failed) == {"duplicates", "motion", "landmarks", "challenge"}


def test_captured_scan_is_live():
    frames = captured_frames()
    result = check_liveness(frames, approaching_detections(), verdict())
    assert result.live is True
    assert result.failed == []
    assert result.score >= liveness.LIVENESS_THRESHOLD


def test_a_scan_with_no_challenge_verdict_fails_closed():
    result = check_liveness(captured_frames(), approaching_detections())
    assert result.live is False
    assert result.failed == ["challenge"]
    assert result.signals["challenge"].detail["nonce_ok"] is False


def test_duplicate_signal_counts_repeated_frames():
    frames = replayed_frames()
    signals = check_liveness(frames, [detection()] * 12).signals
    dup = signals["duplicates"]
    assert dup.ok is False
    assert dup.detail["distinct_sha256"] == 1
    assert dup.detail["largest_identical_group"] == 12
    assert dup.detail["distinct_images"] == 1


def test_duplicate_signal_sees_through_a_re_encode():
    import cv2

    source = (FIXTURES / "face_like.jpg").read_bytes()
    image = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 60])
    assert ok
    reencoded = buf.tobytes()
    assert reencoded != source

    jpegs = [source, reencoded] * 6
    frames = build_scan(jpegs=jpegs, ts_ms=cadence())["frames"]
    dup = check_liveness(frames, [detection()] * 12).signals["duplicates"]
    assert dup.detail["distinct_sha256"] == 2
    assert dup.detail["largest_identical_group"] == 12
    assert dup.ok is False


def test_duplicate_signal_tolerates_a_few_repeats():
    jpegs = make_live_jpegs((FIXTURES / "face_like.jpg").read_bytes())
    jpegs[5] = jpegs[4]
    frames = build_scan(jpegs=jpegs, ts_ms=cadence())["frames"]
    dup = check_liveness(frames, jittered_detections()).signals["duplicates"]
    assert dup.detail["largest_identical_group"] == 2 < MAX_IDENTICAL_FRAMES
    assert dup.ok is True


def test_motion_signal_is_zero_for_identical_frames():
    motion = check_liveness(replayed_frames(), [detection()] * 12).signals["motion"]
    assert motion.detail["mean_abs_diff"] == 0.0
    assert motion.ok is False


def test_motion_signal_rises_for_distinct_frames():
    motion = check_liveness(captured_frames(), jittered_detections()).signals["motion"]
    assert motion.detail["mean_abs_diff"] > liveness.MOTION_FLOOR
    assert motion.ok is True


def test_motion_signal_needs_enough_faces_to_mean_anything():
    detections = [detection()] * 2 + [None] * 10
    motion = check_liveness(captured_frames(), detections).signals["motion"]
    assert motion.ok is False
    assert motion.detail["usable_frames"] < MIN_USABLE_FRAMES
    assert "reason" in motion.detail


def test_landmark_signal_rejects_a_rigid_face():
    landmarks = check_liveness(captured_frames(), [detection()] * 12).signals[
        "landmarks"
    ]
    assert landmarks.detail["landmark_jitter"] == 0.0
    assert landmarks.detail["centre_drift"] == 0.0
    assert landmarks.ok is False


def test_landmark_signal_accepts_plausible_jitter():
    landmarks = check_liveness(captured_frames(), jittered_detections()).signals[
        "landmarks"
    ]
    assert landmarks.detail["landmark_jitter"] > liveness.LANDMARK_JITTER_FLOOR
    assert landmarks.detail["landmark_jitter"] < liveness.LANDMARK_JITTER_CEILING
    assert landmarks.ok is True


def test_f10_centred_approach_with_no_lateral_drift_passes_landmarks():
    centred = approaching_detections(ratio=1.5, amplitude=0.0)
    landmarks = check_liveness(captured_frames(), centred).signals["landmarks"]
    assert landmarks.detail["centre_drift"] == 0.0
    assert landmarks.detail["landmark_jitter"] > liveness.LANDMARK_JITTER_FLOOR
    assert landmarks.ok is True


def test_f10_rigid_face_still_fails_on_jitter():
    landmarks = check_liveness(captured_frames(), [detection()] * 12).signals[
        "landmarks"
    ]
    assert landmarks.detail["landmark_jitter"] == 0.0
    assert landmarks.ok is False


def test_f10_drift_ceiling_still_applies():
    far = [detection(40.0 + (i % 2) * 560.0, 240.0) for i in range(12)]
    landmarks = check_liveness(captured_frames(), far).signals["landmarks"]
    assert landmarks.detail["centre_drift"] > liveness.CENTRE_DRIFT_CEILING
    assert landmarks.ok is False


def test_landmark_signal_rejects_a_face_that_teleports():
    detections = [
        detection(320.0 if i % 2 else 40.0, 240.0 if i % 2 else 400.0)
        for i in range(12)
    ]
    landmarks = check_liveness(captured_frames(), detections).signals["landmarks"]
    assert landmarks.detail["landmark_jitter"] > liveness.LANDMARK_JITTER_CEILING
    assert landmarks.ok is False


def test_timing_signal_accepts_the_sdk_cadence():
    timing = check_liveness(captured_frames(), jittered_detections()).signals["timing"]
    assert timing.detail["median_gap_ms"] == CAPTURE_MS
    assert timing.detail["implausible_gaps"] == 0
    assert timing.ok is True


def test_timing_signal_rejects_frames_stamped_too_close_together():
    frames = build_scan(
        jpegs=make_live_jpegs((FIXTURES / "face_like.jpg").read_bytes()),
        ts_ms=cadence(gap=1),
    )["frames"]
    timing = check_liveness(frames, jittered_detections()).signals["timing"]
    assert timing.detail["implausible_gaps"] == 11
    assert timing.ok is False


def test_timing_signal_rejects_frames_stitched_from_different_sessions():
    ts = cadence()
    ts[6:] = [t + 3_600_000 for t in ts[6:]]
    frames = build_scan(
        jpegs=make_live_jpegs((FIXTURES / "face_like.jpg").read_bytes()), ts_ms=ts
    )["frames"]
    timing = check_liveness(frames, jittered_detections()).signals["timing"]
    assert timing.detail["max_gap_ms"] > liveness.MAX_FRAME_GAP_MS
    assert timing.ok is False


def _challenge(
    detections,
    action=challenge.MOVE_CLOSER,
    verdict_action=None,
    params=FIXTURE_PARAMS,
):
    return check_liveness(
        captured_frames(), detections, verdict(verdict_action or action, params)
    ).signals["challenge"]


def test_challenge_signal_accepts_an_approach():
    signal = _challenge(approaching_detections())
    assert signal.ok is True
    assert signal.detail["action"] == challenge.MOVE_CLOSER
    assert signal.detail["scale_toward"] > liveness.SCALE_DELTA_FLOOR
    assert signal.detail["scale_away"] < liveness.SCALE_OPPOSITE_CEILING


def test_challenge_signal_rejects_a_scan_that_stayed_put():
    signal = _challenge(jittered_detections())
    assert signal.ok is False
    assert signal.detail["scale_toward"] < liveness.SCALE_DELTA_FLOOR


def test_challenge_signal_rejects_a_face_backing_away():
    signal = _challenge(approaching_detections(ratio=1 / 1.5))
    assert signal.ok is False
    assert signal.detail["scale_away"] > liveness.SCALE_OPPOSITE_CEILING


def test_challenge_signal_measures_the_approach_relative_to_the_first_frame():
    near = approaching_detections(start=260.0)
    far = approaching_detections(start=110.0)
    assert _challenge(near).ok is True
    assert _challenge(far).ok is True
    assert (
        _challenge(near).detail["scale_baseline"]
        > _challenge(far).detail["scale_baseline"]
    )


@pytest.mark.parametrize("action", [challenge.LOOK_LEFT, challenge.LOOK_RIGHT])
def test_challenge_signal_accepts_a_turn_in_the_asked_for_direction(action):
    signal = _challenge(turned_detections(action), action)
    assert signal.ok is True
    assert signal.detail["action"] == action
    assert signal.detail["yaw_toward"] > liveness.YAW_DELTA_FLOOR
    assert signal.detail["yaw_away"] < liveness.YAW_OPPOSITE_CEILING


def test_challenge_signal_rejects_a_scan_with_no_head_turn():
    signal = _challenge(jittered_detections(), challenge.LOOK_LEFT)
    assert signal.ok is False
    assert signal.detail["yaw_toward"] < liveness.YAW_DELTA_FLOOR


@pytest.mark.parametrize("action", [challenge.LOOK_LEFT, challenge.LOOK_RIGHT])
def test_challenge_signal_rejects_a_turn_the_other_way(action):
    other = (
        challenge.LOOK_RIGHT if action == challenge.LOOK_LEFT else challenge.LOOK_LEFT
    )
    signal = _challenge(turned_detections(other), verdict_action=action)
    assert signal.ok is False
    assert signal.detail["yaw_away"] > liveness.YAW_OPPOSITE_CEILING


def test_challenge_signal_measures_the_turn_relative_to_the_first_frame():
    lopsided = turned_detections(challenge.LOOK_LEFT)
    for det in lopsided:
        det["landmarks_5"][2][0] -= 12.0
    signal = _challenge(lopsided, challenge.LOOK_LEFT)
    assert signal.ok is True
    assert signal.detail["yaw_baseline"] < 0.0, "the bias is visible..."
    assert signal.detail["yaw_toward"] > liveness.YAW_DELTA_FLOOR, "...and discounted"


def test_challenge_signal_needs_enough_poses_to_mean_anything():
    detections = turned_detections()[:2] + [None] * 10
    signal = _challenge(detections)
    assert signal.ok is False
    assert signal.detail["usable_frames"] < MIN_USABLE_FRAMES
    assert "reason" in signal.detail


def test_challenge_signal_requires_the_face_to_hold_still_first():
    early = approaching_detections(hold=0)
    signal = _challenge(early)
    assert signal.ok is False
    assert signal.detail["scale_settle_spread"] > liveness.SCALE_SETTLE_CEILING

    assert signal.detail["scale_toward"] > signal.detail["target"]


def test_challenge_signal_splits_the_scan_on_the_issued_settle_window():
    short = _challenge(
        approaching_detections(), params={"settle_ms": 900, "target": 0.25}
    )
    long_ = _challenge(
        approaching_detections(), params={"settle_ms": 2400, "target": 0.25}
    )
    assert short.detail["settle_frames"] < long_.detail["settle_frames"]
    assert short.detail["approach_frames"] > long_.detail["approach_frames"]
    assert short.detail["settle_frames"] + short.detail["approach_frames"] == 12


def test_challenge_signal_holds_the_approach_to_the_issued_target():
    track = approaching_detections(ratio=1.35)
    assert _challenge(track, params={"settle_ms": 1500, "target": 0.24}).ok is True
    assert _challenge(track, params={"settle_ms": 1500, "target": 0.44}).ok is False


def test_f02_missing_settle_window_fails_retryable():
    sparse = [None, None] + approaching_detections()[2:]
    signal = _challenge(sparse, params={"settle_ms": 900, "target": 0.25})
    assert signal.detail["settle_checked"] is False
    assert signal.detail["retryable"] is True
    assert signal.detail["reason"] == liveness.INSUFFICIENT_SETTLE_REASON
    assert signal.score == 0.0
    assert signal.ok is False


def test_f02_legacy_verdict_without_settle_ms_is_unchanged():
    legacy = challenge.ChallengeVerdict(
        ok=True,
        reason=None,
        action=challenge.MOVE_CLOSER,
        presented_action=challenge.MOVE_CLOSER,
        params={},
        nonce_prefix="legacy00",
        consumed=False,
    )
    sparse = [None, None] + approaching_detections()[2:]
    signal = check_liveness(captured_frames(), sparse, legacy).signals["challenge"]
    assert signal.detail["settle_checked"] is False
    assert "retryable" not in signal.detail
    assert signal.ok is True


def test_challenge_signal_fails_an_unverifiable_action():
    issued = challenge.issue()
    forged = challenge.ChallengeVerdict(
        ok=True,
        reason=None,
        action=challenge.BLINK_TWICE,
        presented_action=challenge.BLINK_TWICE,
        params=issued["params"],
        nonce_prefix=issued["nonce"][:8],
        consumed=True,
    )
    signal = check_liveness(captured_frames(), turned_detections(), forged).signals[
        "challenge"
    ]
    assert signal.ok is False
    assert "not verifiable" in signal.detail["reason"]


def test_challenge_signal_carries_the_nonce_verdict_when_the_nonce_is_bad():
    dead = challenge.check(
        "00" * challenge.NONCE_BYTES, challenge.LOOK_LEFT, spend=False
    )
    signal = check_liveness(captured_frames(), turned_detections(), dead).signals[
        "challenge"
    ]
    assert signal.score == 0.0
    assert signal.detail["reason"] == challenge.UNKNOWN_NONCE
    assert "yaw_toward" not in signal.detail, "nothing to measure against"
    assert "scale_toward" not in signal.detail


def test_check_liveness_rejects_mismatched_inputs():
    with pytest.raises(ValueError):
        check_liveness(captured_frames(), [detection()] * 11)


def test_overall_score_is_the_weakest_signal():
    result = check_liveness(captured_frames(), [detection()] * 12)
    voting = [s.score for s in result.signals.values() if s.votes]
    assert result.score == min(voting)


def test_advisory_signals_are_reported_but_never_vote():
    result = check_liveness(captured_frames(), approaching_detections(), verdict())
    advisory = {n for n, sig in result.signals.items() if sig.advisory}
    assert advisory == {"depth", "moire"}
    for name in advisory:
        signal = result.signals[name]
        assert signal.votes is False
        assert signal.score is None, "no measured threshold yet, so no score"
        assert signal.ok is None
        assert signal.as_dict()["advisory"] is True
    assert set(result.failed).isdisjoint(advisory), "an advisory signal fails nothing"


def test_depth_reports_more_deformation_for_a_head_than_for_a_plane():
    frames = captured_frames()
    plane = check_liveness(frames, approaching_detections(), verdict())
    head = check_liveness(frames, approaching_detections(nose_gain=0.18), verdict())
    assert (
        plane.signals["depth"].detail["depth_index"]
        < (head.signals["depth"].detail["depth_index"])
    )


def test_depth_says_so_rather_than_guessing_when_nobody_approached():
    signal = check_liveness(
        captured_frames(), jittered_detections(), verdict()
    ).signals["depth"]
    assert signal.score is None
    assert "no approach" in signal.detail["reason"]


def test_result_serialises_every_signal():
    body = check_liveness(captured_frames(), jittered_detections(), verdict()).as_dict()
    assert set(body) == {"live", "score", "threshold", "signals"}
    assert set(body["signals"]) == {
        "duplicates",
        "motion",
        "landmarks",
        "timing",
        "challenge",
        "depth",
        "moire",
    }
    assert all({"score", "ok"} <= set(s) for s in body["signals"].values())


def test_undecodable_frames_are_never_duplicates_of_each_other():
    frames = build_scan(jpegs=[b"\xff\xd8truncated"] * 12, ts_ms=cadence())["frames"]
    dup = check_liveness(frames, [None] * 12).signals["duplicates"]
    assert dup.detail["undecodable_frames"] == 12

    assert dup.detail["distinct_images"] == 1


def test_enforcement_is_on_by_default(monkeypatch):
    monkeypatch.delenv(liveness.ENFORCE_ENV, raising=False)
    assert liveness.enforcement_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " Off "])
def test_enforcement_turns_off_only_for_known_values(monkeypatch, value):
    monkeypatch.setenv(liveness.ENFORCE_ENV, value)
    assert liveness.enforcement_enabled() is False


@pytest.mark.parametrize("value", ["1", "", "true", "yes", "maybe", "0 0"])
def test_enforcement_fails_safe_on_anything_else(monkeypatch, value):
    monkeypatch.setenv(liveness.ENFORCE_ENV, value)
    assert liveness.enforcement_enabled() is True


@requires_models
def test_enroll_rejects_a_replayed_scan(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={
            "user_id": "mallory",
            "facescan": scan_to_b64(replayed_scan("face_like.jpg")),
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "LIVENESS_FAIL"
    assert not store.has_user("mallory")


@requires_models
def test_verify_rejects_a_replayed_scan(low_det_thresh):
    client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    r = client.post(
        "/v1/verify",
        json={
            "user_id": "alice",
            "facescan": scan_to_b64(replayed_scan("face_like.jpg")),
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "LIVENESS_FAIL"
    assert "score" not in r.json()


@requires_models
def test_enroll_accepts_a_captured_scan(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    assert r.status_code == 200
    assert r.json()["liveness"]["live"] is True
    assert r.json()["liveness"]["failed_signals"] == []


@requires_models
def test_enroll_rejects_a_capture_that_did_not_move_closer(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={
            "user_id": "mallory",
            "facescan": scan_to_b64(flat_scan("face_like.jpg")),
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "LIVENESS_FAIL"
    assert "challenge" in r.json()["error"]["message"]
    assert not store.has_user("mallory")


@requires_models
def test_liveness_endpoint_shows_the_face_did_not_approach(low_det_thresh):
    body = client.post(
        "/v1/liveness", json={"facescan": scan_to_b64(flat_scan("face_like.jpg"))}
    ).json()
    signal = body["signals"]["challenge"]
    assert body["live"] is False
    assert signal["nonce_ok"] is True, "the nonce was fine; the distance was not"
    assert signal["action"] in challenge.ISSUABLE_ACTIONS
    assert signal["scale_toward"] < liveness.SCALE_DELTA_FLOOR


@requires_models
def test_liveness_endpoint_measures_a_real_approach(low_det_thresh):
    body = client.post(
        "/v1/liveness", json={"facescan": scan_to_b64(live_scan("face_like.jpg"))}
    ).json()
    signal = body["signals"]["challenge"]
    assert body["live"] is True, body
    assert signal["scale_toward"] > liveness.SCALE_DELTA_FLOOR
    assert signal["scale_away"] < liveness.SCALE_OPPOSITE_CEILING


@requires_models
def test_verify_carries_the_verdict_on_success(low_det_thresh):
    client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    body = client.post(
        "/v1/verify",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    ).json()
    assert body["match"] is True
    assert body["liveness"] == {
        "live": True,
        "score": pytest.approx(body["liveness"]["score"]),
        "enforced": True,
        "failed_signals": [],
    }


@requires_models
def test_enforcement_off_lets_the_spoof_through_but_still_reports_it(
    low_det_thresh, monkeypatch
):
    monkeypatch.setenv(liveness.ENFORCE_ENV, "0")
    r = client.post(
        "/v1/enroll",
        json={
            "user_id": "mallory",
            "facescan": scan_to_b64(replayed_scan("face_like.jpg")),
        },
    )
    assert r.status_code == 200
    assert r.json()["liveness"]["live"] is False
    assert r.json()["liveness"]["enforced"] is False
    assert "duplicates" in r.json()["liveness"]["failed_signals"]


@requires_models
def test_liveness_endpoint_reports_a_replay_without_erroring(low_det_thresh):
    r = client.post(
        "/v1/liveness",
        json={"facescan": scan_to_b64(replayed_scan("face_like.jpg"))},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["live"] is False
    assert body["signals"]["duplicates"]["largest_identical_group"] == 12
    assert body["enforced"] is True


@requires_models
def test_liveness_endpoint_passes_a_captured_scan(low_det_thresh):
    r = client.post(
        "/v1/liveness", json={"facescan": scan_to_b64(live_scan("face_like.jpg"))}
    )
    assert r.status_code == 200
    assert r.json()["live"] is True
    assert 0.0 <= r.json()["score"] <= 1.0


@requires_models
def test_liveness_endpoint_no_face_422(low_det_thresh):
    r = client.post(
        "/v1/liveness", json={"facescan": scan_to_b64(live_scan("blank.jpg"))}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "NO_FACE"


def test_liveness_endpoint_needs_no_user_id():
    r = client.post("/v1/liveness", json={"facescan": scan_to_b64(build_scan())})
    assert r.status_code in (422, 503)
    assert "error" in r.json()


def test_liveness_endpoint_rejects_an_empty_body():
    r = client.post("/v1/liveness", json={})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_liveness_endpoint_requires_the_engine_key():
    bare = TestClient(app)
    r = bare.post("/v1/liveness", json={"facescan": scan_to_b64(build_scan())})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_liveness_endpoint_rejects_a_malformed_scan():
    r = client.post("/v1/liveness", json={"facescan": "!!"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"


def test_liveness_endpoint_accepts_the_msgpack_transport():
    from helpers import scan_to_msgpack

    r = client.post(
        "/v1/liveness",
        content=scan_to_msgpack(build_scan(jpegs=[make_jpeg()] * 12)),
        headers={"Content-Type": "application/msgpack"},
    )

    assert r.status_code in (422, 503)
    assert r.json()["error"]["code"] in ("NO_FACE", "MODEL_UNAVAILABLE")


@requires_models
def test_enroll_embeds_several_frames(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    assert r.status_code == 200
    assert r.json()["quality"]["frames_embedded"] == REFERENCE_FRAMES


@requires_models
def test_the_averaged_template_is_still_a_unit_vector(low_det_thresh):
    client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    embedding = store.get_templates("alice")[0]["embedding"]
    assert float(np.linalg.norm(embedding)) == pytest.approx(1.0, abs=1e-5)
