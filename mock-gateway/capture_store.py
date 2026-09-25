import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("facetech.gateway")

CAPTURED_ENDPOINTS = frozenset({"/v1/enroll", "/v1/verify", "/v1/liveness"})

MSGPACK_CONTENT_TYPES = frozenset(
    {"application/msgpack", "application/x-msgpack", "application/vnd.msgpack"}
)

LABELS = (
    "bona_fide",
    "print",
    "screen_phone",
    "screen_laptop",
    "virtual_cam",
)

RETENTION_DAYS = 90
_PURGE_EVERY_S = 24 * 3600

CONSENT_VERSION = "storage-consent-v1"

META_HEADER = "X-Capture-Meta"
MAX_META_CHARS = 1024

SUBJECT_RE = re.compile(r"(?=[A-Za-z0-9-]*[0-9])[A-Za-z0-9][A-Za-z0-9-]{1,15}")
GUIDE_STATES = frozenset(
    {"none", "multi", "far", "close", "tooclose", "offcentre", "good", "unknown", "off"}
)
GUIDE_PHASES = frozenset({"hold", "move", "done"})
ABORT_REASONS = frozenset(
    {"face-lost", "hidden", "pagehide", "signed-out", "superseded"}
)
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
MAX_UA_CHARS = 256
MAX_BUILD_CHARS = 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at  TEXT    NOT NULL,   -- ISO-8601 UTC, when the gateway saw it
    endpoint     TEXT    NOT NULL,   -- /v1/enroll | /v1/verify | /v1/liveness
    content_type TEXT    NOT NULL,   -- transport the browser used
    scan         BLOB    NOT NULL,   -- the FaceScan msgpack, unmodified
    scan_sha256  TEXT    NOT NULL,
    scan_bytes   INTEGER NOT NULL,
    status_code  INTEGER NOT NULL,   -- what the engine answered
    response     TEXT,               -- engine response (errors: code only)
    label        TEXT,               -- one of LABELS, or NULL = unlabelled
    note         TEXT,
    exported_at  TEXT                -- set once handed to eval / the other team
);
"""

COLUMNS = {
    "decision_json": "TEXT",
    "test_context": "TEXT",
    "request_id": "TEXT",
    "build_id": "TEXT",
    "subject_id": "TEXT",
    "user_agent": "TEXT",
    "consent": "TEXT",
    "brightness": "REAL",
    "guide_state": "TEXT",
    "guide_phase": "TEXT",
    "restarts": "INTEGER",
    "abort_reason": "TEXT",
}

INDEXES = """
CREATE INDEX IF NOT EXISTS captures_label    ON captures(label);
CREATE INDEX IF NOT EXISTS captures_sha      ON captures(scan_sha256);
CREATE INDEX IF NOT EXISTS captures_pending  ON captures(exported_at);
CREATE INDEX IF NOT EXISTS captures_subject  ON captures(subject_id);
CREATE INDEX IF NOT EXISTS captures_request  ON captures(request_id);
CREATE INDEX IF NOT EXISTS captures_captured ON captures(captured_at);
"""

EXPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS exported_files (
    dest        TEXT    NOT NULL,
    capture_id  INTEGER NOT NULL,
    file        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    subject_id  TEXT,
    request_id  TEXT,
    entry       TEXT    NOT NULL,
    exported_at TEXT    NOT NULL,
    PRIMARY KEY (dest, capture_id)
);
CREATE INDEX IF NOT EXISTS exported_subject ON exported_files(subject_id);
CREATE INDEX IF NOT EXISTS exported_request ON exported_files(request_id);
CREATE TABLE IF NOT EXISTS export_versions (
    dest     TEXT    PRIMARY KEY,
    version  INTEGER NOT NULL,
    built_at TEXT    NOT NULL
);
"""

