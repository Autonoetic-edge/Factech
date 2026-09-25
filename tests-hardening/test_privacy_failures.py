"""Failure ordering, restart and disclosure checks for the M3 lifecycle."""

import asyncio
import json
import secrets
import threading

import pytest
import sqlalchemy as sa
from auth_fixtures import run
from facetech_auth import schema as s
from facetech_auth.contracts import Unavailable
from facetech_auth.encryption import KeyFailure, Keyring
from facetech_auth.evaluation_store import records
from facetech_auth.lifecycle import Lifecycle
from facetech_auth.policy import Scope
from facetech_auth.postgres import PostgresRepository
from postgres_fixtures import local_url
from test_privacy_lifecycle import consent, databases, enroll, rows  # noqa: F401
from test_privacy_lifecycle import h as privacy_h

h = privacy_h


def test_p02_record_lock_serializes_concurrent_withdrawal(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        stored, release = threading.Event(), threading.Event()
        original = h.evaluation.write_sync

        def slow(*args, **kwargs):
            original(*args, **kwargs)
            stored.set()
            assert release.wait(5)

        h.evaluation.write_sync = slow
        recording = asyncio.create_task(enroll(h, browser))
        assert await asyncio.to_thread(stored.wait, 5)
        withdrawal = asyncio.create_task(consent(h, browser, method="DELETE"))
        # Let the second request reach its DB lock while recording holds the subject.
        await asyncio.sleep(0.05)
        assert not withdrawal.done()
        release.set()
        outcome, revoked = await asyncio.gather(recording, withdrawal)
        assert outcome.status_code == revoked.status_code == 200
        assert (
            rows(h.repo, s.recordings, subject_id=h.subjects["alice"])[0]["state"]
            == "deleted"
        )
        assert not rows(
            h.repo,
            s.resources,
            owner_actor_id=h.actors["alice"].id,
            kind="capture",
            active=True,
        )

    run(scenario())


def test_p02_withdrawal_wins_before_write_and_no_bytes_persist(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)

        async def withdraw():
            await consent(h, browser, method="DELETE")

        h.analyzer.hook = withdraw

        def forbidden(*args, **kwargs):
            pytest.fail("Evaluation write occurred after an earlier withdrawal")

        h.evaluation.write_sync = forbidden
        result = await enroll(h, browser)
        assert result.status_code == 200 and result.json()["accepted"]
        assert result.json()["recording"]["status"] == "skipped"

    run(scenario())


def test_p06_evaluation_committed_app_commit_fails_and_restart_purges(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        constraint = "m3_record_abort_" + secrets.token_hex(6)
        actor = h.actors["alice"].id
        # Fail only recording audit, after the separate evaluation write committed.
        with h.owner.engine.begin() as connection:
            connection.execute(
                sa.text(
                    f"ALTER TABLE face_auth.audit ADD CONSTRAINT {constraint} "
                    f"CHECK (NOT (actor_id = '{actor}' AND action = 'evaluation.record')) NOT VALID"
                )
            )
        try:
            result = await enroll(h, browser)
            assert result.status_code == 200 and result.json()["accepted"]
            assert result.json()["recording"]["status"] == "failed"
        finally:
            with h.owner.engine.begin() as connection:
                connection.execute(
                    sa.text(f"ALTER TABLE face_auth.audit DROP CONSTRAINT {constraint}")
                )
        [recording] = rows(h.repo, s.recordings, subject_id=h.subjects["alice"])
        identity = recording["id"]
        assert rows(h.evaluation_repo, records, id=identity)
        assert not rows(h.repo, s.resources, id=identity)
        restarted = PostgresRepository(local_url(), clock=lambda: h.now)
        try:
            life = Lifecycle(restarted, h.evaluation, tenant=h.actors["alice"].tenant)
            assert (await life.drain())["completed"] == 1
            assert not rows(h.evaluation_repo, records, id=identity)
            assert (await life.drain())["completed"] == 0
        finally:
            restarted.dispose()

    run(scenario())


def test_p06_purge_ack_loss_reclaims_lease_and_retries_idempotently(h, caplog):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        result = await enroll(h, browser)
        identity = result.json()["recording"]["receipt_id"]
        await h.request("DELETE", f"/v2/captures/{identity}", browser)
        original = h.evaluation.delete

        async def lost_ack(*args):
            await original(*args)
            raise OSError("synthetic lost acknowledgement")

        h.evaluation.delete = lost_ack
        life = Lifecycle(h.repo, h.evaluation, tenant=h.actors["alice"].tenant)
        assert (await life.drain())["failed"] == 1
        assert not rows(h.evaluation_repo, records, id=identity)
        h.evaluation.delete = original
        h.now += 3
        assert (
            await Lifecycle(
                h.other_repo, h.evaluation, tenant=h.actors["alice"].tenant
            ).drain()
        )["completed"] == 1
        job = rows(h.repo, s.purge_jobs, id=identity)[0]
        assert job["state"] == "done" and job["attempts"] == 2
        assert "PURGE_FAILED" in caplog.text

    run(scenario())


def test_p06_crashed_last_lease_becomes_observable_blocked_job(h, caplog):
    async def scenario():
        browser = await h.login()
        identity = h.captures["alice"]
        assert (
            await h.request("DELETE", f"/v2/captures/{identity}", browser)
        ).status_code == 200
        with h.owner.engine.begin() as connection:
            connection.execute(
                s.purge_jobs.update()
                .where(
                    s.purge_jobs.c.tenant == h.actors["alice"].tenant,
                    s.purge_jobs.c.id == identity,
                )
                .values(attempts=5, lease_until=int(h.now) + 30)
            )
        life = Lifecycle(h.other_repo, h.evaluation, tenant=h.actors["alice"].tenant)
        assert (await life.drain())["completed"] == 0
        h.now += 30
        await life.drain()
        assert (await life.status())["blocked"] == 1
        assert "PURGE_RETRY_EXHAUSTED" in caplog.text

    run(scenario())


def test_p03_p05_authorized_review_expiry_and_key_failure(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        result = await enroll(h, browser)
        identity = result.json()["recording"]["receipt_id"]
        reviewer = await h.login("reviewer")
        assert (
            await h.request("GET", f"/v2/captures/{identity}", reviewer)
        ).status_code == 200
        assert (
            await h.request("GET", f"/v2/captures/{identity}/frames/0", reviewer)
        ).status_code == 404
        await h.provisioner.review_grant(
            h.actors["reviewer"].id,
            h.actors["reviewer"].tenant,
            "round-1",
            {Scope.METADATA, Scope.FRAMES},
            int(h.now) + 3600,
        )
        frame = await h.request("GET", f"/v2/captures/{identity}/frames/0", reviewer)
        assert frame.status_code == 200 and frame.content == b"SYNTHETIC NONBIOMETRIC"
        saved = h.evaluation_cipher.keyring._keys.pop("v1")
        missing = await h.request("GET", f"/v2/captures/{identity}", reviewer)
        assert (
            missing.status_code == 503 and missing.json()["error"] == "KEY_UNAVAILABLE"
        )
        h.evaluation_cipher.keyring._keys["v1"] = saved
        # Boundary expiration without advancing sessions: set the immutable test
        # fixture timestamps to now, then assert both stores refuse disclosure.
        with h.owner.engine.begin() as connection:
            connection.execute(
                s.resources.update()
                .where(s.resources.c.id == identity)
                .values(expires_at=int(h.now))
            )
        assert (
            await h.request("GET", f"/v2/captures/{identity}", reviewer)
        ).status_code == 404
        assert (
            await h.request("GET", f"/v2/receipts/{identity}", browser)
        ).status_code == 404

    run(scenario())


def test_p04_evaluation_rotation_keeps_expiry_and_key_loss_is_explicit(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        result = await enroll(h, browser)
        identity = result.json()["recording"]["receipt_id"]
        before = rows(h.evaluation_repo, records, id=identity)[0]
        h.evaluation_cipher.keyring._keys["v2"] = b"2" * 32
        h.evaluation_cipher.keyring.active = "v2"
        tenant, cursor = h.actors["alice"].tenant, ""
        while True:
            batch = await h.evaluation.rotate(tenant=tenant, after=cursor, limit=1)
            if not batch["visited"]:
                break
            cursor = batch["after"]
        after = rows(h.evaluation_repo, records, id=identity)[0]
        assert after["expires_at"] == before["expires_at"]
        assert (
            json.loads(after["ciphertext"])["ciphertext"]
            == json.loads(before["ciphertext"])["ciphertext"]
        )
        h.evaluation_cipher.keyring.retire(
            "v1", references=0, backups_verified=True, backup_references=0
        )
        assert (await h.evaluation.read(tenant, identity))[
            "recording_status"
        ] == "stored"

    run(scenario())


def test_p03_missing_file_and_active_key_have_explicit_failures(tmp_path):
    with pytest.raises(KeyFailure, match="KEY_UNAVAILABLE"):
        Keyring.from_file(tmp_path / "missing", purpose="template", forbidden_roots=[])
    with pytest.raises(KeyFailure, match="KEY_UNAVAILABLE"):
        Keyring({}, "missing", purpose="template")


def test_p05_expiry_during_analysis_and_unconfigured_round_deny_template_commit(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        h.engine_ops.template_expires_at = None
        result = await enroll(h, browser)
        assert (
            result.status_code == 503 and result.json()["error"] == "ROUND_UNCONFIGURED"
        )
        h.engine_ops.template_expires_at = int(h.now) + 10
        result = await enroll(h, browser)
        assert result.status_code == 200

        async def advance():
            h.now += 10

        h.analyzer.hook = advance
        result = await h.scan(
            browser, await h.challenge(browser, operation="verify"), operation="verify"
        )
        assert (
            result.status_code == 409 and result.json()["error"] == "TEMPLATE_EXPIRED"
        )

    run(scenario())


@pytest.mark.parametrize("rejected", [False, True])
def test_p01_late_consent_cannot_record_replayed_attempt(h, rejected):
    async def scenario():
        from facetech_auth.operations import Analysis

        browser = await h.login()
        await h.consent(browser)
        if rejected:

            async def reject(*args, **kwargs):
                return Analysis(
                    False,
                    {"error": "NO_FACE", "message": "Synthetic rejection"},
                    error="NO_FACE",
                    status=422,
                )

            h.analyzer.analyze = reject
        challenge = await h.challenge(browser)
        key = secrets.token_hex(16)
        original = await h.scan(browser, challenge, key=key)
        assert original.status_code == (422 if rejected else 200)
        await consent(h, browser)
        replay = await h.scan(browser, challenge, key=key)
        assert replay.status_code == original.status_code
        assert replay.json()["recording"] == {
            "status": "skipped",
            "reason": "NO_RECORDING_FOR_REPLAY",
        }
        assert rows(h.repo, s.recordings, subject_id=h.subjects["alice"]) == []

    run(scenario())


def test_p03_missing_historical_key_blocks_readiness():
    from facetech_auth.encryption import EnvelopeCipher

    cipher = EnvelopeCipher(Keyring({"v2": b"2" * 32}, "v2", purpose="template"))
    assert cipher.ready({"v2"})
    with pytest.raises(Unavailable):
        cipher.ready({"v1", "v2"})


def test_p04_active_key_switch_during_seal_cannot_mislabel_template(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        original = h.cipher.seal
        h.cipher.keyring._keys["v2"] = b"R" * 32

        def switch(*args):
            ciphertext = original(*args)
            h.cipher.keyring.active = "v2"
            return ciphertext

        h.cipher.seal = switch
        response = await enroll(h, browser)
        assert response.status_code == 200
        [template] = rows(h.repo, s.templates, id=response.json()["template_id"])
        assert (
            template["key_version"]
            == json.loads(template["ciphertext"])["key_version"]
            == "v1"
        )
        assert h.cipher.open(
            template["tenant"],
            "template",
            template["id"],
            template["ciphertext"],
            expected_version=template["key_version"],
        )

    run(scenario())


def test_p03_schema_boolean_is_not_an_authenticated_integer_version():
    from facetech_auth.encryption import DataCipher

    cipher = DataCipher(b"k" * 32)
    envelope = json.loads(cipher.seal("t", "template", "r", b"synthetic"))
    envelope["schema"] = True
    with pytest.raises(Unavailable):
        cipher.open("t", "template", "r", json.dumps(envelope).encode())


@pytest.mark.parametrize("field", ["frames", "challenge"])
@pytest.mark.parametrize("value", [None, "invalid"])
def test_p01_recording_cannot_override_engine_outcome_on_malformed_scan(
    h, field, value
):
    async def scenario():
        import msgpack

        browser = await h.login()
        await h.consent(browser)
        await consent(h, browser)
        challenge = await h.challenge(browser)
        raw = msgpack.packb({"challenge": challenge, field: value})
        result = await h.scan(browser, challenge, raw=raw)
        if field == "challenge":
            assert (
                result.status_code == 409
                and result.json()["error"] == "CHALLENGE_INVALID"
            )
            assert result.json()["recording"]["status"] == "skipped"
            assert h.analyzer.calls == 0
        else:
            # Synthetic analyzer accepts any bytes; this tests optional recording
            # error isolation, not the frozen parser or biometric acceptance.
            assert result.status_code == 200 and result.json()["accepted"]
            assert result.json()["recording"]["status"] == "stored"

    run(scenario())
