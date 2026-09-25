import argparse
import contextlib
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from capture_store import (
    DEFAULT_DB,
    LABELS,
    CaptureStore,
    configured_path,
    response_for_store,
    scan_bytes_of,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "engine" / "eval" / "data" / "pad"
DATASET_FILE = "dataset.json"

MANIFEST_FIELDS = (
    "id",
    "captured_at",
    "endpoint",
    "status_code",
    "response",
    "label",
    "request_id",
    "build_id",
    "subject_id",
    "user_agent",
    "consent",
    "brightness",
    "guide_state",
    "guide_phase",
    "restarts",
    "abort_reason",
    "scan_sha256",
    "scan_bytes",
)


def _open(db: Path) -> CaptureStore:
    if not db.exists():
        sys.exit(f"no capture store at {db} - nothing has been recorded yet")
    return CaptureStore(db)


def _parse_ids(spec: str) -> list[int]:
    ids: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            ids.extend(range(int(lo), int(hi) + 1))
        elif part:
            ids.append(int(part))
    return ids


def _confirm(args: argparse.Namespace, prompt: str) -> None:
    if getattr(args, "yes", False):
        return
    if input(f"{prompt} type 'yes': ").strip() != "yes":
        sys.exit("aborted")


def cmd_status(args: argparse.Namespace) -> None:
    store = _open(args.db)
    conn = store._conn
    rows = conn.execute(
        "SELECT COALESCE(label, '(unlabelled)') AS l, COUNT(*),"
        " SUM(exported_at IS NULL), MIN(captured_at), MAX(captured_at),"
        " SUM(scan_bytes), COUNT(DISTINCT subject_id)"
        " FROM captures GROUP BY l ORDER BY l"
    ).fetchall()
    if not rows:
        print("store is empty")
        return
    print(f"{args.db}\n")
    print(f"{'label':<16}{'rows':>6}{'pending':>9}{'subj':>6}{'MB':>8}  first .. last")
    for label, n, pending, first, last, nbytes, subjects in rows:
        mb = (nbytes or 0) / 1_000_000
        print(
            f"{label:<16}{n:>6}{pending or 0:>9}{subjects:>6}{mb:>8.1f}"
            f"  {first} .. {last}"
        )

    target = {
        "bona_fide": 40,
        "print": 20,
        "screen_phone": 20,
        "screen_laptop": 20,
        "virtual_cam": 10,
    }
    have = {label: n for label, n, *_ in rows}
    missing = {k: v - have.get(k, 0) for k, v in target.items() if have.get(k, 0) < v}
    if missing:
        gap = ", ".join(f"{k} +{v}" for k, v in missing.items())
        print(f"\nstill needed for the PLAN_SECURITY S7.4 PAD set: {gap}")
    else:
        print("\nPLAN_SECURITY S7.4 PAD set is complete")


def cmd_label(args: argparse.Namespace) -> None:
    store = _open(args.db)
    conn = store._conn
    if args.ids:
        ids = _parse_ids(args.ids)
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"UPDATE captures SET label=? WHERE id IN ({marks})", [args.label, *ids]
        )
    elif args.unlabelled:
        ids = [
            r[0] for r in conn.execute("SELECT id FROM captures WHERE label IS NULL")
        ]
        cur = conn.execute(
            "UPDATE captures SET label=? WHERE label IS NULL", (args.label,)
        )
    else:
        sys.exit("pass --ids 12-31 or --unlabelled")
    moved = _relabel_exports(conn, ids, args.label)
    conn.commit()
    print(f"labelled {cur.rowcount} row(s) as {args.label}")
    if moved:
        print(f"moved {moved} exported copy(ies) to {args.label}; manifests rebuilt")


def _private_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _dest_key(dest: Path) -> str:
    return str(dest.resolve())


def _inside(dest: Path, rel: str) -> Path:
    root = dest.resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError(f"inventory path {rel!r} is outside {root}")
    return path