DEFAULT_DB = Path(__file__).resolve().parent / "captures.db"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _media_type(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def scan_bytes_of(content_type: str, body: bytes) -> bytes | None:
    if not body:
        return None
    if _media_type(content_type) in MSGPACK_CONTENT_TYPES:
        return body
    try:
        envelope = json.loads(body)
        b64 = envelope["facescan"]
        if not isinstance(b64, str):
            return None
        scan = base64.b64decode(b64, validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error):
        return None
    return scan or None


def response_for_store(raw: bytes) -> str | None:
    if not raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        code = parsed["error"].get("code")
        return json.dumps({"error": {"code": code if isinstance(code, str) else None}})
    return text


def _allowed(value: object, allowed: frozenset[str] | tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def parse_meta(raw: str | None) -> dict:
    if not raw or len(raw) > MAX_META_CHARS:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    context = {}
    for field, allowed in {
        "lighting": {"daylight", "room", "dim", "backlight"},
        "accessory": {"glasses"},
        "case": {"self", "different_person", "print", "screen_photo", "screen_video"},
    }.items():
        if isinstance(data.get(field), str) and data[field] in allowed:
            context[field] = data[field]
    if context:
        out["test_context"] = json.dumps(context)
    if data.get("consent") == CONSENT_VERSION:
        out["consent"] = CONSENT_VERSION
    subject = data.get("subject")
    if isinstance(subject, str) and SUBJECT_RE.fullmatch(subject):
        out["subject_id"] = subject.upper()
    if label := _allowed(data.get("label"), LABELS):
        out["label"] = label
    brightness = data.get("brightness")
    if (
        isinstance(brightness, (int, float))
        and not isinstance(brightness, bool)
        and 0 <= brightness <= 255
    ):
        out["brightness"] = round(float(brightness), 1)
    if guide_state := _allowed(data.get("guide_state"), GUIDE_STATES):
        out["guide_state"] = guide_state
    if guide_phase := _allowed(data.get("guide_phase"), GUIDE_PHASES):
        out["guide_phase"] = guide_phase
    restarts = data.get("restarts")
    if (
        isinstance(restarts, int)
        and not isinstance(restarts, bool)
        and 0 <= restarts <= 99
    ):
        out["restarts"] = restarts
    if abort_reason := _allowed(data.get("abort_reason"), ABORT_REASONS):
        out["abort_reason"] = abort_reason
    return out


def _clean_request_id(value: str | None) -> str | None:
    if value and REQUEST_ID_RE.fullmatch(value):
        return value
    return None


class CaptureStore:
    def __init__(self, path: Path, *, clock=_utc_now, evaluation_days=None) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self.write_failures = 0
        self.bank_failures = 0
        self.purge_failures = 0
        self.last_purge_at: str | None = None
        self.last_purge_error: str | None = None
        self.skipped: dict[str, int] = {}
        self._last_purge = 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")

        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.executescript(INDEXES)
        self._conn.executescript(EXPORT_SCHEMA)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS capture_policy (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        if evaluation_days is not None:
            if not 1 <= evaluation_days <= 90:
                raise ValueError("evaluation retention must be 1-90 days")
            if self._conn.execute("SELECT COUNT(*) FROM exported_files").fetchone()[0]:
                raise ValueError(
                    "evaluation mode requires a store without exported copies"
                )
            self._conn.execute(
                "INSERT OR REPLACE INTO capture_policy VALUES ('evaluation_days', ?)",
                (str(evaluation_days),),
            )
        policy = self._conn.execute(
            "SELECT value FROM capture_policy WHERE key='evaluation_days'"
        ).fetchone()
        self.evaluation_only = policy is not None
        self.retention_days = int(policy[0]) if policy else RETENTION_DAYS
        self._conn.commit()
        if not existed:
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        self.purge_expired()

    def _migrate(self) -> None:
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(captures)")}
        for name, kind in COLUMNS.items():
            if name not in have:
                self._conn.execute(f"ALTER TABLE captures ADD COLUMN {name} {kind}")

    def record(
        self,
        *,
        endpoint: str,
        content_type: str,
        body: bytes,
        status_code: int,
        response: bytes,
        label: str | None = None,
        request_id: str | None = None,
        build_id: str | None = None,
        user_agent: str | None = None,
        meta: dict | None = None,
        decision: str | None = None,
    ) -> int | None:
        if not body:
            return None
        if self.evaluation_only and len(body) > 350 * 1024:
            self.skip("oversized_scan")
            return None
        meta = meta or {}
        now = self._clock()
        try:
            with self._lock:
                if (
                    self.evaluation_only
                    and self._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[
                        0
                    ]
                    >= 5000
                ):
                    self.skipped["capacity"] = self.skipped.get("capacity", 0) + 1
                    return None
                cur = self._conn.execute(
                    "INSERT INTO captures (captured_at, endpoint, content_type,"
                    " scan, scan_sha256, scan_bytes, status_code, response, label,"
                    " request_id, build_id, subject_id, user_agent, consent,"
                    " brightness, guide_state, guide_phase, restarts, abort_reason,"
                    " decision_json, test_context)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _iso(now),
                        endpoint,
                        content_type,
                        body,
                        hashlib.sha256(body).hexdigest(),
                        len(body),
                        status_code,
                        response_for_store(response),
                        meta.get("label", label),
                        _clean_request_id(request_id),
                        (build_id or None) and build_id[:MAX_BUILD_CHARS],
                        meta.get("subject_id"),
                        (user_agent or None) and user_agent[:MAX_UA_CHARS],
                        meta.get("consent"),
                        meta.get("brightness"),
                        meta.get("guide_state"),
                        meta.get("guide_phase"),
                        meta.get("restarts"),
                        meta.get("abort_reason"),
                        clean_decision(decision, request_id),
                        meta.get("test_context"),
                    ),
                )
                self._conn.commit()
                row_id = cur.lastrowid
        except sqlite3.Error:
            self.write_failures += 1
            return None
        if time.monotonic() - self._last_purge >= _PURGE_EVERY_S:
            self.purge_expired()
        return row_id

    def bank_failed(self) -> None:
        with self._lock:
            self.bank_failures += 1

    def skip(self, reason: str) -> None:
        with self._lock:
            self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def _delete(self, where: str, params: tuple) -> int:
        with self._lock:
            n = self._conn.execute(
                f"DELETE FROM captures WHERE {where}", params
            ).rowcount
            self._conn.commit()
            if n:
                self._conn.execute("VACUUM")
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return n

    def delete_subject(self, subject_id: str) -> int:
        if not isinstance(subject_id, str) or not SUBJECT_RE.fullmatch(subject_id):
            raise ValueError("not a subject code")
        return self._delete("subject_id = ?", (subject_id.upper(),))

    def delete_request(self, request_id: str) -> int:
        if _clean_request_id(request_id) is None:
            raise ValueError("not a request id")
        return self._delete("request_id = ?", (request_id,))

    def purge_older_than(self, days: int) -> int:
        cutoff = _iso(self._clock() - timedelta(days=days))
        return self._delete("captured_at < ?", (cutoff,))

    def purge_expired(self) -> int:
        self._last_purge = time.monotonic()
        try:
            n = self.purge_older_than(self.retention_days)
        except Exception as exc:
            with self._lock:
                self.purge_failures += 1
                self.last_purge_error = type(exc).__name__
            log.warning("capture retention purge failed error=%s", type(exc).__name__)
            return 0
        with self._lock:
            self.last_purge_at = _iso(self._clock())
            self.last_purge_error = None
        return n

    def counts(self) -> dict:
        self.purge_expired()
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(label, '(unlabelled)'), COUNT(*) FROM captures"
                " GROUP BY 1 ORDER BY 1"
            ).fetchall()
            total, pending, subjects = self._conn.execute(
                "SELECT COUNT(*), SUM(exported_at IS NULL), COUNT(DISTINCT subject_id)"
                " FROM captures"
            ).fetchone()
            skipped = dict(self.skipped)
            purge = {
                "purge_failures": self.purge_failures,
                "last_purge_at": self.last_purge_at,
                "last_purge_error": self.last_purge_error,
            }
        return {
            "total": total or 0,
            "pending_export": pending or 0,
            "subjects": subjects or 0,
            "by_label": dict(rows),
            "skipped": skipped,
            "write_failures": self.write_failures,
            "bank_failures": self.bank_failures,
            **purge,
            "retention_days": self.retention_days,
            "evaluation_only": self.evaluation_only,
            "consent_version": CONSENT_VERSION,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_from_env() -> tuple[CaptureStore | None, str | None]:
    path = configured_path()
    if path is None:
        return None, None
    label = os.environ.get("FACETECH_CAPTURE_LABEL", "").strip() or None
    if label is not None and label not in LABELS:
        raise SystemExit(
            f"FACETECH_CAPTURE_LABEL={label!r} is not one of {', '.join(LABELS)}. "
            "A wrong label silently corrupts the APCER count, so this is fatal."
        )
    days = os.environ.get("FACETECH_EVALUATION_DAYS")
    return CaptureStore(path, evaluation_days=int(days) if days else None), label


def clean_decision(raw: str | None, request_id: str | None) -> str | None:
    if not raw or len(raw) > 16384:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("request_id") != request_id:
        return None
    fields = {
        "event",
        "ts",
        "request_id",
        "build_id",
        "endpoint",
        "http_status",
        "outcome",
        "error_code",
        "reason",
        "engine_version",
        "enforced",
        "thresholds",
        "timing_ms",
        "frames",
        "challenge",
        "liveness",
        "match",
        "pad",
        "head_sequence",
    }
    return json.dumps({k: v for k, v in value.items() if k in fields})


def configured_path() -> Path | None:
    setting = os.environ.get("FACETECH_CAPTURE_DB", "").strip()
    if not setting or setting.lower() in {"off", "0", "false", "no"}:
        return None
    return Path(setting)
