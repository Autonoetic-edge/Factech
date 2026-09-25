import base64
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

import app as gateway
import capture_store
import export_captures

SCAN = b"\x83\xa7version\x01\xa6frames\x90\xa6MARKER-face-pixels-0123456789"
USER = "jane.doe"
ENGINE_BUILD = "abc1234"
CONSENT = {"consent": capture_store.CONSENT_VERSION}
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) test"


def meta(**fields) -> dict:
    return {capture_store.META_HEADER: json.dumps({**CONSENT, **fields})}


def json_body(user=USER, scan=SCAN) -> bytes:
    return json.dumps(
        {"user_id": user, "facescan": base64.b64encode(scan).decode()}
    ).encode()


@pytest.fixture
def store(tmp_path):
    s = capture_store.CaptureStore(tmp_path / "captures.db")
    yield s
    s.close()


@pytest.fixture
def engine(monkeypatch, store):
    seen: list[httpx.Request] = []
    replies = {
        "/v1/enroll": (200, {"template_id": "t-1"}),
        "/v1/verify": (
            404,
            {
                "error": {
                    "code": "USER_NOT_FOUND",
                    "message": f"user_id '{USER}' has none",
                }
            },
        ),
        "/v1/liveness": (200, {"live": True}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = replies.get(request.url.path, (404, {}))
        return httpx.Response(
            status,
            json=body,
            headers={
                "X-Request-Id": request.headers.get("X-Request-Id", "none"),
                "X-Engine-Build": ENGINE_BUILD,
            },
        )

    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(gateway, "CAPTURE_LABEL", None)
    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test", transport=httpx.MockTransport(handler)
        ),
    )
    with TestClient(gateway.app, headers={"User-Agent": UA}) as c:
        yield c, store, seen


def rows(store) -> list[dict]:
    cur = store._conn.execute("SELECT * FROM captures ORDER BY id")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def file_bytes(path) -> bytes:
    out = b""
    for p in (path, path.with_name(path.name + "-wal")):
        if p.exists():
            out += p.read_bytes()
    return out


def test_a_tester_row_carries_every_field_and_no_user_id(engine):
    c, store, seen = engine
    r = c.post(
        "/v1/enroll",
        content=json_body(),
        headers={
            "Content-Type": "application/json",
            **meta(
                subject="t07",
                label="print",
                brightness=131.44,
                guide_state="good",
                guide_phase="done",
                restarts=1,
                abort_reason="face-lost",
            ),
        },
    )
    assert r.status_code == 200
    (row,) = rows(store)
    rid = r.headers["X-Request-Id"]
    assert len(rid) == 32
    assert row["request_id"] == rid == seen[0].headers["X-Request-Id"]
    assert row["build_id"] == ENGINE_BUILD
    assert row["subject_id"] == "T07"
    assert row["label"] == "print"
    assert row["user_agent"] == UA
    assert row["consent"] == capture_store.CONSENT_VERSION
    assert row["brightness"] == 131.4
    assert (row["guide_state"], row["guide_phase"]) == ("good", "done")
    assert (row["restarts"], row["abort_reason"]) == (1, "face-lost")

    assert bytes(row["scan"]) == SCAN
    assert row["scan_sha256"] == hashlib.sha256(SCAN).hexdigest()
    assert USER not in json.dumps({k: v for k, v in row.items() if k != "scan"})
    assert USER.encode() not in bytes(row["scan"])


def test_the_engine_gets_a_request_id_and_never_the_meta(engine):
    c, _, seen = engine
    r = c.post(
        "/v1/liveness",
        content=SCAN,
        headers={"Content-Type": "application/msgpack", **meta(subject="T07")},
    )
    sent = seen[0].headers
    assert capture_store.META_HEADER.lower() not in {k.lower() for k in sent}
    assert sent["X-Request-Id"] == r.headers["X-Request-Id"]
    assert "X-Engine-Build" not in r.headers


