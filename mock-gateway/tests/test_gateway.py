from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app as gateway

APPS = Path(__file__).resolve().parent.parent.parent / "apps"


@pytest.fixture
def client(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "engineVersion": "0.1.0"})
        if request.url.path == "/v1/enroll":
            return httpx.Response(
                200, json={"template_id": "t-1", "quality": {"score": 0.9}}
            )
        if request.url.path == "/v1/verify":
            return httpx.Response(
                404, json={"error": {"code": "USER_NOT_FOUND", "message": "nope"}}
            )
        if request.url.path.startswith("/v1/templates/"):
            return httpx.Response(200, json={"user_id": "u", "deleted": 1})
        return httpx.Response(404, json={})

    gateway.http_client = httpx.AsyncClient(
        base_url="http://engine.test", transport=httpx.MockTransport(handler)
    )
    with TestClient(gateway.app) as c:
        yield c, seen
    gateway.http_client = httpx.AsyncClient(base_url=gateway.ENGINE_URL, timeout=60.0)


def test_health_proxied(client):
    c, seen = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "engineVersion": "0.1.0"}
    assert seen[-1].url.path == "/health"


def test_challenge_binding_headers_forwarded(client):
    c, seen = client
    c.get(
        "/v1/challenge", headers={"X-Facetech-Operation": "verify", "X-User-Id": "t01"}
    )
    assert seen[-1].headers["X-Facetech-Operation"] == "verify"
    assert seen[-1].headers["X-User-Id"] == "t01"


def test_enroll_body_status_and_headers_proxied(client):
    c, seen = client
    body = {"user_id": "u", "facescan": "AAAA"}
    r = c.post("/v1/enroll", json=body)
    assert r.status_code == 200
    assert r.json()["template_id"] == "t-1"
    assert seen[-1].url.path == "/v1/enroll"
    assert seen[-1].method == "POST"
    assert b'"facescan"' in seen[-1].read()


