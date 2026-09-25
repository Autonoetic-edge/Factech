"""The account gateway issues the engine's policy challenge (LIVENESS_UPGRADE_PLAN Phase 4).

End to end through the real PostgresOperations (issue -> durable store -> nonce claim ->
analysis -> commit) and the real FrozenAnalyzer/engine, with a small in-memory stand-in for
the PostgreSQL connection (no database here; the Postgres-gated suites cover the SQL itself).
Under CHALLENGE_POLICY=single-turn-glow-v1 the challenge carries the engine's own glow
schedule, it is stored with the nonce, enrolment and verification hand it to the engine, and
FLASH_CHECK=log writes the flash record to the decision trace. Frames are synthetic and
non-biometric (engine/tests/test_flash_glow.py). CHALLENGE_POLICY unset = today's challenge.
"""

import asyncio
import base64
import copy
import hashlib
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from facetech_auth import schema as s
from facetech_auth.encryption import DataCipher
from facetech_auth.inference import FrozenAnalyzer
from facetech_auth.operations import PostgresOperations
from facetech_auth.policy import Action
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine" / "tests"))

from helpers import build_scan, scan_to_b64  # noqa: E402
from test_flash_glow import CADENCE, FACE, jpeg, render  # noqa: E402
from test_secure_pad import vector  # noqa: E402

NOW = 1_758_700_000.0


class Store:
    """Just enough of the face_auth tables for one subject's challenge/scan/commit path."""

    def __init__(self):
        self.challenges, self.operations, self.templates = {}, {}, []

    def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        p, table = (
            compiled.params,
            statement.table if hasattr(statement, "table") else None,
        )
        rows = []
        if statement.is_insert and table is s.challenges:
            self.challenges[p["digest"]] = dict(p)
        elif statement.is_insert and table is s.operations:
            self.operations[p["id"]] = dict(p)
        elif statement.is_insert and table is s.templates:
            self.templates.append(dict(p))
        elif statement.is_update and table is s.challenges:  # spend_nonce
            found = self.challenges.get(p["digest_1"])
            if (
                found
                and not found["consumed"]
                and found["expires_at"] >= p["expires_at_1"]
            ):
                found["consumed"] = True
                rows = [{"parameters": copy.deepcopy(found["parameters"]),
                         "issued_at": found["issued_at"]}]  # fmt: skip
        elif statement.is_update and table is s.operations:
            self.operations.get(p["id_1"], {}).update(state=p["state"])
        elif statement.is_select:
            text = str(compiled)
            if "count(" in text:
                return Result(
                    [{"count": len(self.templates)}], scalar=len(self.templates)
                )
            if "FROM face_auth.consents" in text:
                rows = [{"granted": True, "actor_id": "actor-alice", "revision": 1,
                         "text_version": "template-authentication-v1"}]  # fmt: skip
            elif "FROM face_auth.templates" in text:
                rows = [dict(t) for t in self.templates]
            elif "FROM face_auth.operations" in text and "id_1" in p:
                rows = [dict(self.operations[p["id_1"]])]
        return Result(rows)


class Result:
    def __init__(self, rows, scalar=0):
        self.rows, self.scalar = rows, scalar

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)

    def scalar_one(self):
        return self.scalar


class Repo:
    def __init__(self):
        self.store, self.now = Store(), NOW

    def clock(self):
        return self.now

    async def run(self, fn):
        return fn(self.store)

    def guard(self, connection, access, kind):
        return SimpleNamespace(id="subject-alice", generation=1)

    def tenant_lock(self, connection, tenant):
        pass

    @staticmethod
    def append_audit(connection, event):
        pass


def access(action):
    return SimpleNamespace(
        action=action,
        request_id="r" * 16,
        target=SimpleNamespace(id="subject-alice", kind="subject"),
        auth=SimpleNamespace(
            principal=SimpleNamespace(tenant_id="amfatec", actor_id="actor-alice"),
            session=SimpleNamespace(id="session-1"),
        ),
    )


def execute(ops, action, payload=None):
    if payload is not None:
        ops.repo.now += 7  # the scan arrives after its 5.5 s capture, as on a phone
    a = access(action)

    async def reauthorize():
        return a

    return asyncio.run(ops.execute(a, payload or {}, reauthorize))


def glow_scan(issued):
    """What the page's SDK sends: frames lit by the issued schedule, the challenge echo
    WITHOUT the glow key (the SDK keeps only nonce/action/params)."""
    jpegs = [
        jpeg(render(i * CADENCE, issued["glow"], seed=i)) for i in range(12)
    ]  # fmt: skip
    scan = build_scan(jpegs, ts_ms=[5000 + i * CADENCE for i in range(12)])
    echo = {k: v for k, v in issued.items() if k != "glow"}
    scan["challenge"] = echo
    raw = base64.b64decode(scan_to_b64(scan))
    return {
        "scan": {"challenge": echo},
        "scan_bytes": raw,
        "idempotency_key": secrets.token_hex(16),
        "scan_digest": hashlib.sha256(raw).hexdigest(),
    }