def test_an_error_answer_is_stored_as_its_code_only(engine):
    c, store, _ = engine
    c.post(
        "/v1/verify",
        content=json_body(),
        headers={"Content-Type": "application/json", **meta()},
    )
    (row,) = rows(store)
    assert json.loads(row["response"]) == {"error": {"code": "USER_NOT_FOUND"}}


def test_per_scan_label_wins_and_the_process_label_is_the_fallback(engine, monkeypatch):
    c, store, _ = engine
    monkeypatch.setattr(gateway, "CAPTURE_LABEL", "bona_fide")
    msgpack = {"Content-Type": "application/msgpack"}
    c.post(
        "/v1/liveness", content=SCAN, headers={**msgpack, **meta(label="screen_phone")}
    )
    c.post("/v1/liveness", content=SCAN, headers={**msgpack, **meta()})
    assert [r["label"] for r in rows(store)] == ["screen_phone", "bona_fide"]


def test_unreachable_engine_row_uses_the_gateway_request_id(monkeypatch, store):
    def dead(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test", transport=httpx.MockTransport(dead)
        ),
    )
    with TestClient(gateway.app) as c:
        r = c.post(
            "/v1/enroll",
            content=SCAN,
            headers={"Content-Type": "application/msgpack", **meta()},
        )
    assert r.status_code == 502
    (row,) = rows(store)
    assert row["request_id"] == r.headers["X-Request-Id"]
    assert row["build_id"] is None


def test_no_consent_means_nothing_is_stored(engine):
    c, store, _ = engine
    msgpack = {"Content-Type": "application/msgpack"}
    r = c.post("/v1/enroll", content=SCAN, headers=msgpack)
    assert r.status_code == 200
    c.post(
        "/v1/enroll",
        content=SCAN,
        headers={
            **msgpack,
            capture_store.META_HEADER: json.dumps({"consent": "storage-consent-v0"}),
        },
    )
    assert rows(store) == []
    status = c.get("/capture-status").json()
    assert status["total"] == 0
    assert status["skipped"] == {"no_consent": 2}
    assert status["retention_days"] == 90


def test_a_body_that_is_not_a_scan_is_not_stored(engine):
    c, store, _ = engine
    for body in (
        b"not json",
        b'{"user_id":"x"}',
        b'{"facescan":"@@@"}',
        b'{"facescan":""}',
    ):
        c.post(
            "/v1/enroll",
            content=body,
            headers={"Content-Type": "application/json", **meta()},
        )
    assert rows(store) == []
    assert store.counts()["skipped"] == {"not_a_scan": 4}


@pytest.mark.parametrize(
    "fields",
    [
        {"subject": "JohnSmith"},
        {"subject": "T 07"},
        {"subject": "T" + "0" * 16},
        {"subject": 7},
        {"label": "printout"},
        {"brightness": 999},
        {"brightness": True},
        {"restarts": True},
        {"restarts": -1},
        {"guide_state": "<script>"},
        {"guide_phase": "later"},
        {"abort_reason": "because"},
    ],
)
def test_invalid_meta_fields_are_dropped(fields):
    parsed = capture_store.parse_meta(json.dumps({**CONSENT, **fields}))
    assert parsed == {"consent": capture_store.CONSENT_VERSION}


@pytest.mark.parametrize(
    "raw", [None, "", "[1]", "{bad", json.dumps({"x": "y" * 2000})]
)
def test_unusable_meta_header_is_empty(raw):
    assert capture_store.parse_meta(raw) == {}


def test_request_id_is_validated_before_storage(store):
    store.record(
        endpoint="/v1/enroll",
        content_type="application/msgpack",
        body=SCAN,
        status_code=200,
        response=b"{}",
        request_id="bad id; drop table",
    )
    assert rows(store)[0]["request_id"] is None


def _add(store, subject=None, marker=b"", request_id=None):
    return store.record(
        endpoint="/v1/liveness",
        content_type="application/msgpack",
        body=SCAN + marker,
        status_code=200,
        response=b"{}",
        request_id=request_id,
        meta={
            "consent": capture_store.CONSENT_VERSION,
            **({"subject_id": subject} if subject else {}),
        },
    )


