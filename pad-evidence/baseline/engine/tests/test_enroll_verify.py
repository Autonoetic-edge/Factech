import uuid

import pytest
from fastapi.testclient import TestClient

from app import liveness, store
from app.main import PLACEHOLDER_THRESHOLD, app
from helpers import AUTH, build_scan, live_scan, make_noise_jpeg, scan_to_b64
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


def scan_from_fixture(name: str) -> str:
    return scan_to_b64(live_scan(name))


def test_enroll_rejects_over_350kb_with_413():
    scan = build_scan(jpegs=[make_noise_jpeg(seed=i) for i in range(12)])
    r = client.post("/v1/enroll", json={"user_id": "u", "facescan": scan_to_b64(scan)})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_enroll_rejects_bad_base64_with_422():
    r = client.post("/v1/enroll", json={"user_id": "u", "facescan": "!!"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"


def test_enroll_rejects_eleven_frames_with_422():
    scan = build_scan()
    scan["frames"] = scan["frames"][:11]
    r = client.post("/v1/enroll", json={"user_id": "u", "facescan": scan_to_b64(scan)})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"


def test_verify_rejects_malformed_scan_with_422():
    r = client.post("/v1/verify", json={"user_id": "u", "facescan": "!!"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"


def test_verify_unknown_user_404():
    scan = scan_to_b64(build_scan())
    r = client.post("/v1/verify", json={"user_id": "ghost", "facescan": scan})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_enroll_empty_user_id_422():
    r = client.post(
        "/v1/enroll", json={"user_id": "", "facescan": scan_to_b64(build_scan())}
    )
    assert r.status_code == 422


@requires_models
def test_enroll_then_verify_round_trip(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_like.jpg")},
    )
    assert r.status_code == 200
    body = r.json()
    uuid.UUID(body["template_id"])
    assert 0.0 <= body["quality"]["score"] <= 1.0

    r2 = client.post(
        "/v1/verify",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_like.jpg")},
    )
    assert r2.status_code == 200
    v = r2.json()
    assert v["score"] >= 0.999
    assert v["match"] is True
    assert v["threshold"] == PLACEHOLDER_THRESHOLD


@requires_models
def test_verify_with_different_images_scores_lower(low_det_thresh):
    client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_like.jpg")},
    )

    same = client.post(
        "/v1/verify",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_like.jpg")},
    ).json()

    diff = client.post(
        "/v1/verify",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_synth.jpg")},
    ).json()

    assert -1.0 <= diff["score"] <= 1.0
    assert diff["score"] < same["score"]


@requires_models
def test_enroll_no_face_422(low_det_thresh):
    r = client.post(
        "/v1/enroll", json={"user_id": "u", "facescan": scan_from_fixture("blank.jpg")}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "NO_FACE"


@requires_models
def test_verify_no_face_after_enroll_422(low_det_thresh):
    client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_like.jpg")},
    )
    r = client.post(
        "/v1/verify",
        json={"user_id": "alice", "facescan": scan_from_fixture("blank.jpg")},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "NO_FACE"


@requires_models
def test_enroll_multi_face_422(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "u", "facescan": scan_from_fixture("two_face_like.jpg")},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MULTI_FACE"


@requires_models
def test_verify_multi_face_is_not_an_error(low_det_thresh, monkeypatch):
    monkeypatch.setenv(liveness.ENFORCE_ENV, "0")
    client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_from_fixture("face_like.jpg")},
    )
    r = client.post(
        "/v1/verify",
        json={"user_id": "alice", "facescan": scan_from_fixture("two_face_like.jpg")},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["match"], bool)
