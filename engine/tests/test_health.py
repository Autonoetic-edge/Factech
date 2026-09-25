from fastapi.testclient import TestClient

from app.main import app
from helpers import AUTH

client = TestClient(app, headers=AUTH)


def test_health():
    from app.main import warm_models

    warm_models()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_info_contract_shape():
    r = client.get("/v1/info")
    assert r.status_code == 200
    body = r.json()
    assert "modelVersion" in body
    assert "thresholds" in body
    assert 1 in body["faceScanVersions"]
