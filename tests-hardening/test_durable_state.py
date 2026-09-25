"""D01-D08 durable state through the frozen pipeline, owned PostgreSQL only.

Model outputs are mocked exactly as engine/tests/test_secure_pad.wired_client so
that the unchanged PAD aggregation, continuity, echo, match and persistence
paths run without any biometric image. One test runs the real ONNX models on
uniform synthetic pixels to show the frozen enforcement path, not efficacy.
"""

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from auth_fixtures import run, unlimited
from facetech_auth import schema as s
from facetech_auth.contracts import Denied, Unavailable
from facetech_auth.evaluation_store import EvaluationStore
from facetech_auth.http import create_engine, create_gateway
from facetech_auth.inference import FrozenAnalyzer
from facetech_auth.oidc import Provider
from facetech_auth.operations import MAX_TEMPLATES, PostgresOperations
from facetech_auth.postgres import PostgresRepository, migrate, recover
from facetech_auth.provision import MAX_SUBJECTS
from facetech_auth.sessions import Sessions
from postgres_fixtures import PersistentHarness as _PersistentHarness
from postgres_fixtures import local_secrets, local_url, prepare_databases

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/tests"))
from app import anti_spoof as pad  # noqa: E402
from app import challenge as challenge_mod  # noqa: E402
from app import liveness, main, store  # noqa: E402
from helpers import build_scan, scan_to_msgpack  # noqa: E402

PG_BIN = ROOT / ".hardening-runtime/bin/pgsql/bin"
PROBE = ROOT / "tests-hardening/durable_probe.py"


def vector(index=0):
    v = np.zeros(512, dtype=np.float32)
    v[index] = 1
    return v


def detection(offset=0):
    return {
        "bbox": [10, 10, 54, 58],
        "det_score": 0.99,
        "landmarks_5": [[20, 24], [44, 24], [32 + offset, 35], [25, 46], [39, 46]],
    }


class FakePAD:
    def __init__(self, scores=(0.01, 0.98, 0.01)):
        self.scores = np.asarray(scores)

    def predict(self, image, bbox):
        return [self.scores, self.scores], self.scores


class Counting:
    """Call counter and hook around the frozen analyzer; never changes results."""

    def __init__(self, inner):
        self.inner, self.calls, self.hook = inner, 0, None
        self.format_id, self.admit = inner.format_id, inner.admit

    async def analyze(self, *args, **kwargs):
        self.calls += 1
        if self.hook:
            await self.hook()
        return await self.inner.analyze(*args, **kwargs)


@pytest.fixture(scope="module")
def databases(request):
    if not request.config.getoption("--hardening-postgres"):
        pytest.skip(
            "Explicit --hardening-postgres required; no default database access"
        )
    owner, evaluation = prepare_databases()
    yield owner, evaluation
    owner.dispose()
    evaluation.dispose()


@pytest.fixture(scope="module")
def frozen(databases):
    return FrozenAnalyzer(ROOT / "engine")


@pytest.fixture
def h(databases, frozen):
    harness = run(PersistentHarness().initialize(*databases, Counting(frozen)))
    yield harness
    harness.close()


@pytest.fixture
def wired(monkeypatch):
    """engine/tests/test_secure_pad.wired_client: real wiring, fake model outputs."""
    monkeypatch.setattr(pad, "get_pad", lambda: FakePAD())
    monkeypatch.setattr(
        main, "_scan_detections", lambda s: [[detection()] for _ in range(12)]
    )
    monkeypatch.setattr(main, "embed_aligned", lambda a: vector())
    monkeypatch.setattr(
        main, "align_largest_face", lambda *a, **k: np.zeros((112, 112, 3), np.uint8)
    )
    good = liveness.LivenessResult(
        0.8, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *a: good)


def frozen_scan(challenge, shift=0):
    scan = build_scan(
        challenge={k: challenge[k] for k in ("nonce", "action", "params")}
    )
    for i, frame in enumerate(scan["frames"]):
        frame["ts_ms"] = shift + i * 1400
    return scan_to_msgpack(scan)


