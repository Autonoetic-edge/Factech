"""Environment flags, read one way (FIX_PLAN 2A.4).

`enabled(name, default)` reads an on/off flag and `choice(name, allowed, default)` a
flag with named values. Unset or blank gives the default. Surrounding whitespace is
ignored. An invalid value logs one warning (per flag and value) and gives the default:
it never raises, and it is never quietly read as some other setting.
"""

import logging
import os
import threading

LOGGER = "facetech.flags"
log = logging.getLogger(LOGGER)

# The one "off" vocabulary, and its "on" counterpart.
OFF_VALUES = frozenset({"0", "false", "no", "off"})
ON_VALUES = frozenset({"1", "true", "yes", "on"})

_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


def is_off(value: str) -> bool:
    return value.strip().lower() in OFF_VALUES


def _read(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _invalid(name: str, value: str, expected, default) -> None:
    with _warned_lock:
        if (name, value) in _warned:
            return
        _warned.add((name, value))
    log.warning(
        "ignoring %s=%r: expected one of %s; using the default %r",
        name,
        value,
        ", ".join(sorted(expected)),
        default,
    )


def reset_warnings() -> None:
    """Forget which invalid values were already logged (tests)."""
    with _warned_lock:
        _warned.clear()


def enabled(name: str, default: bool) -> bool:
    value = _read(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in OFF_VALUES:
        return False
    if lowered in ON_VALUES:
        return True
    _invalid(name, value, OFF_VALUES | ON_VALUES, default)
    return default


def choice(name: str, allowed, default: str, *, case_sensitive: bool = False) -> str:
    allowed = tuple(allowed)
    if default not in allowed:
        raise ValueError(f"default {default!r} is not one of {allowed}")
    value = _read(name)
    if value is None:
        return default
    key = value if case_sensitive else value.lower()
    if key in allowed:
        return key
    _invalid(name, value, allowed, default)
    return default
