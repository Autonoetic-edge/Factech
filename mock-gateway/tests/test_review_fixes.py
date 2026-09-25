import httpx
import pytest
from fastapi.testclient import TestClient

import app as gateway


@pytest.fixture
def recorder():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/liveness":
            return httpx.Response(
                503,
                json={"error": {"code": "BUSY", "message": "at capacity"}},
                headers={"Retry-After": "2"},
            )
        return httpx.Response(
            404, json={"error": {"code": "USER_NOT_FOUND", "message": "x"}}
        )

    gateway.http_client = httpx.AsyncClient(
        base_url="http://engine.test", transport=httpx.MockTransport(handler)
    )
    with TestClient(gateway.app) as c:
        yield c, seen
    gateway.http_client = httpx.AsyncClient(base_url=gateway.ENGINE_URL, timeout=60.0)


def test_f23_x_user_id_is_forwarded_on_the_msgpack_transport(recorder):
    c, seen = recorder
    c.post(
        "/v1/verify",
        content=b"\x80",
        headers={"Content-Type": "application/msgpack", "X-User-Id": "alice"},
    )
    assert seen[-1].headers.get("x-user-id") == "alice"


def test_f23_absent_x_user_id_is_not_invented(recorder):
    c, seen = recorder
    c.post("/v1/verify", json={"user_id": "alice", "facescan": "AAAA"})
    assert "x-user-id" not in seen[-1].headers


def test_f21_retry_after_passes_through_the_proxy(recorder):
    c, _ = recorder
    r = c.post("/v1/liveness", json={"facescan": "AAAA"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "BUSY"
    assert r.headers.get("retry-after") == "2"


import capture_store  # noqa: E402


@pytest.mark.parametrize("value", [None, "", "   "])
def test_f06_capture_store_is_off_unless_a_path_is_set(monkeypatch, tmp_path, value):
    monkeypatch.setattr(capture_store, "DEFAULT_DB", tmp_path / "captures.db")
    monkeypatch.delenv("FACETECH_CAPTURE_LABEL", raising=False)
    if value is None:
        monkeypatch.delenv("FACETECH_CAPTURE_DB", raising=False)
    else:
        monkeypatch.setenv("FACETECH_CAPTURE_DB", value)
    assert capture_store.open_from_env() == (None, None)
    assert not (tmp_path / "captures.db").exists(), "a store was created by default"


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_f06_explicit_off_values_stay_off(monkeypatch, value):
    monkeypatch.setenv("FACETECH_CAPTURE_DB", value)
    assert capture_store.open_from_env() == (None, None)


def test_f06_a_path_opts_in(monkeypatch, tmp_path):
    db = tmp_path / "opted-in.db"
    monkeypatch.setenv("FACETECH_CAPTURE_DB", str(db))
    monkeypatch.delenv("FACETECH_CAPTURE_LABEL", raising=False)
    store, label = capture_store.open_from_env()
    try:
        assert store is not None and label is None
        assert db.exists()
    finally:
        store.close()