class PersistentHarness(_PersistentHarness):
    async def frozen(self, browser, challenge, *, advance=True, **kwargs):
        # A real capture lasts about 16 s; the frozen capture-timing rule needs
        # the nonce age to cover the frame span. Still inside the 30 s TTL.
        if advance:
            self.now += 16
        return await self.scan(browser, challenge, raw=frozen_scan(challenge), **kwargs)

    async def enrolled(self, name="alice"):
        browser = await self.login(name)
        assert (await self.consent(browser, name)).status_code == 200
        response = await self.frozen(
            browser, await self.challenge(browser, name), name=name
        )
        assert response.status_code == 200, response.text
        return browser, response.json()

    def templates(self, name="alice", active=True):
        with self.owner.engine.connect() as connection:
            query = sa.select(s.templates).where(
                s.templates.c.subject_id == self.subjects[name]
            )
            if active:
                query = query.where(s.templates.c.active.is_(True))
            return [dict(v) for v in connection.execute(query).mappings()]

    def operation(self, operation_id):
        with self.owner.engine.connect() as connection:
            return dict(
                connection.execute(
                    sa.select(s.operations).where(s.operations.c.id == operation_id)
                )
                .mappings()
                .one()
            )

    def restart(self):
        """Fresh pools, sessions, operations and apps over the same database."""
        self.close()
        clock = lambda: self.now  # noqa: E731
        self.repo = PostgresRepository(local_url(), clock=clock)
        self.other_repo = PostgresRepository(local_url(), clock=clock)
        self.evaluation_repo = PostgresRepository(
            local_url("facetech_eval"), clock=clock
        )
        self.engine_sessions = Sessions(
            self.other_repo,
            Provider(self.config, transport=self.transport, clock=clock),
            self.vault,
            clock=clock,
        )
        self.gateway_sessions = Sessions(
            self.repo,
            Provider(self.config, transport=self.transport, clock=clock),
            self.vault,
            clock=clock,
        )
        self.engine_ops = PostgresOperations(
            self.other_repo,
            self.cipher,
            analyzer=self.analyzer,
            template_expires_at=int(self.now) + 3600,
        )
        self.gateway_ops = PostgresOperations(
            self.repo,
            self.cipher,
            evaluation=EvaluationStore(self.evaluation_repo, self.evaluation_cipher),
        )
        self.limits = unlimited()
        self.engine = create_engine(
            self.engine_sessions, operations=self.engine_ops, limits=self.limits
        )
        self.gateway = create_gateway(
            self.gateway_sessions,
            operations=self.gateway_ops,
            engine_transport=httpx.ASGITransport(app=self.engine),
            limits=self.limits,
        )


def probe(request):
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def pg(tool, *args, database=None):
    environment = {**os.environ, "PGPASSWORD": local_secrets()["pg_password"]}
    completed = subprocess.run(
        [
            str(PG_BIN / f"{tool}.exe"),
            "-h",
            "127.0.0.1",
            "-p",
            "15432",
            "-U",
            "facetech_test",
            *args,
        ],
        env=environment,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return completed.stdout


def fresh_database(name):
    import psycopg

    with psycopg.connect(
        host="127.0.0.1",
        port=15432,
        user="facetech_test",
        password=local_secrets()["pg_password"],
        dbname="postgres",
        autocommit=True,
    ) as connection:
        connection.execute(
            psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                psycopg.sql.Identifier(name)
            )
        )
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
        )


# ---------------------------------------------------------------- D01 -------


def test_d01_frozen_pipeline_persists_model_bound_templates_across_restart(h, wired):
    async def scenario():
        browser, enrolled = await h.enrolled()
        assert enrolled["accepted"] and enrolled["operation"] == "enroll"
        assert enrolled["decision"]["pad"] == {
            "outcome": "live",
            "policy": pad.POLICY_VERSION,
        }
        assert enrolled["decision"]["quality"]["frames_embedded"] == 12
        assert enrolled["decision"]["liveness"]["enforced"] is True
        [record] = h.templates()
        assert record["id"] == enrolled["template_id"]
        assert record["model_fingerprint"] == h.analyzer.format_id == record["format"]
        assert record["owner_actor_id"] == h.actors["alice"].id
        assert record["key_version"] == "v1" and record["revoked_at"] is None
        stored = np.frombuffer(
            h.cipher.open(
                record["tenant"], "template", record["id"], record["ciphertext"]
            ),
            dtype="<f4",
        )
        assert np.array_equal(stored, vector()) and stored.dtype == np.float32
        # Restart: every pool, session cache and operation object is new.
        h.restart()
        listed = await h.request(
            "GET", f"/v2/subjects/{h.subjects['alice']}/templates", browser, engine=True
        )
        assert listed.status_code == 200 and listed.json()["templates"] == [
            {
                "id": record["id"],
                "created_at": record["created_at"],
                "model_fingerprint": h.analyzer.format_id,
            }
        ]
        verified = await h.frozen(
            browser, await h.challenge(browser, operation="verify"), operation="verify"
        )
        assert verified.status_code == 200, verified.text
        decision = verified.json()["decision"]
        assert verified.json()["accepted"] and decision["match"] is True
        assert decision["score"] == pytest.approx(1.0) and decision["threshold"] == 0.55
        alive = await h.frozen(
            browser,
            await h.challenge(browser, operation="liveness"),
            operation="liveness",
        )
        assert alive.status_code == 200 and alive.json()["accepted"]
        assert alive.json()["decision"]["enforced"] is True
        # A genuinely separate process sees the same record and fingerprint.
        assert probe({"command": "fingerprint"})["format_id"] == h.analyzer.format_id
        other = probe(
            {
                "command": "templates",
                "tenant": h.actors["alice"].tenant,
                "subject": h.subjects["alice"],
            }
        )
        assert other["templates"] == [
            {"id": record["id"], "model_fingerprint": h.analyzer.format_id}
        ]

    run(scenario())


