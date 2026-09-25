import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app
from helpers import AUTH, build_scan, live_scan, make_noise_jpeg, scan_to_b64

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