@pytest.fixture
def account(monkeypatch):
    analyzer = FrozenAnalyzer(ROOT / "engine", warm=False)
    main, pad, liveness = analyzer.main, analyzer.pad, analyzer.main.liveness
    monkeypatch.setattr(
        main, "_scan_detections", lambda s_: [[dict(FACE)] for _ in range(12)]
    )
    monkeypatch.setattr(
        pad, "evaluate",
        lambda *a: ({"outcome": "live", "usable": 12, "minimum_detection_score": 0.99}, vector()),
    )  # fmt: skip
    good = liveness.LivenessResult(
        0.8, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *a, **k: good)
    monkeypatch.setattr(
        main, "_embed_selected", lambda selected: (vector(), len(selected))
    )
    lines = []
    monkeypatch.setattr(
        analyzer.trace.Trace,
        "emit",
        lambda self, status: lines.append(self.line(status)),
    )
    for name in ("PAD_GEOMETRY", "PAD_ENSEMBLE"):
        monkeypatch.delenv(name, raising=False)
    ops = PostgresOperations(
        Repo(), DataCipher(secrets.token_bytes(32)), analyzer=analyzer,
        template_expires_at=int(NOW) + 3600,
    )  # fmt: skip
    return SimpleNamespace(
        ops=ops, store=ops.repo.store, lines=lines, analyzer=analyzer
    )


def test_glow_challenge_is_issued_stored_and_reaches_enrol_and_verify(
    monkeypatch, account
):
    monkeypatch.setenv("CHALLENGE_POLICY", "single-turn-glow-v1")
    monkeypatch.setenv("FLASH_CHECK", "log")
    ch = account.analyzer.challenge

    issued = execute(account.ops, "challenge.enroll")
    assert issued["action"] in ch.SINGLE_TURN_ACTIONS
    lo, hi = ch.SINGLE_TURN_SETTLE_MS_MIN, ch.SINGLE_TURN_SETTLE_MS_MAX
    assert set(issued["params"]) == {"settle_ms", "target"}
    assert lo <= issued["params"]["settle_ms"] <= hi
    glow = issued["glow"]
    assert (
        glow["version"] == account.analyzer.main.flash.VERSION
        and 3 <= len(glow["steps"]) <= 4
    )
    # bound: stored with the nonce, exactly as issued
    (stored,) = account.store.challenges.values()
    assert stored["parameters"] == {k: issued[k] for k in ("action", "params", "glow")}

    enrolled = execute(account.ops, Action.ENROLL, glow_scan(issued))
    assert enrolled["accepted"] is True and enrolled["template_id"], enrolled
    record = account.lines[-1]["flash"]
    assert record["mode"] == "log" and record["enforced"] is False
    assert record["outcome"] == "pass", record  # the engine had the stored schedule
    assert record["schedule"] == account.analyzer.main.flash._summary(glow)

    issued = execute(account.ops, "challenge.verify")
    assert issued["glow"] != glow  # a fresh draw per challenge
    verified = execute(account.ops, Action.VERIFY, glow_scan(issued))
    assert verified["accepted"] is True and verified["decision"]["match"] is True
    assert account.lines[-1]["flash"]["outcome"] == "pass"
    assert account.lines[-1]["flash"][
        "schedule"
    ] == account.analyzer.main.flash._summary(issued["glow"])


def test_a_client_glow_is_never_used_only_the_stored_one(monkeypatch, account):
    monkeypatch.setenv("CHALLENGE_POLICY", "single-turn-glow-v1")
    monkeypatch.setenv("FLASH_CHECK", "log")
    issued = execute(account.ops, "challenge.enroll")
    payload = glow_scan(issued)
    payload["scan"]["challenge"]["glow"] = {"steps": []}  # tampered echo: ignored
    execute(account.ops, Action.ENROLL, payload)
    assert account.lines[-1]["flash"][
        "schedule"
    ] == account.analyzer.main.flash._summary(issued["glow"])


def test_single_turn_without_glow(monkeypatch, account):
    monkeypatch.setenv("CHALLENGE_POLICY", "single-turn-v1")
    issued = execute(account.ops, "challenge.enroll")
    assert issued["action"] in account.analyzer.challenge.SINGLE_TURN_ACTIONS
    assert "glow" not in issued
    (stored,) = account.store.challenges.values()
    assert set(stored["parameters"]) == {"action", "params"}


@pytest.mark.parametrize("policy", [None, "head-sequence", "bogus"])
def test_policy_unset_keeps_todays_head_sequence(monkeypatch, account, policy):
    if policy is None:
        monkeypatch.delenv("CHALLENGE_POLICY", raising=False)
    else:
        monkeypatch.setenv("CHALLENGE_POLICY", policy)
    issued = execute(account.ops, "challenge.enroll")
    assert issued["action"] == "HEAD_SEQUENCE" and "glow" not in issued
    assert issued["params"]["settle_ms"] == 3200 and issued["params"]["target"] == 0.18
    assert issued["params"]["switch_ms"] in (9000, 9200, 9400)
    assert account.analyzer.challenge_parameters() is None
    assert issued["nonce"] not in account.analyzer.challenge._issued


def test_drawing_never_touches_the_engines_nonce_table(monkeypatch, account):
    monkeypatch.setenv("CHALLENGE_POLICY", "single-turn-glow-v1")
    before = dict(account.analyzer.challenge._issued)
    for _ in range(5):
        account.analyzer.challenge_parameters()
    assert account.analyzer.challenge._issued == before
