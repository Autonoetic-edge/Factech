import contextlib
import contextvars
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime

LOGGER_NAME = "facetech.decision"
REQUEST_ID_HEADER = "X-Request-Id"
BUILD_HEADER = "X-Engine-Build"

BUILD_ID_ENV = "FACETECH_BUILD_ID"

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")

MAX_REASON_CHARS = 160
MAX_STRING_CHARS = 120

MAX_FAILED_SIGNALS = 8

TRACED_PATHS = {
    "/v1/enroll": "enroll",
    "/v1/verify": "verify",
    "/v1/liveness": "liveness",
}

_logger = logging.getLogger(LOGGER_NAME)
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)

    _logger.propagate = False

_current: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar(
    "facetech_trace", default=None
)


def build_id() -> str:
    return os.environ.get(BUILD_ID_ENV, "").strip() or "dev"


def request_id_from(header_value: str | None) -> str:
    if header_value and _REQUEST_ID_RE.fullmatch(header_value.strip()):
        return header_value.strip()
    return uuid.uuid4().hex


def _scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, str):
        return value[:MAX_STRING_CHARS]
    return None


def _failed_signal_names(result) -> list[str]:
    known = result.signals if isinstance(result.signals, dict) else {}
    names = [n for n in result.failed if isinstance(n, str) and n in known]
    return names[:MAX_FAILED_SIGNALS]


def _flat(mapping) -> dict:
    if not isinstance(mapping, dict):
        return {}
    out = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            out[str(key)] = {
                str(k): _scalar(v)
                for k, v in value.items()
                if not isinstance(v, (dict, list, tuple, bytes, bytearray))
            }
        elif not isinstance(value, (list, tuple, bytes, bytearray)):
            out[str(key)] = _scalar(value)
    return out


class Trace:
    def __init__(self, endpoint: str, request_id: str) -> None:
        self.endpoint = endpoint
        self.request_id = request_id
        self._t0 = time.perf_counter()
        self.fields: dict = {}
        self.timing_ms: dict[str, float] = {}

    @contextlib.contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timing_ms[name] = round(
                self.timing_ms.get(name, 0.0) + (time.perf_counter() - start) * 1000, 2
            )

    def mark(self, name: str, since: float) -> None:
        self.timing_ms[name] = round((time.perf_counter() - since) * 1000, 2)

    def error(self, code: str, reason: str) -> None:
        self.fields["error_code"] = code
        self.fields["reason"] = str(reason)[:MAX_REASON_CHARS]

    def shape_error(self, loc: list, err_type: str) -> None:
        self.fields["error_code"] = None
        self.fields["reason"] = (
            f"request shape: {err_type} at {'.'.join(map(str, loc))}"[:MAX_REASON_CHARS]
        )

    def transport(self, name: str) -> None:
        self.fields["transport"] = name

    def frames(self, count: int, with_face: int) -> None:
        self.fields["frames"] = {"count": count, "with_face": with_face}

    def embedded(self, n: int) -> None:
        self.fields.setdefault("frames", {})["embedded"] = n

    def challenge(self, verdict) -> None:
        if verdict is None:
            self.fields["challenge"] = None
            return
        self.fields["challenge"] = {
            "ok": bool(verdict.ok),
            "reason": _scalar(verdict.reason),
            "action": _scalar(verdict.action),
            "presented_action": _scalar(verdict.presented_action),
            "params": _flat(verdict.params) if verdict.params else None,
            "consumed": bool(verdict.consumed),
        }

    def liveness(self, result, threshold: float) -> None:
        voting = [s for s in result.signals.values() if s.votes]
        weakest = min(voting, key=lambda s: s.score, default=None)
        self.fields["liveness"] = {
            "live": bool(result.live),
            "score": _scalar(result.score),
            "min_signal": weakest.name if weakest else None,
            "failed": _failed_signal_names(result),
            "retryable": bool(result.retryable),
            "signals": {
                name: {
                    "score": _scalar(s.score),
                    "ok": s.ok,
                    "advisory": bool(s.advisory),
                    "votes": bool(s.votes),
                    **{
                        k: v
                        for k, v in _flat(s.detail).items()
                        if k not in {"params", "nonce_prefix"}
                    },
                }
                for name, s in result.signals.items()
            },
        }
        self.fields.setdefault("thresholds", {})["liveness"] = threshold

    def match(self, similarity: float, matched: bool, threshold: float) -> None:
        self.fields["match"] = {"similarity": _scalar(similarity), "match": matched}
        self.fields.setdefault("thresholds", {})["match"] = threshold

    def set(self, key: str, value) -> None:
        self.fields[key] = _scalar(value)

    def line(self, status: int) -> dict:
        self.timing_ms["total"] = round((time.perf_counter() - self._t0) * 1000, 2)
        body = {
            "event": "decision",
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "request_id": self.request_id,
            "build_id": build_id(),
            "endpoint": self.endpoint,
            "http_status": status,
            "outcome": self.fields.get("outcome") or _outcome_for(status),
            "error_code": None,
            "reason": None,
        }
        body.update({k: v for k, v in self.fields.items() if k != "outcome"})
        body["timing_ms"] = dict(self.timing_ms)
        return body

    def emit(self, status: int) -> dict:
        body = self.line(status)
        _logger.info(json.dumps(body, separators=(",", ":"), sort_keys=True))
        return body


def _outcome_for(status: int) -> str:
    if status >= 500:
        return "error"
    return "rejected" if status >= 400 else "ok"


def start(endpoint: str, request_id: str) -> tuple[Trace, contextvars.Token]:
    t = Trace(endpoint, request_id)
    return t, _current.set(t)


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> Trace | None:
    return _current.get()


class _Null:
    def __getattr__(self, _name):
        return self._noop

    @staticmethod
    def _noop(*_args, **_kwargs):
        return None

    @contextlib.contextmanager
    def step(self, _name):
        yield


_NULL = _Null()


def get():
    return _current.get() or _NULL