def test_delete_by_subject_removes_only_that_subject_and_its_bytes(tmp_path):
    path = tmp_path / "c.db"
    store = capture_store.CaptureStore(path)
    _add(store, "T07", b"-GONE-GONE-GONE")
    _add(store, "T07", b"-GONE-GONE-GONE")
    _add(store, "T08", b"-KEPT-KEPT-KEPT")
    assert b"-GONE-GONE-GONE" in file_bytes(path)
    assert store.delete_subject("t07") == 2
    assert [r["subject_id"] for r in rows(store)] == ["T08"]
    assert b"-GONE-GONE-GONE" not in file_bytes(path)
    assert b"-KEPT-KEPT-KEPT" in file_bytes(path)
    assert store.delete_subject("T07") == 0
    with pytest.raises(ValueError):
        store.delete_subject("%")
    store.close()


def test_delete_by_request_id(store):
    _add(store, request_id="a" * 32)
    _add(store, request_id="b" * 32)
    assert store.delete_request("a" * 32) == 1
    assert [r["request_id"] for r in rows(store)] == ["b" * 32]
    with pytest.raises(ValueError):
        store.delete_request("x")


def test_cli_delete_subject(tmp_path, capsys):
    path = tmp_path / "c.db"
    store = capture_store.CaptureStore(path)
    _add(store, "T07")
    _add(store, "T09")
    store.close()
    export_captures.main(["--db", str(path), "delete-subject", "t07", "--yes"])
    assert "deleted 1 capture(s) of subject T07" in capsys.readouterr().out
    check = capture_store.CaptureStore(path)
    assert [r["subject_id"] for r in rows(check)] == ["T09"]
    check.close()
    with pytest.raises(SystemExit):
        export_captures.main(["--db", str(path), "delete-subject", "Jane", "--yes"])


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_rows_past_retention_are_purged_on_open(tmp_path):
    path = tmp_path / "c.db"
    now = datetime(2026, 9, 17, 12, tzinfo=UTC)
    old = capture_store.CaptureStore(path, clock=Clock(now - timedelta(days=91)))
    _add(old, "T01", b"-OLD-OLD-OLD")
    old._clock = Clock(now - timedelta(days=89))
    _add(old, "T02")
    old.close()

    fresh = capture_store.CaptureStore(path, clock=Clock(now))
    assert [r["subject_id"] for r in rows(fresh)] == ["T02"]
    assert b"-OLD-OLD-OLD" not in file_bytes(path)
    fresh.close()


def test_retention_also_runs_daily_while_the_gateway_is_up(tmp_path, monkeypatch):
    now = datetime(2026, 9, 17, 12, tzinfo=UTC)
    clock = Clock(now)
    store = capture_store.CaptureStore(tmp_path / "c.db", clock=clock)
    _add(store, "T01")
    clock.now = now + timedelta(days=capture_store.RETENTION_DAYS + 1)
    monkeypatch.setattr(store, "_last_purge", store._last_purge - 25 * 3600)
    _add(store, "T02")
    assert [r["subject_id"] for r in rows(store)] == ["T02"]
    store.close()


def test_cli_purge_older_than(tmp_path, capsys):
    path = tmp_path / "c.db"
    store = capture_store.CaptureStore(
        path, clock=Clock(datetime.now(UTC) - timedelta(days=10))
    )
    _add(store, "T01")
    store.close()
    export_captures.main(["--db", str(path), "purge", "--older-than", "30", "--yes"])
    assert "nothing matches" in capsys.readouterr().out
    export_captures.main(["--db", str(path), "purge", "--older-than", "7", "--yes"])
    assert "deleted 1 capture(s)" in capsys.readouterr().out


def test_retention_is_ninety_days():
    assert capture_store.RETENTION_DAYS == 90