def test_d01_fingerprint_is_bound_to_verified_model_bytes(frozen, tmp_path):
    assert frozen.format_id.startswith("arcface-512-float32-le/")
    assert FrozenAnalyzer(ROOT / "engine", warm=False).format_id == frozen.format_id
    # A copy with one altered model byte is refused before any template is read.
    copy = tmp_path / "engine"
    (copy / "models/anti_spoof").mkdir(parents=True)
    (copy / "scripts").mkdir()
    for name in ("scripts/download_models.py", "models/anti_spoof/manifest.json"):
        (copy / name).write_bytes((ROOT / "engine" / name).read_bytes())
    for name in ("det_500m.onnx", "w600k_r50.onnx"):
        shutil.copyfile(ROOT / "engine/models" / name, copy / "models" / name)
    for model in json.loads(
        (ROOT / "engine/models/anti_spoof/manifest.json").read_text("utf-8")
    )["models"]:
        source = ROOT / "engine/models/anti_spoof" / model["onnx"]
        (copy / "models/anti_spoof" / model["onnx"]).write_bytes(
            source.read_bytes()[:-1] + b"\x00"
        )
    with pytest.raises(Unavailable):
        FrozenAnalyzer.fingerprint(frozen, copy)


def test_parity_with_legacy_engine_decision_content(h, wired):
    async def scenario():
        browser, enrolled = await h.enrolled()
        challenge = await h.challenge(browser, operation="verify")
        hardened = await h.frozen(browser, challenge, operation="verify")
        assert hardened.status_code == 200
        # Same bytes through the unchanged legacy in-memory path.
        store.reset()
        store.enroll("parity", vector())
        legacy = challenge_mod.issue("verify", "parity")
        challenge_mod._issued[legacy["nonce"]]["issued_ms"] -= 16000
        legacy_scan = build_scan(challenge=legacy)
        for i, frame in enumerate(legacy_scan["frames"]):
            frame["ts_ms"] = i * 1400
        response = main._verify_scan(
            store.get_templates("parity"), legacy_scan, "parity"
        )
        store.reset()
        assert response.status_code == 200
        assert json.loads(response.body) == hardened.json()["decision"]

    run(scenario())


def test_real_models_reject_synthetic_pixels_with_frozen_codes(h):
    # Actual ONNX on uniform synthetic frames: enforcement path only, no efficacy.
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        response = await h.frozen(browser, challenge)
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "NO_FACE"
        assert h.templates() == []
        # Legacy engine, same bytes, same verdict.
        legacy = challenge_mod.issue("enroll", "parity")
        challenge_mod._issued[legacy["nonce"]]["issued_ms"] -= 16000
        legacy_scan = build_scan(challenge=legacy)
        for i, frame in enumerate(legacy_scan["frames"]):
            frame["ts_ms"] = i * 1400
        failure, _, _ = main._secure_analysis(legacy_scan, "enroll", "parity")
        assert json.loads(failure.body)["error"]["code"] == "NO_FACE"
        assert (
            json.loads(failure.body)["error"]["message"] == response.json()["message"]
        )
        # Spent nonce; the rejected decision is the operation's recorded outcome.
        again = await h.frozen(browser, challenge, advance=False)
        assert again.status_code == 409 and again.json()["error"] == "CHALLENGE_INVALID"

    run(scenario())


# ---------------------------------------------------------------- D02 -------


def test_d02_template_capacity_is_explicit_without_eviction(h, wired):
    async def scenario():
        browser, first = await h.enrolled()
        for _ in range(MAX_TEMPLATES - 1):
            added = await h.frozen(
                browser, await h.challenge(browser), operation="templates"
            )
            assert added.status_code == 200, added.text
        assert len(h.templates()) == MAX_TEMPLATES
        calls = h.analyzer.calls
        sixth = await h.frozen(browser, await h.challenge(browser))
        assert sixth.status_code == 409 and sixth.json()["error"] == "CAPACITY_EXCEEDED"
        assert h.analyzer.calls == calls  # refused before inference
        ids = {t["id"] for t in h.templates()}
        assert first["template_id"] in ids and len(ids) == MAX_TEMPLATES
        verified = await h.frozen(
            browser, await h.challenge(browser, operation="verify"), operation="verify"
        )
        assert verified.status_code == 200 and verified.json()["accepted"]

    run(scenario())


