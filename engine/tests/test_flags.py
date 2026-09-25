"""FIX_PLAN 2A.4: one env-flag helper, one "off" vocabulary, log-once-and-fall-back.

Every migrated parser keeps its old answer for every valid value; an invalid value now
logs one warning and gives the default instead of being silently read as something.
"""

import logging
import re
from pathlib import Path

import pytest

from app import (
    aenet,
    challenge,
    enrolment,
    flags,
    flash,
    geometry,
    head_sequence,
    liveness,
)

ROOT = Path(__file__).resolve().parents[2]
NAME = "FACETECH_TEST_FLAG"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    flags.reset_warnings()
    yield
    flags.reset_warnings()


def warnings(caplog):
    return [r for r in caplog.records if r.name == flags.LOGGER and r.levelno == logging.WARNING]


# --- the helper --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF ", "False"])
def test_the_off_vocabulary(monkeypatch, value):
    monkeypatch.setenv(NAME, value)
    assert flags.enabled(NAME, True) is False
    assert flags.is_off(value)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " On "])
def test_the_on_vocabulary(monkeypatch, value):
    monkeypatch.setenv(NAME, value)
    assert flags.enabled(NAME, False) is True


def test_unset_or_blank_is_the_default_without_a_warning(monkeypatch, caplog):
    monkeypatch.delenv(NAME, raising=False)
    assert flags.enabled(NAME, True) is True
    assert flags.choice(NAME, ("a", "b"), "b") == "b"
    monkeypatch.setenv(NAME, "  ")
    assert flags.enabled(NAME, False) is False
    assert flags.choice(NAME, ("a", "b"), "a") == "a"
    assert warnings(caplog) == []


def test_an_invalid_value_logs_once_and_falls_back(monkeypatch, caplog):
    monkeypatch.setenv(NAME, "maybe")
    caplog.set_level(logging.WARNING, logger=flags.LOGGER)
    for _ in range(3):
        assert flags.enabled(NAME, True) is True
        assert flags.choice(NAME, ("a", "b"), "b") == "b"
    logged = warnings(caplog)
    assert len(logged) == 1, [r.getMessage() for r in logged]
    assert NAME in logged[0].getMessage() and "maybe" in logged[0].getMessage()
    monkeypatch.setenv(NAME, "perhaps")  # a different bad value is worth its own line
    flags.enabled(NAME, True)
    assert len(warnings(caplog)) == 2


def test_choice_is_case_insensitive_unless_asked(monkeypatch):
    monkeypatch.setenv(NAME, " B ")
    assert flags.choice(NAME, ("a", "b"), "a") == "b"
    assert flags.choice(NAME, ("a", "b"), "a", case_sensitive=True) == "a"


def test_the_default_must_be_allowed():
    with pytest.raises(ValueError):
        flags.choice(NAME, ("a", "b"), "c")


# --- every migrated parser keeps its old answers ------------------------------------------


def _answers(monkeypatch, env, read, values):
    out = {}
    for value in values:
        if value is None:
            monkeypatch.delenv(env, raising=False)
        else:
            monkeypatch.setenv(env, value)
        out[value] = read()
    return out


def test_liveness_enforce(monkeypatch):
    got = _answers(
        monkeypatch,
        liveness.ENFORCE_ENV,
        liveness.enforcement_enabled,
        [None, "", "1", "on", "true", "0", "off", "no", "FALSE", "junk"],
    )
    assert got == {
        None: True, "": True, "1": True, "on": True, "true": True,
        "0": False, "off": False, "no": False, "FALSE": False, "junk": True,
    }  # fmt: skip


def test_flash_mode(monkeypatch):
    got = _answers(
        monkeypatch, flash.MODE_ENV, flash.mode, [None, "log", " ON ", "off", "strict", ""]
    )
    assert got == {None: "off", "log": "log", " ON ": "on", "off": "off", "strict": "off", "": "off"}


def test_geometry_mode_folds_on_into_log(monkeypatch):
    got = _answers(monkeypatch, geometry.MODE_ENV, geometry.mode, [None, "log", "on", "off", "x"])
    assert got == {None: "off", "log": "log", "on": "log", "off": "off", "x": "off"}


def test_aenet_mode(monkeypatch):
    got = _answers(
        monkeypatch,
        aenet.MODE_ENV,
        aenet.mode,
        [None, aenet.LOG, aenet.ON.upper(), "minifas", "aenet"],
    )
    assert got == {
        None: aenet.MINIFAS, aenet.LOG: aenet.LOG, aenet.ON.upper(): aenet.ON,
        "minifas": aenet.MINIFAS, "aenet": aenet.MINIFAS,
    }  # fmt: skip