def test_an_old_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(capture_store.SCHEMA)
    conn.execute(
        "INSERT INTO captures (captured_at, endpoint, content_type, scan, scan_sha256,"
        " scan_bytes, status_code) VALUES (?,?,?,?,?,?,?)",
        (datetime.now(UTC).isoformat(), "/v1/enroll", "", SCAN, "x", len(SCAN), 200),
    )
    conn.commit()
    conn.close()
    store = capture_store.CaptureStore(path)
    (row,) = rows(store)
    assert set(capture_store.COLUMNS) <= set(row)
    assert bytes(row["scan"]) == SCAN
    _add(store, "T01")
    assert store.counts()["subjects"] == 1
    store.close()


def test_write_failures_are_counted_not_raised(engine):
    c, store, _ = engine
    store._conn.execute("DROP TABLE captures")
    store._conn.commit()
    r = c.post(
        "/v1/enroll",
        content=SCAN,
        headers={"Content-Type": "application/msgpack", **meta()},
    )
    assert r.status_code == 200
    assert store.write_failures == 1


def test_export_writes_msgpack_manifest_and_checksums(tmp_path):
    path = tmp_path / "c.db"
    store = capture_store.CaptureStore(path)
    store.record(
        endpoint="/v1/verify",
        content_type="application/msgpack",
        body=SCAN,
        status_code=200,
        response=b'{"match":true}',
        request_id="c" * 32,
        build_id=ENGINE_BUILD,
        meta={
            "consent": capture_store.CONSENT_VERSION,
            "subject_id": "T07",
            "label": "bona_fide",
        },
    )

    store._conn.execute(
        "INSERT INTO captures (captured_at, endpoint, content_type, scan, scan_sha256,"
        " scan_bytes, status_code, response, label) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            datetime.now(UTC).isoformat(),
            "/v1/verify",
            "application/json",
            json_body(),
            "d" * 64,
            1,
            404,
            json.dumps(
                {"error": {"code": "USER_NOT_FOUND", "message": f"user_id '{USER}'"}}
            ),
            "print",
        ),
    )
    store._conn.commit()
    store.close()

    dest = tmp_path / "out"
    export_captures.main(["--db", str(path), "export", "--dest", str(dest)])
    files = sorted(dest.rglob("*.msgpack"))
    assert [f.read_bytes() for f in files] == [SCAN, SCAN]

    (sums,) = dest.glob("SHA256SUMS-*")
    for line in sums.read_text().splitlines():
        digest, rel = line.split("  ")
        assert hashlib.sha256((dest / rel).read_bytes()).hexdigest() == digest

    (manifest,) = dest.glob("manifest-*.jsonl")
    text = manifest.read_text()
    assert USER not in text
    entries = [json.loads(x) for x in text.splitlines()]
    assert entries[0]["request_id"] == "c" * 32
    assert entries[0]["subject_id"] == "T07"
    assert entries[0]["build_id"] == ENGINE_BUILD
    assert entries[1]["response"] == json.dumps({"error": {"code": "USER_NOT_FOUND"}})


def test_cli_finds_the_configured_database(monkeypatch, tmp_path, capsys):
    path = tmp_path / "configured.db"
    capture_store.CaptureStore(path).close()
    monkeypatch.setenv("FACETECH_CAPTURE_DB", str(path))
    export_captures.main(["status"])
    assert "store is empty" in capsys.readouterr().out


BAD_VALUES = [[], ["good"], {}, {"state": "good"}, None, 7, 1.5, True]


@pytest.mark.parametrize(
    "field", ["guide_state", "guide_phase", "abort_reason", "label"]
)
@pytest.mark.parametrize("value", BAD_VALUES, ids=repr)
def test_malformed_meta_types_are_dropped_not_raised(field, value):
    parsed = capture_store.parse_meta(json.dumps({**CONSENT, field: value}))
    assert parsed == {"consent": capture_store.CONSENT_VERSION}


