import os

import httpx

ENGINE_URL = os.environ.get("ENGINE_URL", "http://engine:8000")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8080")

ENGINE_KEY_HEADERS = {"X-Engine-Key": os.environ.get("ENGINE_API_KEY", "")}


def test_engine_health_direct():
    r = httpx.get(f"{ENGINE_URL}/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_engine_info():
    r = httpx.get(f"{ENGINE_URL}/v1/info", headers=ENGINE_KEY_HEADERS, timeout=10)
    assert r.status_code == 200
    assert 1 in r.json()["faceScanVersions"]


def test_engine_rejects_unauthenticated_v1_calls():
    r = httpx.get(f"{ENGINE_URL}/v1/info", timeout=10)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
