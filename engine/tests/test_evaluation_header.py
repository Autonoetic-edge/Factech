import json

from fastapi.testclient import TestClient

from app.main import app
from helpers import AUTH


def test_internal_diagnostics_are_opt_in_and_authenticated(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("FACETECH_DIAGNOSTIC_HEADER", raising=False)
    assert (
        "X-Facetech-Decision"
        not in client.post("/v1/enroll", json={}, headers=AUTH).headers
    )
    monkeypatch.setenv("FACETECH_DIAGNOSTIC_HEADER", "1")
    assert "X-Facetech-Decision" not in client.post("/v1/enroll", json={}).headers
    result = client.post("/v1/enroll", json={}, headers=AUTH)
    decision = json.loads(result.headers["X-Facetech-Decision"])
    assert decision["request_id"] == result.headers["X-Request-Id"]
    assert decision["http_status"] == result.status_code
    assert "frames" not in decision
