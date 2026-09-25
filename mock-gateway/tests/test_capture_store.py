import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

import app as gateway
import capture_store

SCAN = b"\x82\xa7version\x01\xa6frames\x90not-really-msgpack-but-bytes-are-bytes"
MSGPACK = {"Content-Type": "application/msgpack"}

CONSENTED = {
    capture_store.META_HEADER: json.dumps({"consent": capture_store.CONSENT_VERSION})
}


@pytest.fixture
def store(tmp_path):
    s = capture_store.CaptureStore(tmp_path / "captures.db")
    yield s
    s.close()


@pytest.fixture
def recording_client(monkeypatch, store):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/enroll":
            return httpx.Response(200, json={"template_id": "t-1"})
        if request.url.path == "/v1/challenge":
            return httpx.Response(200, json={"nonce": "ab" * 16})
        return httpx.Response(404, json={})

    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(gateway, "CAPTURE_LABEL", "bona_fide")
    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test", transport=httpx.MockTransport(handler)
        ),
    )
    with TestClient(gateway.app, headers=CONSENTED) as c:
        yield c, store


def _rows(store):
    return store._conn.execute(
        "SELECT endpoint, scan, status_code, response, label FROM captures"
    ).fetchall()


def test_scan_is_stored_byte_for_byte(recording_client):
    c, store = recording_client
    r = c.post(
        "/v1/enroll", content=SCAN, headers={"Content-Type": "application/msgpack"}
    )
    assert r.status_code == 200

    (row,) = _rows(store)
    endpoint, scan, status, response, label = row
    assert endpoint == "/v1/enroll"

    assert bytes(scan) == SCAN
    assert status == 200
    assert '"template_id"' in response
    assert label == "bona_fide"


def test_engine_verdict_is_kept_with_the_scan(recording_client):
    c, store = recording_client
    c.post("/v1/enroll", content=SCAN, headers={"Content-Type": "application/msgpack"})
    (row,) = _rows(store)
    assert row[3] == '{"template_id":"t-1"}'


def test_non_scan_endpoints_are_not_recorded(recording_client):
    c, store = recording_client
    c.get("/health")
    c.get("/v1/challenge")
    assert _rows(store) == []


def test_scan_is_banked_even_when_the_engine_is_unreachable(monkeypatch, store):
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("engine down")

    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(gateway, "CAPTURE_LABEL", None)
    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test", transport=httpx.MockTransport(dead)
        ),
    )
    with TestClient(gateway.app, headers=CONSENTED) as c:
        r = c.post("/v1/liveness", content=SCAN, headers=MSGPACK)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == gateway.ENGINE_UNREACHABLE

    (row,) = _rows(store)
    assert bytes(row[1]) == SCAN
    assert row[2] == 502
    assert row[4] is None


def test_a_broken_store_does_not_fail_the_users_scan(recording_client):
    c, store = recording_client
    store._conn.execute("DROP TABLE captures")
    store._conn.commit()

    r = c.post("/v1/enroll", content=SCAN)
    assert r.status_code == 200
    assert r.json() == {"template_id": "t-1"}


def test_empty_body_is_not_stored(store):
    assert (
        store.record(
            endpoint="/v1/enroll",
            content_type="application/msgpack",
            body=b"",
            status_code=400,
            response=b"{}",
        )
        is None
    )


def test_counts_group_by_label_and_track_pending_export(store):
    for label in ("print", "print", None):
        store.record(
            endpoint="/v1/liveness",
            content_type="application/msgpack",
            body=SCAN + (label or "x").encode(),
            status_code=200,
            response=b"{}",
            label=label,
        )
    counts = store.counts()
    assert counts["total"] == 3
    assert counts["pending_export"] == 3
    assert counts["by_label"] == {"(unlabelled)": 1, "print": 2}


def test_capture_status_reports_counts_but_never_scans(recording_client):
    c, store = recording_client
    c.post("/v1/enroll", content=SCAN, headers=MSGPACK)
    body = c.get("/capture-status").json()
    assert body["enabled"] is True
    assert body["by_label"] == {"bona_fide": 1}
    assert "scan" not in str(body)


def test_capture_status_when_disabled(monkeypatch):
    monkeypatch.setattr(gateway, "CAPTURES", None)
    with TestClient(gateway.app) as c:
        assert c.get("/capture-status").json() == {"enabled": False}


def test_off_switch(monkeypatch):
    monkeypatch.setenv("FACETECH_CAPTURE_DB", "off")
    assert capture_store.open_from_env() == (None, None)


def test_an_unknown_label_is_fatal_not_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("FACETECH_CAPTURE_DB", str(tmp_path / "c.db"))
    monkeypatch.setenv("FACETECH_CAPTURE_LABEL", "printout")
    with pytest.raises(SystemExit):
        capture_store.open_from_env()


def test_schema_is_created_idempotently(tmp_path):
    path = tmp_path / "c.db"
    first = capture_store.CaptureStore(path)
    first.record(
        endpoint="/v1/enroll",
        content_type="",
        body=SCAN,
        status_code=200,
        response=b"{}",
    )
    first.close()
    second = capture_store.CaptureStore(path)
    assert second.counts()["total"] == 1
    second.close()


def test_export_writes_labelled_scans_and_holds_back_unlabelled(tmp_path):
    import export_captures

    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    store.record(
        endpoint="/v1/liveness",
        content_type="",
        body=SCAN,
        status_code=200,
        response=b"{}",
        label="print",
    )
    store.record(
        endpoint="/v1/liveness",
        content_type="",
        body=SCAN + b"2",
        status_code=200,
        response=b"{}",
        label=None,
    )
    store.close()

    dest = tmp_path / "pad"
    export_captures.cmd_export(
        type("A", (), {"db": db, "dest": dest, "again": False})()
    )

    written = list((dest / "print").glob("*.msgpack"))
    assert len(written) == 1
    assert written[0].read_bytes() == SCAN
    assert not (dest / "None").exists()

    conn = sqlite3.connect(db)
    stamped = conn.execute(
        "SELECT COUNT(*) FROM captures WHERE exported_at IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert stamped == 1


def test_parse_ids_ranges():
    from export_captures import _parse_ids

    assert _parse_ids("12") == [12]
    assert _parse_ids("4,7,9-11") == [4, 7, 9, 10, 11]