def _remove_copy(dest: Path, rel: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        _inside(dest, rel).unlink()


def _write_manifest(conn, dest: Path, now: datetime) -> int:
    key = _dest_key(dest)
    entries = [
        json.loads(text)
        for (text,) in conn.execute(
            "SELECT entry FROM exported_files WHERE dest=? ORDER BY capture_id", (key,)
        )
    ]
    row = conn.execute(
        "SELECT version FROM export_versions WHERE dest=?", (key,)
    ).fetchone()
    version = (row[0] if row else 0) + 1
    manifest_name = f"manifest-v{version}.jsonl"
    sums_name = f"SHA256SUMS-v{version}"
    by_label: dict[str, int] = {}
    for e in entries:
        by_label[e["label"]] = by_label.get(e["label"], 0) + 1
    dest.mkdir(parents=True, exist_ok=True)
    _private_write(
        dest / manifest_name, "".join(json.dumps(e) + "\n" for e in entries).encode()
    )
    _private_write(
        dest / sums_name,
        "".join(f"{e['file_sha256']}  {e['file']}\n" for e in entries).encode(),
    )
    built_at = now.isoformat(timespec="seconds")
    _private_write(
        dest / DATASET_FILE,
        json.dumps(
            {
                "version": version,
                "built_at": built_at,
                "manifest": manifest_name,
                "checksums": sums_name,
                "files": len(entries),
                "by_label": by_label,
            },
            indent=2,
        ).encode(),
    )
    for old in [*dest.glob("manifest-v*.jsonl"), *dest.glob("SHA256SUMS-v*")]:
        if old.name not in (manifest_name, sums_name):
            old.unlink()
    conn.execute(
        "INSERT OR REPLACE INTO export_versions (dest, version, built_at)"
        " VALUES (?,?,?)",
        (key, version, built_at),
    )
    return version


def _relabel_exports(conn, ids: list[int], label: str) -> int:
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    entries = conn.execute(
        "SELECT dest, capture_id, file, entry FROM exported_files"
        f" WHERE capture_id IN ({marks}) AND label != ?",
        [*ids, label],
    ).fetchall()
    dests = set()
    for dest_s, capture_id, rel, text in entries:
        dest = Path(dest_s)
        new_rel = f"{label}/{Path(rel).name}"
        target = _inside(dest, new_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        dests.add(dest_s)
        try:
            os.replace(_inside(dest, rel), target)
        except FileNotFoundError:
            conn.execute(
                "DELETE FROM exported_files WHERE dest=? AND capture_id=?",
                (dest_s, capture_id),
            )
            continue
        entry = {**json.loads(text), "label": label, "file": new_rel}
        conn.execute(
            "UPDATE exported_files SET file=?, label=?, entry=?"
            " WHERE dest=? AND capture_id=?",
            (new_rel, label, json.dumps(entry), dest_s, capture_id),
        )
    now = datetime.now(UTC)
    for dest_s in dests:
        _write_manifest(conn, Path(dest_s), now)
    return len(entries)


def _forget_exports(conn, column: str, value: str) -> tuple[int, list[str]]:
    entries = conn.execute(
        f"SELECT dest, capture_id, file FROM exported_files WHERE {column} = ?",
        (value,),
    ).fetchall()
    removed, failed, dests = 0, [], set()
    for dest_s, capture_id, rel in entries:
        try:
            _remove_copy(Path(dest_s), rel)
        except (OSError, ValueError) as exc:
            failed.append(f"{dest_s}/{rel}: {type(exc).__name__}")
            continue
        conn.execute(
            "DELETE FROM exported_files WHERE dest=? AND capture_id=?",
            (dest_s, capture_id),
        )
        dests.add(dest_s)
        removed += 1
    now = datetime.now(UTC)
    for dest_s in dests:
        if Path(dest_s).is_dir():
            _write_manifest(conn, Path(dest_s), now)
    conn.commit()
    return removed, failed


def cmd_export(args: argparse.Namespace) -> None:
    store = _open(args.db)
    if store.evaluation_only:
        store.close()
        sys.exit("Export is disabled for this evaluation store; review captures in place.")
    if store.purge_failures:
        sys.exit(
            "the retention purge failed "
            f"({store.last_purge_error}); nothing exported until it succeeds"
        )
    conn = store._conn
    cols = ", ".join(MANIFEST_FIELDS)
    rows = conn.execute(
        f"SELECT {cols}, content_type, scan FROM captures"
        " WHERE label IS NOT NULL ORDER BY id"
    ).fetchall()
    skipped = conn.execute(
        "SELECT COUNT(*) FROM captures WHERE label IS NULL"
    ).fetchone()[0]
    key = _dest_key(args.dest)
    known = dict(
        conn.execute(
            "SELECT capture_id, file FROM exported_files WHERE dest=?", (key,)
        ).fetchall()
    )
    if not rows and not known:
        print(f"nothing to export ({skipped} unlabelled row(s) held back)")
        return
    now = datetime.now(UTC)
    args.dest.mkdir(parents=True, exist_ok=True)
    legacy = sorted(p.name for p in args.dest.glob("manifest-2*.jsonl"))
    kept = set()
    for row in rows:
        meta = dict(zip(MANIFEST_FIELDS, row[: len(MANIFEST_FIELDS)]))
        content_type, stored = row[len(MANIFEST_FIELDS) :]
        scan = bytes(stored)

        if isinstance(meta["response"], str):
            meta["response"] = response_for_store(meta["response"].encode())

        if scan[:1] == b"{":
            scan = scan_bytes_of(content_type if content_type else "", scan) or scan
        out_dir = args.dest / meta["label"]
        out_dir.mkdir(parents=True, exist_ok=True)

        name = f"{meta['id']:05d}_{meta['scan_sha256'][:8]}.msgpack"
        rel = f"{meta['label']}/{name}"
        old = known.get(meta["id"])
        if old is not None and old != rel:
            _remove_copy(args.dest, old)
        _private_write(out_dir / name, scan)
        digest = hashlib.sha256(scan).hexdigest()
        stamp = now.isoformat(timespec="seconds")
        conn.execute(
            "INSERT OR REPLACE INTO exported_files (dest, capture_id, file, label,"
            " subject_id, request_id, entry, exported_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                key,
                meta["id"],
                rel,
                meta["label"],
                meta["subject_id"],
                meta["request_id"],
                json.dumps({**meta, "file": rel, "file_sha256": digest}),
                stamp,
            ),
        )
        conn.execute(
            "UPDATE captures SET exported_at=? WHERE id=?", (stamp, meta["id"])
        )
        kept.add(meta["id"])
    stale = [(cid, rel) for cid, rel in known.items() if cid not in kept]
    for capture_id, rel in stale:
        _remove_copy(args.dest, rel)
        conn.execute(
            "DELETE FROM exported_files WHERE dest=? AND capture_id=?",
            (key, capture_id),
        )
    version = _write_manifest(conn, args.dest, now)
    conn.commit()
    print(f"exported {len(rows)} scan(s) to {args.dest} (dataset version {version})")
    print(
        f"manifest-v{version}.jsonl, SHA256SUMS-v{version} and {DATASET_FILE} written"
    )
    if stale:
        print(f"removed {len(stale)} copy(ies) no longer in the store")
    if legacy:
        print(
            "older timestamped manifests are not in the export inventory and were "
            "left as they are: " + ", ".join(legacy)
        )
    if skipped:
        print(f"{skipped} unlabelled row(s) held back - label them first")
    print(
        "\nThis directory is biometric data. Keep it out of git (eval/data/ is "
        "ignored) and delete it when it has been copied."
    )


def cmd_purge(args: argparse.Namespace) -> None:
    store = _open(args.db)
    conn = store._conn
    if args.older_than is not None:
        if args.older_than < 1:
            sys.exit("--older-than must be at least 1 day")
        n = conn.execute(
            "SELECT COUNT(*) FROM captures WHERE captured_at < ?",
            (
                (datetime.now(UTC) - timedelta(days=args.older_than)).isoformat(
                    timespec="seconds"
                ),
            ),
        ).fetchone()[0]
        if not n:
            print("nothing matches")
            return
        _confirm(args, f"delete {n} capture(s) older than {args.older_than} days?")
        print(f"deleted {store.purge_older_than(args.older_than)} capture(s)")
        return
    if args.exported:
        where, params = "exported_at IS NOT NULL", ()
    elif args.ids:
        ids = _parse_ids(args.ids)
        where, params = f"id IN ({','.join('?' * len(ids))})", tuple(ids)
    else:
        sys.exit(
            "pass --exported, --ids or --older-than; there is no "
            "'purge everything' shortcut"
        )
    n = conn.execute(f"SELECT COUNT(*) FROM captures WHERE {where}", params).fetchone()[
        0
    ]
    if not n:
        print("nothing matches")
        return
    _confirm(args, f"delete {n} capture(s) permanently?")
    print(f"deleted {store._delete(where, params)} capture(s)")


def _report_exports(removed: int, failed: list[str]) -> None:
    if removed:
        print(f"removed {removed} exported copy(ies) and their manifest entries")
    if failed:
        print("could not remove: " + "; ".join(failed))
        sys.exit(1)


def cmd_delete_subject(args: argparse.Namespace) -> None:
    store = _open(args.db)
    try:
        code = args.subject.upper()
        n = store._conn.execute(
            "SELECT COUNT(*) FROM captures WHERE subject_id = ?", (code,)
        ).fetchone()[0]
        copies = store._conn.execute(
            "SELECT COUNT(*) FROM exported_files WHERE subject_id = ?", (code,)
        ).fetchone()[0]
        if n or copies:
            _confirm(
                args,
                f"delete all {n} capture(s) and {copies} exported copy(ies)"
                f" of subject {code}?",
            )
        deleted = store.delete_subject(args.subject)
    except ValueError:
        sys.exit(
            "that is not a subject code (2-16 letters/digits/dashes, with a digit)"
        )
    print(f"deleted {deleted} capture(s) of subject {code}")
    _report_exports(*_forget_exports(store._conn, "subject_id", code))


def cmd_delete_request(args: argparse.Namespace) -> None:
    store = _open(args.db)
    try:
        deleted = store.delete_request(args.request_id)
    except ValueError:
        sys.exit("that is not a scan reference")
    print(f"deleted {deleted} capture(s)")
    _report_exports(*_forget_exports(store._conn, "request_id", args.request_id))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Manage evaluation captures.")
    ap.add_argument("--db", type=Path, default=configured_path() or DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="rows by label, and the gap to the S7.4 set")

    p = sub.add_parser("label", help="set the attack type on rows")
    p.add_argument("label", choices=LABELS)
    p.add_argument("--ids", help="e.g. 12-31 or 4,7,9-11")
    p.add_argument("--unlabelled", action="store_true", help="every unlabelled row")

    p = sub.add_parser("export", help="write scans into the PAD eval layout")
    p.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    p.add_argument(
        "--again",
        action="store_true",
        help="accepted for old scripts; every export rebuilds the whole dataset",
    )

    p = sub.add_parser("purge", help="delete captures (retention)")
    p.add_argument("--exported", action="store_true")
    p.add_argument("--ids")
    p.add_argument("--older-than", type=int, metavar="DAYS")
    p.add_argument("--yes", action="store_true", help="do not ask")

    p = sub.add_parser("delete-subject", help="delete every capture of a subject code")
    p.add_argument("subject")
    p.add_argument("--yes", action="store_true", help="do not ask")

    p = sub.add_parser("delete-request", help="delete one capture by scan reference")
    p.add_argument("request_id")

    args = ap.parse_args(argv)
    {
        "status": cmd_status,
        "label": cmd_label,
        "export": cmd_export,
        "purge": cmd_purge,
        "delete-subject": cmd_delete_subject,
        "delete-request": cmd_delete_request,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
