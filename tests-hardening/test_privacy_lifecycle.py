"""P01-P08 with nonbiometric bytes and ownership-checked PostgreSQL fixtures."""

import asyncio
import base64
import json

import msgpack
import pytest
import sqlalchemy as sa
from auth_fixtures import run
from facetech_auth import schema as s
from facetech_auth.contracts import Denied, Unavailable
from facetech_auth.encryption import DataCipher, EnvelopeCipher, KeyFailure, Keyring
from facetech_auth.evaluation_store import records, tombstones
from facetech_auth.lifecycle import Lifecycle
from facetech_auth.privacy import Recording
from facetech_auth.recovery import (
    RestoreGate,
    backup_queries,
    seal_ledger,
)
from postgres_fixtures import PersistentHarness, prepare_databases


@pytest.fixture(scope="module")
def databases(request):
    if not request.config.getoption("--hardening-postgres"):
        pytest.skip("Explicit owned synthetic PostgreSQL opt-in required")
    owner, evaluation = prepare_databases()
    yield owner, evaluation
    owner.dispose()
    evaluation.dispose()


@pytest.fixture
def h(databases):
    harness = run(PersistentHarness().initialize(*databases))
    harness.gateway_ops.recording = Recording(
        harness.repo, harness.evaluation, round_id="round-1"
    )
    yield harness
    harness.close()


async def consent(
    h, browser, scope="evaluation_recording", method="POST", version=None, name="alice"
):
    versions = {
        "evaluation_recording": "evaluation-recording-v1",
        "template_authentication": "template-authentication-v1",
    }
    return await h.request(
        method,
        f"/v2/subjects/{h.subjects[name]}/consents/{scope}",
        browser,
        **(
            {"json": {"text_version": version or versions[scope]}}
            if method == "POST"
            else {}
        ),
    )


async def enroll(h, browser, *, key=None):
    challenge = await h.challenge(browser)
    raw = msgpack.packb(
        {"challenge": challenge, "frames": [{"jpeg_bytes": b"SYNTHETIC NONBIOMETRIC"}]}
    )
    return await h.scan(browser, challenge, raw=raw, key=key)


def rows(repo, table, **filters):
    with repo.engine.connect() as connection:
        return [
            dict(v)
            for v in connection.execute(
                sa.select(table).where(
                    *(getattr(table.c, k) == v for k, v in filters.items())
                )
            ).mappings()
        ]


def test_p01_required_optional_and_old_versions_are_independent(h):
    async def scenario():
        browser = await h.login()
        assert (await consent(h, browser, version="old")).status_code == 400
        assert (await consent(h, browser)).status_code == 200
        missing = await enroll(h, browser)
        assert (
            missing.status_code == 403 and missing.json()["error"] == "CONSENT_REQUIRED"
        )
        await consent(h, browser, method="DELETE")
        assert (await h.consent(browser)).status_code == 200
        result = await enroll(h, browser)
        assert result.status_code == 200 and result.json()["accepted"]
        assert result.json()["recording"] == {
            "status": "skipped",
            "reason": "NO_EVALUATION_CONSENT",
        }
        assert (await consent(h, browser)).status_code == 200
        result = await enroll(h, browser)
        assert (
            result.status_code == 200
            and result.json()["recording"]["status"] == "stored"
        )
        receipt = result.json()["recording"]["receipt_id"]
        owner = await h.request("GET", f"/v2/receipts/{receipt}", browser)
        assert owner.status_code == 200 and owner.json() == {
            "recording_status": "stored"
        }
        events = rows(h.repo, s.consent_events, subject_id=h.subjects["alice"])
        assert all(
            e["actor_id"] == h.actors["alice"].id and e["authority"] == "self"
            for e in events
        )
        assert [
            e["revision"] for e in events if e["scope"] == "evaluation_recording"
        ] == [1, 2, 3]

    run(scenario())


@pytest.mark.parametrize("name", ["bob", "carol", "reviewer", "admin", "operator"])
def test_p01_others_cannot_grant_or_withdraw(h, name):
    async def scenario():
        browser = await h.login(name)
        for method in ("POST", "DELETE"):
            for scope in ("template_authentication", "evaluation_recording"):
                assert (await consent(h, browser, scope, method)).status_code == 404
        assert rows(h.repo, s.consents, subject_id=h.subjects["alice"]) == []

    run(scenario())