def test_d02_concurrent_additions_at_subject_boundary_admit_exactly_one(h, wired):
    async def scenario():
        browser, _ = await h.enrolled()
        for _ in range(MAX_TEMPLATES - 2):
            assert (
                await h.frozen(
                    browser, await h.challenge(browser), operation="templates"
                )
            ).status_code == 200
        challenges = [await h.challenge(browser) for _ in range(8)]
        h.now += 16
        results = await asyncio.gather(
            *(
                h.scan(
                    browser,
                    c,
                    raw=frozen_scan(c),
                    operation="templates",
                    engine=bool(i % 2),
                )
                for i, c in enumerate(challenges)
            )
        )
        codes = [r.status_code for r in results]
        assert codes.count(200) == 1, codes
        assert {r.json()["error"] for r in results if r.status_code != 200} <= {
            "CAPACITY_EXCEEDED",
            "BUSY",
        }
        assert len(h.templates()) == MAX_TEMPLATES
        # BUSY (engine admission bound) kept its nonce: a retry now reaches the
        # explicit capacity error instead of a spent-nonce error.
        for challenge, response in zip(challenges, results):
            if response.status_code == 503:
                retry = await h.scan(
                    browser,
                    challenge,
                    raw=frozen_scan(challenge),
                    operation="templates",
                )
                assert retry.status_code == 409
                assert retry.json()["error"] == "CAPACITY_EXCEEDED"

    run(scenario())


def filler_subjects(h, tenant, count, label):
    """Bulk synthetic subjects (no login, no templates) in one transaction."""
    filler = [label + secrets.token_hex(8) for _ in range(count)]
    with h.owner.engine.begin() as connection:
        connection.execute(
            s.actors.insert(),
            [
                {
                    "id": "filler-" + f,
                    "issuer": h.config.issuer,
                    "sub": "filler-" + f,
                    "tenant": tenant,
                    "roles": ["participant"],
                    "epoch": 0,
                    "active": True,
                }
                for f in filler
            ],
        )
        connection.execute(
            s.resources.insert(),
            [
                {
                    "tenant": tenant,
                    "kind": "subject",
                    "id": "subject-" + f,
                    "owner_actor_id": "filler-" + f,
                    "generation": 0,
                    "active": True,
                }
                for f in filler
            ],
        )
    return filler


