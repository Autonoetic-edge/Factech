import secrets
import threading
import time
from dataclasses import dataclass

NONCE_BYTES = 16

EXPIRY_MS = 30_000

RETAIN_AFTER_EXPIRY_MS = 5 * 60_000

SETTLE_MS_MIN = 900
SETTLE_MS_MAX = 2400

TARGET_DECIMALS = 3

TARGET_STEPS = 25

TARGET_BAND_FRACTION = 0.5

TARGET_ECHO_TOLERANCE = 1e-6

MAX_ISSUED_NONCES = 10_000

LOOK_LEFT = "LOOK_LEFT"
LOOK_RIGHT = "LOOK_RIGHT"

MOVE_CLOSER = "MOVE_CLOSER"

BLINK_TWICE = "BLINK_TWICE"

ACTIONS = (LOOK_LEFT, LOOK_RIGHT, MOVE_CLOSER, BLINK_TWICE)

ISSUABLE_ACTIONS = (MOVE_CLOSER,)

MISSING_NONCE = "missing_nonce"

UNKNOWN_NONCE = "unknown_nonce"

REUSED_NONCE = "reused_nonce"

EXPIRED_NONCE = "expired_nonce"

ACTION_MISMATCH = "action_mismatch"

UNKNOWN_ACTION = "unknown_action"

PARAMS_MISMATCH = "params_mismatch"

EARLY_REFUSAL_REASONS = frozenset({UNKNOWN_NONCE, EXPIRED_NONCE, REUSED_NONCE})

_lock = threading.Lock()

_issued: dict[str, dict] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _evict(now: int) -> None:
    dead = [
        n
        for n, rec in _issued.items()
        if now > rec["expires_ms"] + RETAIN_AFTER_EXPIRY_MS
    ]
    for nonce in dead:
        del _issued[nonce]

    while len(_issued) >= MAX_ISSUED_NONCES:
        _issued.pop(next(iter(_issued)))


def _target_band(action: str) -> tuple[float, float]:
    from app import liveness  # noqa: PLC0415

    if action == MOVE_CLOSER:
        return liveness.SCALE_DELTA_FLOOR, liveness.SCALE_DELTA_LIVE
    return liveness.YAW_DELTA_FLOOR, liveness.YAW_DELTA_LIVE


def _draw_params(action: str) -> dict:
    floor, live = _target_band(action)
    live = floor + (live - floor) * TARGET_BAND_FRACTION
    step = secrets.randbelow(TARGET_STEPS + 1) / TARGET_STEPS
    return {
        "settle_ms": SETTLE_MS_MIN
        + secrets.randbelow(SETTLE_MS_MAX - SETTLE_MS_MIN + 1),
        "target": round(floor + (live - floor) * step, TARGET_DECIMALS),
    }


def _params_match(presented: dict | None, issued: dict) -> bool:
    if not isinstance(presented, dict):
        return False
    settle, target = presented.get("settle_ms"), presented.get("target")
    if isinstance(settle, bool) or not isinstance(settle, int):
        return False
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        return False
    if settle != issued["settle_ms"]:
        return False
    return abs(float(target) - issued["target"]) <= TARGET_ECHO_TOLERANCE


def issue() -> dict:
    now = _now_ms()
    nonce = secrets.token_hex(NONCE_BYTES)
    action = secrets.choice(ISSUABLE_ACTIONS)
    record = {
        "action": action,
        "params": _draw_params(action),
        "issued_ms": now,
        "expires_ms": now + EXPIRY_MS,
        "used": False,
    }
    with _lock:
        _evict(now)
        _issued[nonce] = record
    return {
        "nonce": nonce,
        "action": action,
        "params": dict(record["params"]),
        "issued_ms": record["issued_ms"],
        "expires_ms": record["expires_ms"],
    }


@dataclass(frozen=True)
class ChallengeVerdict:
    ok: bool
    reason: str | None
    action: str | None
    presented_action: str | None

    params: dict | None

    nonce_prefix: str

    consumed: bool

    def as_dict(self) -> dict:
        body = {
            "nonce_ok": self.ok,
            "nonce_prefix": self.nonce_prefix,
            "action": self.action,
            "params": self.params,
        }
        if not self.ok:
            body["reason"] = self.reason
            if self.presented_action != self.action:
                body["presented_action"] = self.presented_action
        return body


def _verdict(
    ok: bool,
    reason: str | None,
    action: str | None,
    presented_action: str | None,
    nonce: str,
    consumed: bool = False,
    params: dict | None = None,
) -> ChallengeVerdict:
    return ChallengeVerdict(
        ok=ok,
        reason=reason,
        action=action,
        presented_action=presented_action,
        params=params,
        nonce_prefix=nonce[:8],
        consumed=consumed,
    )


def _presented(scan: dict) -> tuple[str, str | None, dict | None]:
    raw = scan.get("challenge")
    if not isinstance(raw, dict):
        return "", None, None
    nonce = raw.get("nonce")
    action = raw.get("action")
    params = raw.get("params")
    return (
        nonce.strip() if isinstance(nonce, str) else "",
        action.strip() if isinstance(action, str) else None,
        params if isinstance(params, dict) else None,
    )


def check(
    nonce: str,
    presented_action: str | None,
    *,
    spend: bool,
    presented_params: dict | None = None,
) -> ChallengeVerdict:
    if not nonce:
        return _verdict(False, MISSING_NONCE, None, presented_action, "")
    now = _now_ms()
    with _lock:
        record = _issued.get(nonce)
        if record is None:
            return _verdict(False, UNKNOWN_NONCE, None, presented_action, nonce)

        action, params, expires_ms, already_used = (
            record["action"],
            dict(record["params"]),
            record["expires_ms"],
            record["used"],
        )
        if spend:
            record["used"] = True

    def fail(reason: str) -> ChallengeVerdict:
        return _verdict(False, reason, action, presented_action, nonce, spend, params)

    if already_used:
        return fail(REUSED_NONCE)
    if now > expires_ms:
        return fail(EXPIRED_NONCE)
    if presented_action not in ACTIONS:
        return fail(UNKNOWN_ACTION)
    if presented_action != action:
        return fail(ACTION_MISMATCH)
    if not _params_match(presented_params, params):
        return fail(PARAMS_MISMATCH)
    return _verdict(True, None, action, presented_action, nonce, spend, params)


def consume_from_scan(scan: dict) -> ChallengeVerdict:
    nonce, action, params = _presented(scan)
    return check(nonce, action, spend=True, presented_params=params)


def inspect_from_scan(scan: dict) -> ChallengeVerdict:
    nonce, action, params = _presented(scan)
    return check(nonce, action, spend=False, presented_params=params)


def outstanding() -> int:
    with _lock:
        return len(_issued)


def reset() -> None:
    with _lock:
        _issued.clear()
