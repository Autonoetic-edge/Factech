"""Enrolment polish (docs/LIVENESS_UPGRADE_PLAN.md Phase 5).

Same capture as verification. Under a single-turn policy the template comes from one
enrolment photo (the sharpest of the most frontal settle frames). The stricter bar (nose
conclusive; glow conclusive or poor light) refuses only when that check's flag is `on`;
`log` only records what enrolment would have decided; all flags off = today.
Frames are synthetic and non-biometric (the flat patch of test_flash_glow).
"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import anti_spoof as pad
from app import challenge, enrolment, flash, geometry, liveness, main, store, trace
from helpers import AUTH, build_scan, scan_to_b64
from test_flash_glow import CADENCE, FACE, GLOW, jpeg, render
from test_secure_pad import vector

# --- the bar -----------------------------------------------------------------------


def modes(monkeypatch, geometry_mode=None, flash_mode=None):
    for env, value in (
        (geometry.MODE_ENV, geometry_mode),
        (flash.MODE_ENV, flash_mode),
    ):
        if value is None:
            monkeypatch.delenv(env, raising=False)
        else:
            monkeypatch.setenv(env, value)


SCORED = {"outcome": "scored", "score": 0.09}
UNSURE = {"outcome": "inconclusive", "reason": "turned_frames"}


def glow(outcome, reason=None):
    return {"outcome": outcome, "reason": reason}


def test_all_flags_off_the_bar_does_not_exist(monkeypatch):
    modes(monkeypatch)
    assert enrolment.bar(None, None) is None
    modes(monkeypatch, "bogus", "bogus")
    assert enrolment.bar(UNSURE, glow("fail")) is None


def test_nose_must_be_conclusive_and_only_on_refuses(monkeypatch):
    modes(monkeypatch, "log")
    assert enrolment.bar(SCORED, None)["would_refuse"] == []
    logged = enrolment.bar(UNSURE, None)
    assert logged["would_refuse"] == [enrolment.GEOMETRY] and logged["refuse"] == []
    assert "flash" not in logged  # FLASH_CHECK off: the glow part is not judged
    modes(monkeypatch, "on")
    assert enrolment.bar(UNSURE, None)["refuse"] == [enrolment.GEOMETRY]
    assert enrolment.bar({"outcome": "error"}, None)["refuse"] == [enrolment.GEOMETRY]
    assert enrolment.bar(SCORED, None)["refuse"] == []


@pytest.mark.parametrize(
    "record, ok",
    [
        (glow("pass"), True),
        (glow("inconclusive", "too_bright"), True),  # poor light: allowed
        (glow("inconclusive", "too_few_frames"), False),  # the shortcut: not allowed
        (glow("inconclusive", "no_glow_issued"), False),
        (glow("fail", "lag"), False),
        ({"outcome": "error"}, False),
        (None, False),
    ],
)
def test_glow_must_be_conclusive_or_poor_light(monkeypatch, record, ok):
    modes(monkeypatch, None, "on")
    decision = enrolment.bar(None, record)
    assert decision["flash"]["ok"] is ok
    assert decision["refuse"] == ([] if ok else [enrolment.FLASH])
    assert "geometry" not in decision
    modes(monkeypatch, None, "log")
    assert enrolment.bar(None, record)["refuse"] == []


def test_each_part_is_enforced_by_its_own_flag(monkeypatch):
    modes(monkeypatch, "on", "log")
    decision = enrolment.bar(UNSURE, glow("inconclusive", "too_few_frames"))
    assert decision["would_refuse"] == [enrolment.GEOMETRY, enrolment.FLASH]
    assert decision["refuse"] == [enrolment.GEOMETRY]
    modes(monkeypatch, "log", "on")
    decision = enrolment.bar(UNSURE, glow("inconclusive", "too_few_frames"))
    assert decision["refuse"] == [enrolment.FLASH]


# --- the photo ---------------------------------------------------------------------


def yawed(amount):
    """FACE with the nose moved sideways: a non-zero yaw proxy."""
    det = dict(FACE)
    marks = [list(p) for p in FACE["landmarks_5"]]
    marks[2][0] += amount
    det["landmarks_5"] = marks
    return det


def photo_scan(blur=(9, 5, 0, 0, 0, 0), yaw=(0, 0, 0, 30, 0, 0)):
    """6 frames at 500 ms; settle 1500 ms = frames 0-3. `blur` kernel per frame (0 = none)."""
    images, dets = [], []
    for i in range(6):
        image = render(i * CADENCE, seed=i)
        image[20:220, 80:240] = np.where(
            (np.indices((200, 160)).sum(axis=0) // 4 % 2)[..., None] == 0,
            image[20:220, 80:240],
            image[20:220, 80:240] // 2,
        )  # texture, so blur is measurable
        if blur[i]:
            image = cv2.GaussianBlur(image, (blur[i] | 1, blur[i] | 1), 0)
        images.append(image)
        dets.append(yawed(yaw[i]))
    frames = [{"ts_ms": 10_000 + i * CADENCE, "jpeg_bytes": i} for i in range(6)]
    return frames, dets, images.__getitem__


def test_photo_is_the_sharpest_of_the_most_frontal_settle_frames():
    frames, dets, decode = photo_scan()
    chosen = enrolment.photo(frames, dets, 1500, decode)
    # frames 0 and 1 are blurred, 3 is turned (not among the 3 most frontal), 4-5 after settle
    assert chosen["index"] == 2, chosen
    assert chosen["candidates"] == 3 and chosen["yaw"] == 0.0
    frames, dets, decode = photo_scan(blur=(0, 5, 9, 0, 0, 0))
    assert enrolment.photo(frames, dets, 1500, decode)["index"] == 0


def test_no_usable_settle_frame_gives_no_photo():
    frames, dets, _ = photo_scan()
    chosen = enrolment.photo(frames, dets, 1500, lambda _: None)
    assert chosen["index"] is None and chosen["reason"] == "no_settle_frame"
    assert enrolment.photo(frames, [None] * 6, 1500, lambda _: None)["index"] is None


@pytest.mark.parametrize(
    "policy, wanted",
    [
        (None, False),
        ("head-sequence", False),
        ("bogus", False),
        (challenge.POLICY_SINGLE_TURN_V1, True),
        (GLOW, True),
    ],
)
def test_photo_only_under_a_single_turn_policy(monkeypatch, policy, wanted):
    if policy is None:
        monkeypatch.delenv(challenge.POLICY_ENV, raising=False)
    else:
        monkeypatch.setenv(challenge.POLICY_ENV, policy)
    assert enrolment.photo_wanted() is wanted


# --- endpoint ----------------------------------------------------------------------

PHOTO = np.eye(512, dtype=np.float32)[7]


def enrol_scan(user="newbie", **render_kw):
    issued = challenge.issue("enroll", user)
    record = challenge._issued[issued["nonce"]]
    record["issued_ms"] -= 7000
    schedule = record.get("glow")
    jpegs = [
        jpeg(
            render(i * CADENCE, schedule, seed=i, **render_kw)
            if schedule
            else render(i * CADENCE, seed=i)
        )
        for i in range(12)
    ]
    scan = build_scan(jpegs, ts_ms=[5000 + i * CADENCE for i in range(12)])
    scan["challenge"] = {k: v for k, v in issued.items() if k != "glow"}
    return scan


@pytest.fixture
def engine(monkeypatch):
    store.reset()
    store.enroll("test", vector())
    modes(monkeypatch)
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
    monkeypatch.setattr(
        main, "_embed_selected", lambda selected: (PHOTO, len(selected))
    )
    lines = []
    monkeypatch.setattr(
        trace.Trace, "emit", lambda self, status: lines.append(self.line(status))
    )
    return lines


def enrol(client, scan, user="newbie"):
    return client.post(
        "/v1/enroll", json={"user_id": user, "facescan": scan_to_b64(scan)}
    )


def saved(user="newbie"):
    return np.asarray(store.get_templates(user)[-1]["embedding"])


def test_all_flags_off_enrolment_is_today(monkeypatch, engine):
    monkeypatch.delenv(challenge.POLICY_ENV, raising=False)
    monkeypatch.setattr(enrolment, "photo", lambda *a: pytest.fail("photo while off"))
    with TestClient(main.app, headers=AUTH) as client:
        response = enrol(client, enrol_scan())
    assert response.status_code == 200, response.text
    assert np.allclose(saved(), vector())  # the mean of the PAD frames, as today
    assert "enrolment" not in engine[-1] and "photo" not in engine[-1]["timing_ms"]


def test_single_turn_enrolment_saves_the_photo(monkeypatch, engine):
    monkeypatch.setenv(challenge.POLICY_ENV, challenge.POLICY_SINGLE_TURN_V1)
    with TestClient(main.app, headers=AUTH) as client:
        response = enrol(client, enrol_scan())
    assert response.status_code == 200, response.text
    assert np.allclose(saved(), PHOTO)
    record = engine[-1]["enrolment"]
    assert record["photo"]["index"] in range(4)  # a settle frame
    assert "bar" not in record  # no check flag set
    assert "photo" in engine[-1]["timing_ms"]


def test_no_photo_falls_back_to_the_mean(monkeypatch, engine):
    monkeypatch.setenv(challenge.POLICY_ENV, challenge.POLICY_SINGLE_TURN_V1)
    monkeypatch.setattr(main, "_embed_selected", lambda selected: (None, 0))
    with TestClient(main.app, headers=AUTH) as client:
        response = enrol(client, enrol_scan())
    assert response.status_code == 200
    assert np.allclose(saved(), vector())
    assert engine[-1]["enrolment"]["photo"]["reason"] == "alignment"


def test_log_mode_records_what_enrolment_would_decide_and_never_refuses(
    monkeypatch, engine
):
    monkeypatch.setenv(challenge.POLICY_ENV, GLOW)
    modes(monkeypatch, "log", "log")
    monkeypatch.setattr(geometry, "nose_residual", lambda *a: dict(UNSURE))
    with TestClient(main.app, headers=AUTH) as client:
        response = enrol(
            client, enrol_scan(glow=0.0)
        )  # a replay: glow fails, nose unsure
    assert response.status_code == 200, response.text
    bar = engine[-1]["enrolment"]["bar"]
    assert bar["would_refuse"] == [enrolment.GEOMETRY, enrolment.FLASH]
    assert bar["refuse"] == []


def test_on_mode_refuses_an_unsure_nose_at_enrolment_only(monkeypatch, engine):
    monkeypatch.setenv(challenge.POLICY_ENV, challenge.POLICY_SINGLE_TURN_V1)
    modes(monkeypatch, "on")
    monkeypatch.setattr(geometry, "nose_residual", lambda *a: dict(UNSURE))
    with TestClient(main.app, headers=AUTH) as client:
        refused = enrol(client, enrol_scan())
        issued = challenge.issue("verify", "test")
        challenge._issued[issued["nonce"]]["issued_ms"] -= 7000
        scan = build_scan(
            [jpeg(render(i * CADENCE, seed=i)) for i in range(12)],
            ts_ms=[5000 + i * CADENCE for i in range(12)],
        )
        scan["challenge"] = issued
        verified = client.post(
            "/v1/verify", json={"user_id": "test", "facescan": scan_to_b64(scan)}
        )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "LIVENESS_FAIL"
    assert (
        refused.json()["error"]["message"]
        == "liveness check failed: pad_enrol_geometry"
    )
    assert not store.has_user("newbie")
    assert (
        verified.status_code == 200 and verified.json()["match"] is True
    )  # verification keeps the shortcut
    assert "enrolment" not in engine[-1]


def test_on_mode_glow_conclusive_or_poor_light(monkeypatch, engine):
    monkeypatch.setenv(challenge.POLICY_ENV, GLOW)
    modes(monkeypatch, "on", "on")
    monkeypatch.setattr(geometry, "nose_residual", lambda *a: dict(SCORED))
    with TestClient(main.app, headers=AUTH) as client:
        live = enrol(client, enrol_scan("a"), "a")
        sunny = enrol(client, enrol_scan("b", ambient=1.12, glow=0.02), "b")
        monkeypatch.setattr(
            flash, "check", lambda *a: glow("inconclusive", "too_few_frames")
        )
        unsure = enrol(client, enrol_scan("c"), "c")
    assert live.status_code == 200, live.text
    assert sunny.status_code == 200, sunny.text
    assert engine[-2]["enrolment"]["bar"]["flash"]["reason"] == "too_bright"
    assert unsure.status_code == 422
    assert unsure.json()["error"]["message"] == "liveness check failed: pad_enrol_flash"
    assert not store.has_user("c")