def test_d02_team_capacity_at_first_enrollment_and_provisioning(h, wired):
    tenant = h.actors["alice"].tenant
    filler = filler_subjects(h, tenant, MAX_SUBJECTS - 1, "e")
    with h.owner.engine.begin() as connection:
        connection.execute(
            s.templates.insert(),
            [
                {
                    "id": "template-" + f,
                    "tenant": tenant,
                    "subject_id": "subject-" + f,
                    "owner_actor_id": "filler-" + f,
                    "created_at": h.now,
                    "active": True,
                    "ciphertext": b"filler",
                    "format": h.analyzer.format_id,
                    "model_fingerprint": h.analyzer.format_id,
                    "key_version": "v1",
                }
                for f in filler
            ],
        )

    async def scenario():
        alice, bob = await h.login(), await h.login("bob")
        await h.consent(alice)
        await h.consent(bob, "bob")
        first, second = await h.challenge(alice), await h.challenge(bob, "bob")
        h.now += 16
        results = await asyncio.gather(
            h.scan(alice, first, raw=frozen_scan(first)),
            h.scan(bob, second, raw=frozen_scan(second), name="bob"),
        )
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409], [r.text for r in results]
        assert [r.json()["error"] for r in results if r.status_code == 409] == [
            "CAPACITY_EXCEEDED"
        ]
        assert len(h.templates()) + len(h.templates("bob")) == 1
        # Provisioning the 1,001st active subject of a team is refused, never evicted.
        capped = h.namespace + "-cap"
        filler_subjects(h, capped, MAX_SUBJECTS - 1, "p")
        outcomes = await asyncio.gather(
            *(
                h.provisioner.create_account(
                    h.config.issuer,
                    "late-" + secrets.token_hex(4),
                    capped,
                    ["participant"],
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(o, Denied) for o in outcomes) == 1
        assert all(
            o.code == "CAPACITY_EXCEEDED" for o in outcomes if isinstance(o, Denied)
        )

    run(scenario())


def test_d02_comparison_is_max_cosine_over_stored_vectors(h, wired, monkeypatch):
    async def scenario():
        browser, _ = await h.enrolled()
        monkeypatch.setattr(main, "embed_aligned", lambda a: vector(1))
        added = await h.frozen(
            browser, await h.challenge(browser), operation="templates"
        )
        assert added.status_code == 200
        verified = await h.frozen(
            browser, await h.challenge(browser, operation="verify"), operation="verify"
        )
        assert verified.status_code == 200 and verified.json()["accepted"]
        assert verified.json()["decision"]["score"] == pytest.approx(1.0)
        monkeypatch.setattr(main, "embed_aligned", lambda a: vector(2))
        rejected = await h.frozen(
            browser, await h.challenge(browser, operation="verify"), operation="verify"
        )
        assert rejected.status_code == 200 and rejected.json()["accepted"] is False
        assert rejected.json()["decision"]["match"] is False
        assert rejected.json()["decision"]["score"] == main.cosine_similarity(
            vector(2), vector(1)
        )

    run(scenario())


# ---------------------------------------------------------------- D03 -------


def test_d03_twenty_claims_from_four_processes_spend_a_nonce_once(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        import hashlib

        request = {
            "command": "claim",
            "digest": hashlib.sha256(challenge["nonce"].encode()).hexdigest(),
            "binding": {
                "actor_id": h.actors["alice"].id,
                "session_id": browser.session_id,
                "tenant": h.actors["alice"].tenant,
                "subject_id": h.subjects["alice"],
                "generation": 0,
                "operation": "enroll",
            },
            "now_ms": int(h.now * 1000) + 16000,
            "attempts": 5,
        }
        results = await asyncio.gather(
            *(asyncio.to_thread(probe, request) for _ in range(4))
        )
        assert sum(r["wins"] for r in results) == 1, results
        assert sum(r["attempts"] for r in results) == 20 and not any(
            r["errors"] for r in results
        )
        # No later process, restart or retry can revive it.
        assert probe(request)["wins"] == 0
        h.restart()
        denied = await h.frozen(browser, challenge)
        assert (
            denied.status_code == 409 and denied.json()["error"] == "CHALLENGE_INVALID"
        )
        assert h.analyzer.calls == 0

    run(scenario())


def test_d03_expired_nonce_is_refused_inclusively_at_the_boundary(h, wired):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        h.now += 30.001
        late = await h.frozen(browser, challenge, advance=False)
        assert late.status_code == 409 and late.json()["error"] == "CHALLENGE_INVALID"
        challenge = await h.challenge(browser)
        h.now += 30
        assert (await h.frozen(browser, challenge, advance=False)).status_code == 200
        assert h.analyzer.calls == 1

    run(scenario())


# ---------------------------------------------------------------- D04 -------


@pytest.mark.parametrize("operation", ["verify", "templates"])
def test_d04_revocation_during_analysis_denies_commit(h, wired, operation):
    async def scenario():
        browser, _ = await h.enrolled()
        challenge = await h.challenge(
            browser, operation="verify" if operation == "verify" else "enroll"
        )

        async def revoke():
            revoked = await h.request(
                "DELETE", f"/v2/subjects/{h.subjects['alice']}/templates", browser
            )
            assert revoked.status_code == 200 and revoked.json()["revoked"] == 1

        h.analyzer.hook = revoke
        response = await h.frozen(browser, challenge, operation=operation)
        assert response.status_code == 409, response.text
        assert response.json()["error"] in {"SUBJECT_REVOKED", "RESOURCE_CHANGED"}
        assert h.templates() == []
        [old] = h.templates(active=False)
        assert old["revoked_at"] == h.now
        with h.owner.engine.connect() as connection:
            ops = list(
                connection.execute(
                    sa.select(s.operations.c.state, s.operations.c.result).where(
                        s.operations.c.subject_id == h.subjects["alice"],
                        s.operations.c.operation != "enroll",
                    )
                    if operation == "verify"
                    else sa.select(s.operations.c.state, s.operations.c.result).where(
                        s.operations.c.subject_id == h.subjects["alice"]
                    )
                ).mappings()
            )
        assert all(o["state"] != "processing" for o in ops)
        assert any((o["result"] or {}).get("error") == "SUBJECT_REVOKED" for o in ops)
        # A new request after revocation never accepts against the old set.
        h.analyzer.hook = None
        verify = await h.request(
            "GET",
            f"/v2/subjects/{h.subjects['alice']}/challenge?operation=verify",
            browser,
        )
        assert verify.status_code == 200
        response = await h.frozen(browser, verify.json(), operation="verify")
        assert (
            response.status_code == 404 and response.json()["error"] == "USER_NOT_FOUND"
        )

    run(scenario())


# ---------------------------------------------------------------- D05 -------


def test_d05_idempotent_replay_conflict_and_lost_response(h, wired):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        key = secrets.token_hex(16)
        # Engine committed but the gateway never delivered the response.
        first = await h.frozen(browser, challenge, key=key, engine=True)
        assert first.status_code == 200 and first.json()["template_id"]
        calls = h.analyzer.calls
        replay = await h.frozen(browser, challenge, key=key, advance=False)
        assert replay.status_code == 200, replay.text
        assert replay.json() == {**first.json(), "replayed": True}
        assert h.analyzer.calls == calls and len(h.templates()) == 1
        assert h.operation(first.json()["operation_id"])["state"] == "completed"
        # Same key, different payload.
        other = await h.scan(browser, challenge, raw=frozen_scan(challenge, 1), key=key)
        assert (
            other.status_code == 409 and other.json()["error"] == "IDEMPOTENCY_CONFLICT"
        )
        # New key cannot reuse the spent nonce.
        fresh = await h.frozen(browser, challenge, advance=False)
        assert fresh.status_code == 409 and fresh.json()["error"] == "CHALLENGE_INVALID"
        assert h.analyzer.calls == calls and len(h.templates()) == 1
        # Other actor cannot see the key.
        bob = await h.login("bob")
        await h.consent(bob, "bob")
        theirs = await h.frozen(bob, await h.challenge(bob, "bob"), name="bob", key=key)
        assert theirs.status_code == 200 and theirs.json()["template_id"]
        assert not theirs.json().get("replayed")

    run(scenario())


def test_d05_processing_operation_conflicts_until_committed(h, wired):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        key = secrets.token_hex(16)
        seen = []

        async def duplicate():
            h.analyzer.hook = None
            seen.append(await h.frozen(browser, challenge, key=key, advance=False))

        h.analyzer.hook = duplicate
        first = await h.frozen(browser, challenge, key=key)
        assert first.status_code == 200, first.text
        [dup] = seen
        assert dup.status_code == 409 and dup.json()["error"] == "OPERATION_IN_PROGRESS"
        assert len(h.templates()) == 1 and h.analyzer.calls == 1

    run(scenario())


def test_d05_rejected_decision_is_replayed_without_new_inference(h, wired, monkeypatch):
    monkeypatch.setattr(pad, "get_pad", lambda: FakePAD((0.98, 0.01, 0.01)))

    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        key = secrets.token_hex(16)
        first = await h.frozen(browser, challenge, key=key)
        assert first.status_code == 422 and first.json()["error"] == "LIVENESS_FAIL"
        assert "PAD spoof" in first.json()["message"]
        replay = await h.frozen(browser, challenge, key=key, advance=False)
        assert replay.status_code == 422 and replay.json() == {
            **first.json(),
            "request_id": replay.json()["request_id"],
        }
        assert h.analyzer.calls == 1 and h.templates() == []
        with h.owner.engine.connect() as connection:
            record = (
                connection.execute(
                    sa.select(s.operations).where(s.operations.c.key == key)
                )
                .mappings()
                .one()
            )
        assert record["state"] == "completed" and record["status"] == 422
        assert record["result"]["error"] == "LIVENESS_FAIL"

    run(scenario())


# ---------------------------------------------------------------- D06 -------


def test_d06_store_outage_fails_closed_and_leaves_nonce_unspent(h, wired):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        working = h.engine_ops.repo
        h.engine_ops.repo = PostgresRepository(local_url().set(port=15433))
        try:
            down = await h.frozen(browser, challenge, engine=True)
        finally:
            h.engine_ops.repo.dispose()
            h.engine_ops.repo = working
        assert (
            down.status_code == 503 and down.json()["error"] == "DEPENDENCY_UNAVAILABLE"
        )
        assert h.analyzer.calls == 0 and h.templates() == []
        recovered = await h.frozen(browser, challenge, advance=False)
        assert recovered.status_code == 200, recovered.text

    run(scenario())


def test_d06_incompatible_model_fingerprint_fails_before_inference(h, wired):
    async def scenario():
        browser, enrolled = await h.enrolled()
        with h.owner.engine.begin() as connection:
            connection.execute(
                s.templates.update()
                .where(s.templates.c.id == enrolled["template_id"])
                .values(
                    model_fingerprint="arcface-512-float32-le/000000000000000000000000"
                )
            )
        calls = h.analyzer.calls
        for operation in ("verify", "templates"):
            challenge = await h.challenge(
                browser, operation="verify" if operation == "verify" else "enroll"
            )
            response = await h.frozen(browser, challenge, operation=operation)
            assert response.status_code == 409, response.text
            assert response.json()["error"] == "MODEL_VERSION_MISMATCH"
        assert h.analyzer.calls == calls and len(h.templates()) == 1

    run(scenario())


def test_d06_tampered_ciphertext_fails_closed_and_ends_the_operation(h, wired):
    async def scenario():
        browser, enrolled = await h.enrolled()
        with h.owner.engine.begin() as connection:
            connection.execute(
                s.templates.update()
                .where(s.templates.c.id == enrolled["template_id"])
                .values(ciphertext=b"\x00" * 60)
            )
        key = secrets.token_hex(16)
        challenge = await h.challenge(browser, operation="verify")
        response = await h.frozen(browser, challenge, operation="verify", key=key)
        assert response.status_code == 503
        assert h.analyzer.calls == 1  # enrollment only; nothing decrypted reached it
        with h.owner.engine.connect() as connection:
            record = (
                connection.execute(
                    sa.select(s.operations).where(s.operations.c.key == key)
                )
                .mappings()
                .one()
            )
        assert record["state"] == "failed"
        retry = await h.frozen(
            browser, challenge, operation="verify", key=key, advance=False
        )
        assert (
            retry.status_code == 409 and retry.json()["error"] == "CIPHERTEXT_INVALID"
        )

    run(scenario())


def test_d06_commit_abort_keeps_no_partial_template(h, wired):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        constraint = "synthetic_commit_abort_" + secrets.token_hex(8)
        actor_id = h.actors["alice"].id
        with h.owner.engine.begin() as connection:
            connection.execute(
                sa.text(
                    f"ALTER TABLE face_auth.audit ADD CONSTRAINT {constraint} CHECK "
                    f"(NOT(actor_id = '{actor_id}' AND action = 'template.enroll' "
                    "AND outcome = 'committed')) NOT VALID"
                )
            )
        key = secrets.token_hex(16)
        try:
            aborted = await h.frozen(browser, challenge, key=key)
            assert aborted.status_code == 503
            assert h.templates() == [] and h.analyzer.calls == 1
        finally:
            with h.owner.engine.begin() as connection:
                connection.execute(
                    sa.text(f"ALTER TABLE face_auth.audit DROP CONSTRAINT {constraint}")
                )
        # Bounded retry: the same key is reconciled by status, not by inference.
        retry = await h.frozen(browser, challenge, key=key, advance=False)
        assert retry.status_code == 409
        assert retry.json()["error"] == "OPERATION_IN_PROGRESS"
        h.now += 121
        gone = await h.frozen(browser, challenge, key=key, advance=False)
        assert (
            gone.status_code == 409 and gone.json()["error"] == "OPERATION_INTERRUPTED"
        )
        assert h.templates() == [] and h.analyzer.calls == 1

    run(scenario())


# ---------------------------------------------------------------- D07 -------


def test_d07_forward_migration_of_0001_synthetic_data_and_old_app_refusal(databases):
    fresh_database("facetech_m2_migrate")
    owner = PostgresRepository(local_url("facetech_m2_migrate"))
    try:
        migrate(owner.engine, "0001_identity")
        with owner.engine.begin() as connection:
            columns = {
                r[0]
                for r in connection.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns WHERE "
                        "table_schema='face_auth' AND table_name='templates'"
                    )
                )
            }
            assert {"model_fingerprint", "owner_actor_id", "revoked_at"}.isdisjoint(
                columns
            )
            tables = {
                r[0]
                for r in connection.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables WHERE "
                        "table_schema='face_auth'"
                    )
                )
            }
            assert "operations" not in tables and "deletion_ledger" not in tables
            connection.execute(
                sa.text(
                    "INSERT INTO face_auth.actors VALUES "
                    "('a1','https://identity.test/realms/m','s1','t1','[\"participant\"]',0,true)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO face_auth.resources (tenant,kind,id,owner_actor_id,"
                    "generation,active) VALUES ('t1','subject','sub1','a1',0,true)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO face_auth.templates (id,tenant,subject_id,created_at,"
                    "active,ciphertext,format) VALUES "
                    "('tp1','t1','sub1',100,true,'\\x00','synthetic-nonbiometric-v1'),"
                    "('tp2','t1','sub1',200,false,'\\x00','synthetic-nonbiometric-v1')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO face_auth.deletion_requests VALUES ('t1','cap1','a1',300)"
                )
            )
        assert run(owner.ready(expected="0001_identity"))
        with pytest.raises(Unavailable):  # the current application refuses 0001
            run(owner.ready())
        migrate(owner.engine)
        migrate(owner.engine)  # re-runnable
        with owner.engine.connect() as connection:
            rows = {
                r["id"]: dict(r)
                for r in connection.execute(sa.select(s.templates)).mappings()
            }
            assert rows["tp1"]["owner_actor_id"] == "a1"
            assert rows["tp1"]["model_fingerprint"] == "synthetic-nonbiometric-v1"
            assert (
                rows["tp1"]["key_version"] == "v1" and rows["tp1"]["revoked_at"] is None
            )
            assert rows["tp2"]["revoked_at"] == 200
            ledger = list(connection.execute(sa.select(s.deletion_ledger)).mappings())
            assert [(e["kind"], e["id"]) for e in ledger] == [("capture", "cap1")]
        assert run(owner.ready())
        with pytest.raises(Unavailable):  # an old 0001-only application is refused
            run(owner.ready(expected="0001_identity"))
        config = AlembicConfig()
        config.set_main_option(
            "script_location",
            str(ROOT / "packages/face-auth/src/facetech_auth/migrations"),
        )
        with owner.engine.begin() as connection, pytest.raises(RuntimeError):
            config.attributes["connection"] = connection
            command.downgrade(config, "0001_identity")
        assert run(owner.ready())
    finally:
        owner.dispose()
        fresh_database("facetech_m2_migrate")
    # The shared M1 fixture database itself was migrated forward by this run.
    with databases[0].engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(s.templates)
                .where(s.templates.c.model_fingerprint.is_(None))
            ).scalar_one()
            == 0
        )