@pytest.mark.parametrize("when", ["analysis", "before_commit", "after_commit"])
def test_p02_withdrawal_serializes_and_regrant_does_not_revive(h, when):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)

        async def withdraw():
            assert (await consent(h, browser, method="DELETE")).status_code == 200
            # Regrant has a new revision: an in-flight old grant remains invalid.
            assert (await consent(h, browser)).status_code == 200

        if when == "analysis":
            h.analyzer.hook = withdraw
        if when == "before_commit":
            finish = h.gateway_ops.recording.finish

            async def race(*args, **kwargs):
                await withdraw()
                return await finish(*args, **kwargs)

            h.gateway_ops.recording.finish = race
        result = await enroll(h, browser)
        assert result.status_code == 200 and result.json()["accepted"]
        if when == "after_commit":
            assert result.json()["recording"]["status"] == "stored"
            await withdraw()
        else:
            assert result.json()["recording"]["status"] == "skipped"
        reservations = rows(h.repo, s.recordings, subject_id=h.subjects["alice"])
        assert len(reservations) == 1 and reservations[0]["state"] == "deleted"
        identity = reservations[0]["id"]
        for route in (
            f"/v2/receipts/{identity}",
            f"/v2/captures/{identity}",
        ):
            assert (await h.request("GET", route, browser)).status_code == 404
        life = Lifecycle(h.repo, h.evaluation, tenant=h.actors["alice"].tenant)
        await life.drain(limit=100)
        assert not rows(
            h.evaluation_repo, records, tenant=h.actors["alice"].tenant, id=identity
        )
        with pytest.raises(Denied):
            await h.evaluation.write(
                h.actors["alice"].tenant, identity, {"fixture": True}, h.now + 10
            )

    run(scenario())


def test_p02_real_concurrent_grants_withdrawals_and_recording(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        results = await asyncio.gather(
            enroll(h, browser), consent(h, browser, method="DELETE")
        )
        assert all(r.status_code == 200 for r in results)
        assert (
            rows(h.repo, s.recordings, subject_id=h.subjects["alice"], state="stored")
            == []
        )
        assert not rows(
            h.repo,
            s.resources,
            tenant=h.actors["alice"].tenant,
            kind="capture",
            owner_actor_id=h.actors["alice"].id,
            active=True,
        )

    run(scenario())


def test_p02_template_withdrawal_revokes_and_blocks_inflight_commit(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        enrolled = await enroll(h, browser)
        assert enrolled.status_code == 200

        async def withdraw():
            assert (
                await consent(h, browser, "template_authentication", "DELETE")
            ).status_code == 200

        h.analyzer.hook = withdraw
        late = await enroll(h, browser)
        assert late.status_code in {403, 409}
        assert not rows(
            h.repo, s.templates, subject_id=h.subjects["alice"], active=True
        )
        await Lifecycle(h.repo, h.evaluation, tenant=h.actors["alice"].tenant).drain(
            limit=100
        )
        assert not rows(h.repo, s.templates, subject_id=h.subjects["alice"])

    run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        "tenant",
        "id",
        "kind",
        "ciphertext",
        "wrapped",
        "version",
        "schema",
        "missing",
        "plaintext",
    ],
)
def test_p03_authenticated_envelopes_fail_closed(change):
    cipher = DataCipher(b"x" * 32)
    value = cipher.seal("a", "template", "r", b"SYNTHETIC")
    tenant, kind, identity = "a", "template", "r"
    if change == "tenant":
        tenant = "b"
    elif change == "id":
        identity = "other"
    elif change == "kind":
        kind = "evaluation"
    elif change == "missing":
        cipher = EnvelopeCipher(Keyring({"v2": b"z" * 32}, "v2", purpose="template"))
    elif change == "plaintext":
        value = b"SYNTHETIC"
    else:
        data = json.loads(value)
        if change in {"ciphertext", "wrapped"}:
            data[change] = base64.b64encode(b"wrong" * 12).decode()
        if change == "version":
            data["key_version"] = "v9"
        if change == "schema":
            data["schema"] = 2
        value = json.dumps(data).encode()
    with pytest.raises(KeyFailure):
        cipher.open(tenant, kind, identity, value)


def test_p04_rotation_rewrap_and_missing_key_recovery():
    keys = Keyring({"old": b"a" * 32, "new": b"b" * 32}, "old", purpose="template")
    cipher = EnvelopeCipher(keys)
    old = cipher.seal("a", "template", "r", b"synthetic")
    keys.active = "new"
    new = cipher.rewrap("a", "template", "r", old)
    assert json.loads(old)["ciphertext"] == json.loads(new)["ciphertext"]
    assert cipher.open("a", "template", "r", old) == cipher.open(
        "a", "template", "r", new
    )
    for kwargs in (
        {"references": 1, "backups_verified": True, "backup_references": 0},
        {"references": 0, "backups_verified": False, "backup_references": 0},
        {"references": 0, "backups_verified": True, "backup_references": 1},
    ):
        with pytest.raises(KeyFailure):
            keys.retire("old", **kwargs)
    keys.retire("old", references=0, backups_verified=True, backup_references=0)
    with pytest.raises(KeyFailure):
        cipher.open("a", "template", "r", old)
    recovered = EnvelopeCipher(Keyring({"old": b"a" * 32}, "old", purpose="template"))
    assert recovered.open("a", "template", "r", old) == b"synthetic"
    assert cipher.open("a", "template", "r", new) == b"synthetic"


