import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import msgpack
import pytest
from fastapi.testclient import TestClient

import app as gateway
import capture_store
import export_captures


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    store = capture_store.CaptureStore(tmp_path / "captures.db", evaluation_days=7)
    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(gateway, "CAPTURE_LABEL", None)

    def engine(request):
        request_id = request.headers["X-Request-Id"]
        decision = {
            "request_id": request_id,
            "outcome": "rejected",
            "liveness": {"score": 0.23, "failed": ["challenge"]},
        }
        return httpx.Response(
            422,
            json={"error": {"code": "LIVENESS_FAIL"}},
            headers={
                "X-Request-Id": request_id,
                "X-Facetech-Decision": json.dumps(decision),
            },
        )

    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test", transport=httpx.MockTransport(engine)
        ),
    )
    with TestClient(gateway.app) as client:
        yield client, store
    store.close()


def submit(client, consent=True):
    scan = msgpack.packb(
        {
            "frames": [{"jpeg_bytes": b"\xff\xd8SYNTHETIC", "ts_ms": 100}],
            "device": {"user_agent": "test"},
            "challenge": {"action": "MOVE_CLOSER"},
        },
        use_bin_type=True,
    )
    meta = (
        {
            "consent": capture_store.CONSENT_VERSION,
            "subject": "T01",
            "label": "bona_fide",
            "case": "self",
            "lighting": "room",
        }
        if consent
        else {}
    )
    return client.post(
        "/v1/enroll",
        json={"user_id": "test", "facescan": base64.b64encode(scan).decode()},
        headers={"X-Capture-Meta": json.dumps(meta)},
    )


def test_rejected_capture_is_linked_to_diagnostics_and_frames(evaluation):
    client, store = evaluation
    response = submit(client)
    assert response.status_code == 422
    assert "X-Facetech-Decision" not in response.headers
    receipt = client.get(
        "/review/api/receipt/" + response.headers["X-Request-Id"]
    ).json()
    assert receipt["stored"] and receipt["diagnostics"]
    detail = client.get("/review/api/captures/" + str(receipt["id"]))
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["decision"]["liveness"]["score"] == 0.23
    assert detail.json()["test_context"] == {"case": "self", "lighting": "room"}
    assert detail.json()["label"] == "bona_fide"
    frame = client.get(f"/review/api/captures/{receipt['id']}/frames/0")
    assert frame.content == b"\xff\xd8SYNTHETIC"
    assert frame.headers["cache-control"] == "no-store"


def test_no_consent_no_capture(evaluation):
    client, store = evaluation
    response = submit(client, consent=False)
    assert response.status_code == 422
    assert client.get("/review/api/captures").json()["items"] == []


def test_delete_requires_explicit_action_and_removes_frames_scores(evaluation):
    client, store = evaluation
    submit(client)
    capture_id = client.get("/review/api/captures").json()["items"][0]["id"]
    path = f"/review/api/captures/{capture_id}"
    assert client.delete(path).status_code == 403
    assert client.delete(path, headers={"X-Review-Action": "delete"}).json() == {
        "deleted": 1
    }
    assert client.get(path).status_code == 404
    assert client.get(path + "/frames/0").status_code == 404
    assert store._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0


def test_retention_persists_and_expired_frame_is_not_served(evaluation):
    client, store = evaluation
    submit(client)
    store._clock = lambda: datetime.now(UTC) + timedelta(days=8)
    assert client.get("/review/api/captures/1/frames/0").status_code == 404
    with pytest.raises(SystemExit, match="Export is disabled"):
        export_captures.main(
            [
                "--db",
                str(store.path),
                "export",
                "--dest",
                str(store.path.parent / "export"),
            ]
        )
    assert not (store.path.parent / "export").exists()
    reopened = capture_store.CaptureStore(store.path)
    assert reopened.retention_days == 7 and reopened.evaluation_only
    reopened.close()


def test_wrong_request_decision_is_not_attached():
    assert capture_store.clean_decision('{"request_id":"other"}', "mine") is None


def test_review_disabled_for_ordinary_capture_store(tmp_path, monkeypatch):
    store = capture_store.CaptureStore(tmp_path / "ordinary.db")
    monkeypatch.setattr(gateway, "CAPTURES", store)
    with TestClient(gateway.app) as client:
        assert client.get("/review").status_code == 404
    store.close()