# ---------------------------------------------------------------- D08 -------


def test_d08_restore_drill_quarantines_and_applies_deletion_ledger(h, wired, tmp_path):
    async def scenario():
        alice, alice_enrolled = await h.enrolled()
        bob, bob_enrolled = await h.enrolled("bob")
        pending = await h.challenge(alice, operation="verify")
        dump = tmp_path / "facetech_m1.dump"
        with h.owner.engine.connect() as connection:
            interrupted = connection.execute(
                sa.select(sa.func.count())
                .select_from(s.operations)
                .where(s.operations.c.state == "processing")
            ).scalar_one()
        pg("pg_dump", "-Fc", "-f", str(dump), "facetech_m1")
        # After the backup: bob revokes, carol enrolls (lost: documented RPO).
        revoked = await h.request(
            "DELETE", f"/v2/subjects/{h.subjects['bob']}/templates", bob
        )
        assert revoked.status_code == 200 and revoked.json()["revoked"] == 1
        revoke_time = int(h.now)
        carol, carol_enrolled = await h.enrolled("carol")
        with h.owner.engine.connect() as connection:
            ledger = [
                dict(v)
                for v in connection.execute(sa.select(s.deletion_ledger)).mappings()
            ]
        fresh_database("facetech_m2_restore")
        pg("pg_restore", "--no-owner", "-d", "facetech_m2_restore", str(dump))
        restored = PostgresRepository(
            local_url("facetech_m2_restore"), clock=lambda: h.now
        )
        try:
            assert await restored.ready()
            with restored.engine.begin() as connection:
                summary = recover(connection, ledger, int(h.now))
            assert summary["sessions_revoked"] >= 2 and summary["nonces_spent"] >= 1
            assert summary["ledger_templates"] == 1
            assert summary["operations_failed"] == interrupted
            with restored.engine.connect() as connection:
                rows = {
                    r["id"]: dict(r)
                    for r in connection.execute(
                        sa.select(s.templates).where(
                            s.templates.c.subject_id.in_(list(h.subjects.values()))
                        )
                    ).mappings()
                }
                assert rows[alice_enrolled["template_id"]]["active"] is True
                assert rows[bob_enrolled["template_id"]]["active"] is False
                assert rows[bob_enrolled["template_id"]]["revoked_at"] == revoke_time
                assert carol_enrolled["template_id"] not in rows
                assert not connection.execute(
                    sa.select(s.sessions.c.id).where(s.sessions.c.active.is_(True))
                ).first()
                assert not connection.execute(
                    sa.select(s.challenges.c.digest).where(
                        s.challenges.c.consumed.is_(False)
                    )
                ).first()
                template = rows[alice_enrolled["template_id"]]
                assert template["model_fingerprint"] == h.analyzer.format_id
                assert np.array_equal(
                    np.frombuffer(
                        h.cipher.open(
                            template["tenant"],
                            "template",
                            template["id"],
                            template["ciphertext"],
                        ),
                        dtype="<f4",
                    ),
                    vector(),
                )
        finally:
            restored.dispose()
            fresh_database("facetech_m2_restore")
            dump.unlink()
        # The live fixture database is untouched by the drill.
        live = await h.frozen(alice, pending, operation="verify", advance=False)
        assert live.status_code == 200, live.text

    run(scenario())
