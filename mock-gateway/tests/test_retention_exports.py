import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app as gateway
import capture_store
import export_captures

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
SCAN = b"\x83\xa7version\x01\xa6frames\x90\xa6SYNTHETIC-not-a-face"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def add(store, subject=None, label=None, marker=b"", request_id=None):
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
            **({"label": label} if label else {}),
        },
    )


def stored_rows(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    finally:
        conn.close()


def dataset(dest):
    info = json.loads((dest / "dataset.json").read_text())
    manifests = sorted(dest.glob("manifest-v*.jsonl"))
    sums = sorted(dest.glob("SHA256SUMS-v*"))
    assert [m.name for m in manifests] == [info["manifest"]]
    assert [s.name for s in sums] == [info["checksums"]]
    entries = [json.loads(x) for x in manifests[0].read_text().splitlines()]
    sum_lines = sums[0].read_text().splitlines()
    files = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*.msgpack"))
    for line in sum_lines:
        digest, rel = line.split("  ")
        assert hashlib.sha256((dest / rel).read_bytes()).hexdigest() == digest
    assert info["files"] == len(entries) == len(sum_lines)
    return info, entries, sum_lines, files


def on_disk(dest) -> list[str]:
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*.msgpack"))


def export(db, dest):
    export_captures.main(["--db", str(db), "export", "--dest", str(dest)])


def test_91_days_with_no_new_capture_still_purges_on_status_read(tmp_path):
    clock = Clock(NOW)
    store = capture_store.CaptureStore(tmp_path / "c.db", clock=clock)
    add(store, "T01", marker=b"-EXPIRED-")
    clock.now = NOW + timedelta(days=capture_store.RETENTION_DAYS + 1)
    counts = store.counts()
    assert counts["total"] == 0
    assert counts["purge_failures"] == 0
    assert counts["last_purge_at"] == (clock.now).isoformat(timespec="seconds")
    store.close()


def test_capture_status_purges_without_new_captures(tmp_path, monkeypatch):
    clock = Clock(NOW)
    store = capture_store.CaptureStore(tmp_path / "c.db", clock=clock)
    add(store, "T01")
    add(store, "T02")
    clock.now = NOW + timedelta(days=91)
    monkeypatch.setattr(gateway, "CAPTURES", store)
    with TestClient(gateway.app) as c:
        status = c.get("/capture-status").json()
    assert status["total"] == 0
    assert status["purge_failures"] == 0
    assert stored_rows(tmp_path / "c.db") == 0
    store.close()


def test_the_gateway_purges_on_a_schedule_with_no_requests(tmp_path, monkeypatch):
    path = tmp_path / "c.db"
    clock = Clock(NOW)
    store = capture_store.CaptureStore(path, clock=clock)
    add(store, "T01")
    clock.now = NOW + timedelta(days=91)
    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(gateway, "PURGE_TASK_S", 0.02, raising=False)
    with TestClient(gateway.app):
        deadline = time.monotonic() + 5
        while stored_rows(path) and time.monotonic() < deadline:
            time.sleep(0.02)
        left = stored_rows(path)
    assert left == 0
    store.close()


def test_a_purge_error_is_counted_logged_and_shown(tmp_path, monkeypatch, caplog):
    store = capture_store.CaptureStore(tmp_path / "c.db", clock=Clock(NOW))

    def broken(days):
        raise sqlite3.OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr(store, "purge_older_than", broken)
    with caplog.at_level("WARNING", logger="facetech.gateway"):
        assert store.purge_expired() == 0
    assert store.purge_failures == 1
    assert store.last_purge_error == "OperationalError"
    assert "purge failed" in caplog.text
    assert "/secret/path" not in caplog.text
    monkeypatch.setattr(gateway, "CAPTURES", store)
    with TestClient(gateway.app) as c:
        status = c.get("/capture-status").json()
    assert status["purge_failures"] == 2
    assert status["last_purge_error"] == "OperationalError"
    store.close()


def test_a_failing_scheduled_purge_keeps_running(tmp_path, monkeypatch):
    store = capture_store.CaptureStore(tmp_path / "c.db", clock=Clock(NOW))
    calls = []

    def broken(days):
        calls.append(days)
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(store, "purge_older_than", broken)
    monkeypatch.setattr(gateway, "CAPTURES", store)
    monkeypatch.setattr(gateway, "PURGE_TASK_S", 0.02, raising=False)
    with TestClient(gateway.app):
        deadline = time.monotonic() + 5
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
    assert len(calls) >= 3
    assert store.purge_failures >= 3
    store.close()


def test_export_refuses_while_the_retention_purge_is_failing(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db, clock=Clock(NOW))
    add(store, "T01", label="print")
    store.close()

    def broken(self, days):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(capture_store.CaptureStore, "purge_older_than", broken)
    dest = tmp_path / "pad"
    with pytest.raises(SystemExit):
        export(db, dest)
    assert not dest.exists() or not list(dest.rglob("*.msgpack"))


def test_banking_stays_off_by_default_and_needs_consent():
    assert capture_store.configured_path() is None
    assert capture_store.parse_meta(json.dumps({"subject": "T01"})) == {
        "subject_id": "T01"
    }


def test_relabel_after_export_leaves_one_copy_in_the_new_class(tmp_path):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    cid = add(store, "T01", label="print")
    add(store, "T02", label="bona_fide", marker=b"2")
    store.close()
    dest = tmp_path / "pad"
    export(db, dest)
    assert [f.split("/")[0] for f in on_disk(dest)] == ["bona_fide", "print"]

    export_captures.main(["--db", str(db), "label", "screen_phone", "--ids", str(cid)])
    assert [f.split("/")[0] for f in on_disk(dest)] == ["bona_fide", "screen_phone"], (
        "the relabelled scan must leave print/ and be in screen_phone/ once"
    )
    info, entries, sums, files = dataset(dest)
    assert info["version"] == 2
    assert [e["label"] for e in entries] == ["screen_phone", "bona_fide"]
    assert sorted(e["file"] for e in entries) == files
    assert sorted(line.split("  ")[1] for line in sums) == files
    assert info["by_label"] == {"screen_phone": 1, "bona_fide": 1}

    export(db, dest)
    info, entries, _, files = dataset(dest)
    assert info["version"] == 3
    assert len(files) == 2
    assert [e["label"] for e in entries] == ["screen_phone", "bona_fide"]


def test_a_rebuild_follows_the_store_labels_even_when_changed_elsewhere(tmp_path):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    cid = add(store, "T01", label="print")
    store.close()
    dest = tmp_path / "pad"
    export(db, dest)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE captures SET label='virtual_cam' WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    export(db, dest)
    assert [f.split("/")[0] for f in on_disk(dest)] == ["virtual_cam"]
    _, entries, _, files = dataset(dest)
    assert [e["label"] for e in entries] == ["virtual_cam"]


def test_delete_subject_removes_exported_files_and_manifest_entries(tmp_path):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    add(store, "T01", label="print", marker=b"-GONE-")
    add(store, "T02", label="print", marker=b"-KEPT-")
    store.close()
    dest = tmp_path / "pad"
    export(db, dest)
    export_captures.main(["--db", str(db), "delete-subject", "t01", "--yes"])
    assert b"-GONE-" not in b"".join(p.read_bytes() for p in dest.rglob("*.msgpack"))
    assert len(on_disk(dest)) == 1
    info, entries, sums, files = dataset(dest)
    assert [e["subject_id"] for e in entries] == ["T02"]
    assert len(files) == 1 and len(sums) == 1
    assert "T01" not in "".join(p.read_text() for p in dest.glob("manifest-v*.jsonl"))
    conn = sqlite3.connect(db)
    left = conn.execute(
        "SELECT COUNT(*) FROM exported_files WHERE subject_id='T01'"
    ).fetchone()[0]
    conn.close()
    assert left == 0


def test_delete_subject_finds_copies_after_the_store_row_is_gone(tmp_path):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    add(store, "T01", label="print")
    store.close()
    dest = tmp_path / "pad"
    export(db, dest)
    export_captures.main(["--db", str(db), "purge", "--exported", "--yes"])
    assert stored_rows(db) == 0
    export_captures.main(["--db", str(db), "delete-subject", "T01", "--yes"])
    assert on_disk(dest) == []
    entries = dataset(dest)[1]
    assert entries == []


def test_delete_request_removes_its_exported_copy(tmp_path):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    add(store, label="print", request_id="a" * 32)
    add(store, label="print", request_id="b" * 32, marker=b"2")
    store.close()
    dest = tmp_path / "pad"
    export(db, dest)
    export_captures.main(["--db", str(db), "delete-request", "a" * 32])
    assert len(on_disk(dest)) == 1
    entries = dataset(dest)[1]
    assert [e["request_id"] for e in entries] == ["b" * 32]


def test_the_inventory_survives_re_export_and_covers_every_destination(tmp_path):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    add(store, "T01", label="print")
    store.close()
    a, b = tmp_path / "a", tmp_path / "b"
    export(db, a)
    export(db, a)
    export(db, b)
    store = capture_store.CaptureStore(db)
    add(store, "T02", label="print", marker=b"2")
    store.close()
    export(db, a)
    export_captures.main(["--db", str(db), "delete-subject", "T01", "--yes"])
    assert (len(on_disk(a)), len(on_disk(b))) == (1, 0), "T01 copies left behind"
    conn = sqlite3.connect(db)
    inventory = conn.execute(
        "SELECT subject_id, COUNT(*) FROM exported_files GROUP BY 1 ORDER BY 1"
    ).fetchall()
    conn.close()
    assert inventory == [("T02", 1)]
    assert json.loads((a / "dataset.json").read_text())["version"] == 4
    for dest, left in ((a, ["T02"]), (b, [])):
        _, entries, _, files = dataset(dest)
        assert [e["subject_id"] for e in entries] == left
        assert len(files) == len(left)


def test_a_copy_that_cannot_be_removed_is_reported_and_kept_in_the_inventory(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "c.db"
    store = capture_store.CaptureStore(db)
    add(store, "T01", label="print")
    store.close()
    dest = tmp_path / "pad"
    export(db, dest)

    def refuse(dest, rel):
        raise PermissionError("denied")

    monkeypatch.setattr(export_captures, "_remove_copy", refuse)
    with pytest.raises(SystemExit) as exc:
        export_captures.main(["--db", str(db), "delete-subject", "T01", "--yes"])
    assert exc.value.code == 1
    assert "could not remove" in capsys.readouterr().out
    conn = sqlite3.connect(db)
    left = conn.execute("SELECT COUNT(*) FROM exported_files").fetchone()[0]
    conn.close()
    assert left == 1
