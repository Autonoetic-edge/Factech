import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import errors, store
from app.main import app
from helpers import AUTH, live_scan, scan_to_b64
from model_guard import requires_models

client = TestClient(app, headers=AUTH)


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


def scan_from_fixture(name: str) -> str:
    return scan_to_b64(live_scan(name))


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


def _fake_enroll(user_id: str, n: int = 1) -> None:
    for i in range(n):
        store.enroll(user_id, np.full(512, i + 1, dtype=np.float32))


def test_delete_removes_the_user_and_reports_the_count():
    _fake_enroll("alice", 3)
    r = client.delete("/v1/templates/alice")
    assert r.status_code == 200
    assert r.json() == {"user_id": "alice", "deleted": 3}
    assert not store.has_user("alice")
    assert store.get_templates("alice") == []


def test_delete_unknown_user_is_user_not_found():
    r = client.delete("/v1/templates/ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == errors.USER_NOT_FOUND


def test_second_delete_is_404_not_200():
    _fake_enroll("alice")
    assert client.delete("/v1/templates/alice").status_code == 200
    assert client.delete("/v1/templates/alice").status_code == 404


def test_delete_touches_only_the_named_user():
    _fake_enroll("alice", 2)
    _fake_enroll("bob", 2)
    client.delete("/v1/templates/alice")
    assert not store.has_user("alice")
    assert len(store.get_templates("bob")) == 2


def test_delete_requires_the_key():
    _fake_enroll("alice")
    bare = TestClient(app)
    assert bare.delete("/v1/templates/alice").status_code == 401
    assert bare.delete("/v1/templates/ghost").status_code == 401
    assert store.has_user("alice"), "unauthenticated request deleted a template"


@pytest.mark.parametrize("user_id", ["a b", "alice@example.com", "ünïcode", "a" * 200])
def test_delete_handles_awkward_user_ids(user_id):
    _fake_enroll(user_id)
    r = client.delete(f"/v1/templates/{user_id}")
    assert r.status_code == 200
    assert r.json()["user_id"] == user_id
    assert not store.has_user(user_id)


@requires_models
def test_enroll_verify_revoke_verify_round_trip(low_det_thresh):
    def body() -> dict:
        return {"user_id": "carol", "facescan": scan_from_fixture("face_like.jpg")}

    assert client.post("/v1/enroll", json=body()).status_code == 200
    assert client.post("/v1/verify", json=body()).status_code == 200

    revoked = client.delete("/v1/templates/carol")
    assert revoked.status_code == 200
    assert revoked.json()["deleted"] == 1

    after = client.post("/v1/verify", json=body())
    assert after.status_code == 404
    assert after.json()["error"]["code"] == errors.USER_NOT_FOUND


def test_reissue_after_revocation_works():
    _fake_enroll("alice")
    client.delete("/v1/templates/alice")
    _fake_enroll("alice")
    assert store.has_user("alice")
    assert client.delete("/v1/templates/alice").status_code == 200