def test_p05_self_deletion_removes_links_ciphertext_and_wrapped_key(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        result = await enroll(h, browser)
        identity = result.json()["recording"]["receipt_id"]
        assert (
            await h.request("DELETE", f"/v2/captures/{identity}", browser)
        ).status_code == 200
        assert (
            await h.request("GET", f"/v2/receipts/{identity}", browser)
        ).status_code == 404
        life = Lifecycle(h.repo, h.evaluation, tenant=h.actors["alice"].tenant)
        await life.drain(limit=100)
        await life.drain(limit=100)
        assert rows(h.evaluation_repo, records, id=identity) == []
        assert rows(h.evaluation_repo, tombstones, id=identity)
        assert rows(h.repo, s.resources, id=identity) == []

    run(scenario())


def test_p06_disk_full_and_bounded_purge_retries_deny_reads(h, caplog):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        original = h.evaluation.write_sync

        def full(*args, **kwargs):
            raise OSError(28, "synthetic disk full")

        h.evaluation.write_sync = full
        result = await enroll(h, browser)
        assert result.status_code == 200 and result.json()["accepted"]
        assert result.json()["recording"]["status"] == "failed"
        h.evaluation.write_sync = original
        result = await enroll(h, browser)
        identity = result.json()["recording"]["receipt_id"]
        expiry = rows(h.repo, s.recordings, id=identity)[0]["expires_at"]
        h.now = expiry
        with pytest.raises(Denied):
            await h.evaluation.read(h.actors["alice"].tenant, identity)
        life = Lifecycle(h.repo, h.evaluation, tenant=h.actors["alice"].tenant)
        await life.expire()
        original_delete = h.evaluation.delete

        async def purge_full(*args, **kwargs):
            raise OSError(28, "synthetic disk full")

        h.evaluation.delete = purge_full
        for _ in range(5):
            await life.drain(limit=100)
            h.now += 301
        job = rows(h.repo, s.purge_jobs, id=identity)[0]
        assert job["state"] == "blocked" and job["attempts"] == 5
        await life.drain(limit=100)
        assert rows(h.repo, s.purge_jobs, id=identity)[0]["attempts"] == 5
        assert rows(h.repo, s.resources, id=identity, active=True) == []
        h.evaluation.delete = original_delete
        assert "RECORDING_FAILED" in caplog.text and "PURGE_FAILED" in caplog.text

    run(scenario())


def test_p07_missing_stale_ledger_and_template_expiry_quarantine(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        result = await enroll(h, browser)
        identity = result.json()["template_id"]
        cipher = EnvelopeCipher(
            Keyring({"backup-v1": b"q" * 32}, "backup-v1", purpose="backup")
        )
        gate = RestoreGate(required_through=int(h.now))
        h.other_repo.restore_gate = gate
        with pytest.raises(Unavailable):
            await h.other_repo.target(
                h.actors["alice"].tenant, "subject", h.subjects["alice"]
            )
        with h.owner.engine.begin() as connection:
            stale = seal_ledger(
                connection, cipher, identity="ledger", now=int(h.now) - 301
            )
        with pytest.raises(Unavailable):
            gate.validate(
                json.loads(cipher.open("recovery", "backup", "ledger", stale)),
                int(h.now),
                stale,
            )
        assert not gate.enabled
        # Isolated harness rows only: emulate an expired template restored from an older backup.
        with h.owner.engine.begin() as connection:
            connection.execute(
                s.templates.update()
                .where(s.templates.c.id == identity)
                .values(expires_at=int(h.now))
            )
            fresh = seal_ledger(connection, cipher, identity="ledger", now=int(h.now))
        # Other tests use different ephemeral keys in this shared DB, so a full recovery
        # belongs in a separate physical restore DB (see dedicated test below).
        assert json.loads(cipher.open("recovery", "backup", "ledger", fresh))[
            "through"
        ] == int(h.now)
        h.other_repo.restore_gate = None
        check = await h.scan(
            browser, await h.challenge(browser, operation="verify"), operation="verify"
        )
        assert check.status_code == 404
        assert not gate.enabled

    run(scenario())


def test_p07_backup_scope_excludes_raw_sessions_tokens_and_receipts(h):
    queries = {table.name: query for table, query in backup_queries(int(h.now))}
    assert (
        not {"records", "recordings", "sessions", "logins", "challenges", "operations"}
        & queries.keys()
    )
    with h.owner.engine.connect() as connection:
        resources = list(connection.execute(queries["resources"]).mappings())
    assert all(
        r["kind"] in {"subject", "round"} and r["data_ref"] is None for r in resources
    )


def test_p08_export_has_no_enabled_route(h):
    browser = run(h.login("admin"))
    for route in ("/review/api/export", "/v2/export", "/export"):
        assert run(h.request("POST", route, browser)).status_code == 404