def test_challenge_policy_stays_case_sensitive(monkeypatch):
    got = _answers(
        monkeypatch,
        challenge.POLICY_ENV,
        challenge.selected_policy,
        [None, " single-turn-v1 ", "single-turn-glow-v1", "Single-Turn-V1", "head-sequence", "x"],
    )
    assert got == {
        None: "head-sequence",
        " single-turn-v1 ": "single-turn-v1",
        "single-turn-glow-v1": "single-turn-glow-v1",
        "Single-Turn-V1": "head-sequence",
        "head-sequence": "head-sequence",
        "x": "head-sequence",
    }


def test_head_gate_policy_stays_case_sensitive(monkeypatch):
    v2, v3 = head_sequence.POLICY_V2_SLOW, head_sequence.POLICY_V3_GUIDED
    got = _answers(
        monkeypatch,
        head_sequence.POLICY_ENV,
        head_sequence.selected_policy,
        [None, v3, " " + v3 + " ", v3.upper(), v2, "x"],
    )
    assert got == {None: v2, v3: v3, " " + v3 + " ": v3, v3.upper(): v2, v2: v2, "x": v2}


def test_diagnostic_header_needs_exactly_1(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from helpers import AUTH

    client = TestClient(app)
    for value, shown in ((None, False), ("1", True), ("0", False), ("true", False), ("on", False)):
        if value is None:
            monkeypatch.delenv("FACETECH_DIAGNOSTIC_HEADER", raising=False)
        else:
            monkeypatch.setenv("FACETECH_DIAGNOSTIC_HEADER", value)
        headers = client.post("/v1/enroll", json={}, headers=AUTH).headers
        assert ("X-Facetech-Decision" in headers) is shown, value


def test_invalid_values_are_logged_by_the_real_parsers(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=flags.LOGGER)
    monkeypatch.setenv(flash.MODE_ENV, "strict")
    monkeypatch.setenv(challenge.POLICY_ENV, "single-turn-v9")
    flash.mode(), flash.mode(), challenge.selected_policy()
    text = [r.getMessage() for r in warnings(caplog)]
    assert len(text) == 2
    assert any("FLASH_CHECK" in t and "strict" in t for t in text)
    assert any("CHALLENGE_POLICY" in t and "single-turn-v9" in t for t in text)


# --- the real bug: PAD_GEOMETRY meant two things ------------------------------------------

UNSURE = {"outcome": "inconclusive", "reason": "turned_frames"}


def test_pad_geometry_on_no_longer_enforces_at_enrolment(monkeypatch):
    """geometry.py documents PAD_GEOMETRY=on as log-only; enrolment used to read the same
    value as enforcing. Enrolment enforcement now has its own flag."""
    monkeypatch.delenv(flash.MODE_ENV, raising=False)
    monkeypatch.delenv(enrolment.ENFORCE_GEOMETRY_ENV, raising=False)
    monkeypatch.setenv(geometry.MODE_ENV, "on")
    assert geometry.mode() == "log"
    decision = enrolment.bar(UNSURE, None)
    assert decision["geometry"]["mode"] == "log"
    assert decision["would_refuse"] == [enrolment.GEOMETRY] and decision["refuse"] == []


def test_enrolment_enforcement_has_its_own_flag(monkeypatch):
    monkeypatch.delenv(flash.MODE_ENV, raising=False)
    monkeypatch.setenv(geometry.MODE_ENV, "log")
    monkeypatch.setenv(enrolment.ENFORCE_GEOMETRY_ENV, "on")
    assert enrolment.bar(UNSURE, None)["refuse"] == [enrolment.GEOMETRY]
    # The nose check itself only runs under PAD_GEOMETRY: without it there is no part to judge.
    monkeypatch.setenv(geometry.MODE_ENV, "off")
    assert enrolment.bar(UNSURE, None) is None


# --- no parser of its own is left ---------------------------------------------------------

MIGRATED = [
    "engine/app/liveness.py",
    "engine/app/main.py",
    "engine/app/flash.py",
    "engine/app/geometry.py",
    "engine/app/enrolment.py",
    "engine/app/aenet.py",
    "engine/app/challenge.py",
    "engine/app/head_sequence.py",
    "engine/tests/model_guard.py",
]


@pytest.mark.parametrize("rel", MIGRATED)
def test_no_module_reads_the_environment_itself(rel):
    source = (ROOT / rel).read_text(encoding="utf-8")
    assert not re.search(r"os\.environ\.get\(|os\.getenv\(", source), rel