def test_engine_error_envelope_passes_through(client):
    c, _ = client
    r = c.post("/v1/verify", json={"user_id": "ghost", "facescan": "AAAA"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_statics_served_same_origin(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert b"<title>Guided face capture</title>" in r.content
    assert r.headers["content-type"].startswith("text/html")
    assert r.content == c.get("/integration/").content
    assert c.get("/page.js").status_code == 200, "the page's relative ./page.js"
    r2 = c.get("/console/")
    assert r2.status_code == 200
    assert b"AmFatec" in r2.content
    for asset in (
        "/console/console.js",
        "/console/capture.js",
        "/console/deps.js",
        "/shared/boot-check.js",
        "/shared/verdict.js",
        "/shared/fonts/fonts.css",
        "/shared/logo.png",
    ):
        assert c.get(asset).status_code == 200, asset

    assert c.get("/vendor/fetch.sh").status_code == 404


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("timed out")],
    ids=["refused", "timeout"],
)
def test_engine_not_answering_maps_to_502_envelope(exc):
    def dead(request):
        raise exc

    gateway.http_client = httpx.AsyncClient(
        base_url="http://engine.test", transport=httpx.MockTransport(dead)
    )
    with TestClient(gateway.app) as c:
        r = c.get("/health")
    assert r.status_code == gateway.GATEWAY_HTTP_STATUS[gateway.ENGINE_UNREACHABLE]
    assert r.status_code == 502
    assert (
        r.json()["error"]["code"] == gateway.ENGINE_UNREACHABLE == "ENGINE_UNREACHABLE"
    )


def test_gateway_owns_exactly_one_code():
    assert frozenset({"ENGINE_UNREACHABLE"}) == gateway.GATEWAY_CODES
    assert set(gateway.GATEWAY_HTTP_STATUS) == gateway.GATEWAY_CODES


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(gateway, "ENGINE_API_KEY", "k3y")
    return "k3y"


def test_engine_key_is_attached_to_upstream_calls(client, keyed):
    c, seen = client
    c.post("/v1/enroll", json={"user_id": "u", "facescan": "AAAA"})
    assert seen[-1].headers["x-engine-key"] == keyed


def test_browser_supplied_engine_key_is_not_forwarded(client, keyed):
    c, seen = client
    c.post(
        "/v1/enroll",
        json={"user_id": "u", "facescan": "AAAA"},
        headers={"X-Engine-Key": "attacker-supplied"},
    )
    assert seen[-1].headers["x-engine-key"] == keyed


def test_no_key_configured_sends_no_key_header(client, monkeypatch):
    monkeypatch.setattr(gateway, "ENGINE_API_KEY", "")
    c, seen = client
    c.get("/health")
    assert "x-engine-key" not in seen[-1].headers


def test_health_probe_also_carries_the_key_harmlessly(client, keyed):
    c, seen = client
    c.get("/health")
    assert seen[-1].headers["x-engine-key"] == keyed


def test_delete_is_proxied(client, keyed):
    c, seen = client
    r = c.delete("/v1/templates/alice")
    assert r.status_code == 200
    assert r.json() == {"user_id": "u", "deleted": 1}
    assert seen[-1].method == "DELETE"
    assert seen[-1].url.path == "/v1/templates/alice"
    assert seen[-1].headers["x-engine-key"] == keyed


def test_engine_401_passes_through_unchanged(monkeypatch):
    def unauthorized(request):
        return httpx.Response(
            401, json={"error": {"code": "UNAUTHORIZED", "message": "nope"}}
        )

    gateway.http_client = httpx.AsyncClient(
        base_url="http://engine.test", transport=httpx.MockTransport(unauthorized)
    )
    with TestClient(gateway.app) as c:
        r = c.get("/v1/info")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_optional_mounts_are_present_when_their_directories_exist():
    mounted = {r.path for r in gateway.app.routes if hasattr(r, "app")}
    assert (APPS / "integration-demo").is_dir()
    assert "/integration" in mounted
    assert ("/sdk" in mounted) is gateway.SDK_DIST.is_dir()


def test_guided_page_scripts_and_detector_are_served_same_origin():
    mounted = [r.path for r in gateway.app.routes if hasattr(r, "app")]
    assert "/shared" in mounted
    assert gateway.MEDIAPIPE_DIR == APPS / "vendor" / "mediapipe"
    assert ("/vendor/mediapipe" in mounted) is gateway.MEDIAPIPE_DIR.is_dir()
    assert mounted[-1] == "", "the catch-all '/' mount must stay last"
    with TestClient(gateway.app) as c:
        r = c.get("/shared/face-guide.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert c.get("/integration/page.js").status_code == 200
        if gateway.MEDIAPIPE_DIR.is_dir():
            r = c.get("/vendor/mediapipe/vision_bundle.mjs")
            assert r.status_code == 200
            assert "javascript" in r.headers["content-type"]
            assert (
                c.get("/vendor/mediapipe/wasm/vision_wasm_internal.wasm").status_code
                == 200
            )
            assert (
                c.get("/vendor/mediapipe/blaze_face_short_range.tflite").status_code
                == 200
            )


def test_optional_mount_is_skipped_when_the_directory_is_missing(tmp_path):
    fresh = FastAPI()
    before = len(fresh.routes)
    assert gateway.mount_if_present(fresh, "/gone", tmp_path / "nope", "gone") is False
    assert len(fresh.routes) == before

    served = tmp_path / "there"
    served.mkdir()
    (served / "index.html").write_text("<!doctype html>hi", encoding="utf-8")
    assert gateway.mount_if_present(fresh, "/there", served, "there", html=True) is True
    with TestClient(fresh) as c:
        assert c.get("/there/").status_code == 200


@pytest.mark.parametrize("path", ["/", "/page.js", "/evaluation-form.js", "/sdk/index.js", "/capture-status", "/health"])
def test_evaluation_assets_cannot_be_cached_across_releases(client, path):
    c, _ = client
    response = c.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