@pytest.mark.parametrize("field", ["guide_state", "guide_phase", "abort_reason"])
@pytest.mark.parametrize("value", [["good"], {"a": 1}], ids=repr)
def test_malformed_meta_cannot_break_success_or_rejection(engine, field, value):
    c, store, _ = engine
    msgpack = {"Content-Type": "application/msgpack", **meta(**{field: value})}
    ok = c.post("/v1/enroll", content=SCAN, headers=msgpack)
    assert ok.status_code == 200
    assert ok.json() == {"template_id": "t-1"}
    assert ok.headers.get("x-request-id")
    rejected = c.post("/v1/verify", content=SCAN, headers=msgpack)
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "USER_NOT_FOUND"
    assert rejected.headers.get("x-request-id")
    banked = rows(store)
    assert len(banked) == 2, "consented scans still bank; only the bad field is dropped"
    assert all(r[field] is None for r in banked)
    assert store.counts()["bank_failures"] == 0


@pytest.mark.parametrize("broken", ["record", "parse_meta", "scan_bytes_of"])
def test_a_banking_failure_keeps_the_engine_answer(engine, monkeypatch, caplog, broken):
    c, store, _ = engine

    def boom(*_a, **_k):
        raise RuntimeError("storage exploded with jane.doe inside")

    if broken == "record":
        monkeypatch.setattr(store, "record", boom)
    else:
        monkeypatch.setattr(capture_store, broken, boom)
    headers = {
        "Content-Type": "application/msgpack",
        **meta(subject="T07", guide_state="good"),
    }
    with caplog.at_level("WARNING", logger="facetech.gateway"):
        ok = c.post("/v1/enroll", content=SCAN, headers=headers)
        rejected = c.post("/v1/verify", content=SCAN, headers=headers)
    assert ok.status_code == 200 and ok.json() == {"template_id": "t-1"}
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "USER_NOT_FOUND"
    for r in (ok, rejected):
        assert capture_store.REQUEST_ID_RE.fullmatch(r.headers["x-request-id"])
    status = c.get("/capture-status").json()
    assert status["bank_failures"] == 2
    text = caplog.text
    assert text.count("capture banking failed") == 2
    assert "RuntimeError" in text
    for secret in ("jane.doe", "T07", "MARKER", "storage exploded", "good"):
        assert secret not in text


def test_a_banking_failure_on_the_unreachable_path_keeps_the_502(monkeypatch, store):
    def down(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test", transport=httpx.MockTransport(down)
        ),
    )
    monkeypatch.setattr(store, "record", lambda **_k: 1 / 0)
    with TestClient(gateway.app) as c:
        r = c.post(
            "/v1/enroll",
            content=SCAN,
            headers={"Content-Type": "application/msgpack", **meta()},
        )
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "ENGINE_UNREACHABLE"
    assert r.headers.get("x-request-id")
    assert store.bank_failures == 1


def test_cancellation_is_not_swallowed_by_the_banking_guard(monkeypatch, store):
    import asyncio

    def cancelled(**_k):
        raise asyncio.CancelledError

    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(store, "record", cancelled)

    class FakeRequest:
        headers = {
            capture_store.META_HEADER: json.dumps(CONSENT),
            "content-type": "application/msgpack",
        }

    async def go():
        await gateway._bank("/v1/enroll", FakeRequest(), SCAN, 200, b"{}", "a" * 32)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())
    assert store.bank_failures == 0


def test_banking_off_never_touches_the_store(monkeypatch):
    monkeypatch.setattr(gateway, "CAPTURES", None)
    monkeypatch.setattr(capture_store, "parse_meta", lambda *_a: 1 / 0)
    monkeypatch.setattr(
        gateway,
        "http_client",
        httpx.AsyncClient(
            base_url="http://engine.test",
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"template_id": "t-1"})
            ),
        ),
    )
    with TestClient(gateway.app) as c:
        r = c.post(
            "/v1/enroll",
            content=SCAN,
            headers={"Content-Type": "application/msgpack", **meta()},
        )
    assert r.status_code == 200
