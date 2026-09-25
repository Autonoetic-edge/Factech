import copy
import secrets
import threading
import time
from dataclasses import dataclass

from app import flags
from app.constants import CHALLENGE_EXPIRY_MS

NONCE_BYTES = 16

EXPIRY_MS = CHALLENGE_EXPIRY_MS

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
HEAD_SEQUENCE = "HEAD_SEQUENCE"

BLINK_TWICE = "BLINK_TWICE"

ACTIONS = (LOOK_LEFT, LOOK_RIGHT, MOVE_CLOSER, BLINK_TWICE, HEAD_SEQUENCE)

ISSUABLE_ACTIONS = (HEAD_SEQUENCE,)

# CHALLENGE_POLICY selects what issue() hands out. Anything but an opt-in name is
# today's HEAD_SEQUENCE, unchanged. See docs/LIVENESS_UPGRADE_PLAN.md Phase 1.
POLICY_ENV = "CHALLENGE_POLICY"
POLICY_HEAD_SEQUENCE = "head-sequence"
POLICY_SINGLE_TURN_V1 = "single-turn-v1"
# Phase 4: the same single turn, plus a soft colour glow schedule bound to the nonce.
POLICY_SINGLE_TURN_GLOW_V1 = "single-turn-glow-v1"
SINGLE_TURN_POLICIES = (POLICY_SINGLE_TURN_V1, POLICY_SINGLE_TURN_GLOW_V1)
SINGLE_TURN_ACTIONS = (LOOK_LEFT, LOOK_RIGHT)

# single-turn-v1 hold before the turn: >=3 settle frames at 500 ms, predictable hold.
SINGLE_TURN_SETTLE_MS_MIN = 1200
SINGLE_TURN_SETTLE_MS_MAX = 2000

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


def selected_policy() -> str:
    return flags.choice(
        POLICY_ENV,
        (POLICY_HEAD_SEQUENCE, *SINGLE_TURN_POLICIES),
        POLICY_HEAD_SEQUENCE,
        case_sensitive=True,
    )


def issuable_actions() -> tuple[str, ...]:
    if selected_policy() in SINGLE_TURN_POLICIES:
        return SINGLE_TURN_ACTIONS
    return ISSUABLE_ACTIONS


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
    if action == HEAD_SEQUENCE:
        return {
            "settle_ms": 3200,
            "switch_ms": 9000 + secrets.randbelow(3) * 200,
            "first_sign": secrets.choice((-1, 1)),
            "target": 0.18,
        }
    floor, live = _target_band(action)
    live = floor + (live - floor) * TARGET_BAND_FRACTION
    step = secrets.randbelow(TARGET_STEPS + 1) / TARGET_STEPS
    low, high = (
        (SINGLE_TURN_SETTLE_MS_MIN, SINGLE_TURN_SETTLE_MS_MAX)
        if action in SINGLE_TURN_ACTIONS
        else (SETTLE_MS_MIN, SETTLE_MS_MAX)
    )
    return {
        "settle_ms": low + secrets.randbelow(high - low + 1),
        "target": round(floor + (live - floor) * step, TARGET_DECIMALS),
    }


def _params_match(presented: dict | None, issued: dict) -> bool:
    if not isinstance(presented, dict):
        return False
    if "first_sign" in issued:
        return set(presented) == set(issued) and all(
            type(presented[k]) in (int, float) and presented[k] == v
            for k, v in issued.items()
        )
    settle, target = presented.get("settle_ms"), presented.get("target")
    if isinstance(settle, bool) or not isinstance(settle, int):
        return False
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        return False
    if settle != issued["settle_ms"]:
        return False
    return abs(float(target) - issued["target"]) <= TARGET_ECHO_TOLERANCE


def _draw_glow() -> dict | None:
    if selected_policy() != POLICY_SINGLE_TURN_GLOW_V1:
        return None
    from app import flash  # noqa: PLC0415

    return flash.draw_schedule()


def issue(operation: str = "liveness", user_id: str = "") -> dict:
    now = _now_ms()
    nonce = secrets.token_hex(NONCE_BYTES)
    action = secrets.choice(issuable_actions())
    glow = _draw_glow() if action in SINGLE_TURN_ACTIONS else None
    record = {
        "action": action,
        "params": _draw_params(action),
        "issued_ms": now,
        "expires_ms": now + EXPIRY_MS,
        "used": False,
        "binding": (operation, user_id),
    }
    if glow is not None:
        record["glow"] = glow
    with _lock:
        _evict(now)
        _issued[nonce] = record
    issued = {
        "nonce": nonce,
        "action": action,
        "params": dict(record["params"]),
        "issued_ms": record["issued_ms"],
        "expires_ms": record["expires_ms"],
    }
    if glow is not None:
        issued["glow"] = copy.deepcopy(glow)
    return issued


@dataclass(frozen=True)
class ChallengeVerdict:
    ok: bool
    reason: str | None
    action: str | None
    presented_action: str | None

    params: dict | None

    nonce_prefix: str

    consumed: bool

    # Phase 4 glow schedule as issued (engine copy, never the client's echo); else None.
    glow: dict | None = None

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
    glow: dict | None = None,
) -> ChallengeVerdict:
    return ChallengeVerdict(
        ok=ok,
        reason=reason,
        action=action,
        presented_action=presented_action,
        params=params,
        nonce_prefix=nonce[:8],
        consumed=consumed,
        glow=glow,
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
    binding: tuple[str, str] | None = None,
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
        glow = copy.deepcopy(record["glow"]) if "glow" in record else None
        if spend:
            record["used"] = True

    def fail(reason: str) -> ChallengeVerdict:
        return _verdict(False, reason, action, presented_action, nonce, spend, params)

    if already_used:
        return fail(REUSED_NONCE)
    if now > expires_ms:
        return fail(EXPIRED_NONCE)
    if binding is not None and record.get("binding") != binding:
        return fail("binding_mismatch")
    if presented_action not in ACTIONS:
        return fail(UNKNOWN_ACTION)
    if presented_action != action:
        return fail(ACTION_MISMATCH)
    if not _params_match(presented_params, params):
        return fail(PARAMS_MISMATCH)
    return _verdict(True, None, action, presented_action, nonce, spend, params, glow)


def consume_from_scan(
    scan: dict, binding: tuple[str, str] | None = None
) -> ChallengeVerdict:
    nonce, action, params = _presented(scan)
    verdict = check(nonce, action, spend=True, presented_params=params, binding=binding)
    if verdict.ok and binding is not None:
        with _lock:
            age = _now_ms() - _issued[nonce]["issued_ms"]
        frames = scan.get("frames", [])
        span = frames[-1]["ts_ms"] - frames[0]["ts_ms"] if frames else 0
        if age + 250 < span:
            return _verdict(
                False, "capture_before_issue", action, action, nonce, True, params
            )
    return verdict


def inspect_from_scan(scan: dict) -> ChallengeVerdict:
    nonce, action, params = _presented(scan)
    return check(nonce, action, spend=False, presented_params=params)


def outstanding() -> int:
    with _lock:
        return len(_issued)


def reset() -> None:
    with _lock:
        _issued.clear()
