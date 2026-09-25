"""Synthetic encrypted application backup, fresh-ledger recovery and key drills."""

import base64
import hashlib
import json

import pytest
from auth_fixtures import run
from facetech_auth import schema as s
from facetech_auth.contracts import Unavailable
from facetech_auth.encryption import DataCipher, EnvelopeCipher, Keyring, migrate_legacy
from facetech_auth.postgres import PostgresRepository, migrate
from facetech_auth.recovery import (
    RestoreGate,
    backup_queries,
    enable_restored_reads,
    quarantine,
    rotate_templates,
    seal_ledger,
)
from postgres_fixtures import local_url
from test_durable_state import fresh_database
from test_privacy_lifecycle import consent, databases, enroll, rows  # noqa: F401
from test_privacy_lifecycle import h as privacy_h

h = privacy_h


def test_p07_encrypted_allowed_backup_restore_requires_fresh_ledger(h, tmp_path):
    async def scenario():
        participants = {}
        for name in ("alice", "bob", "carol"):
            browser = await h.login(name)
            await h.consent(browser, name)
            response = await h.scan(
                browser, await h.challenge(browser, name), name=name
            )
            assert response.status_code == 200
            participants[name] = (browser, response.json()["template_id"])
        tenant_ids = {a.tenant for a in h.actors.values()}
        actor_ids = {a.id for a in h.actors.values()}
        backup_cipher = EnvelopeCipher(
            Keyring({"b1": b"B" * 32}, "b1", purpose="backup")
        )
        backup = {}
        with h.owner.engine.connect() as connection:
            for table, query in backup_queries(int(h.now)):
                if "tenant" in table.c:
                    query = query.where(table.c.tenant.in_(tenant_ids))
                elif "actor_id" in table.c:
                    query = query.where(table.c.actor_id.in_(actor_ids))
                backup[table.name] = [
                    dict(value) for value in connection.execute(query).mappings()
                ]
            stale = seal_ledger(
                connection, backup_cipher, identity="ledger", now=int(h.now)
            )

        def encode(value):
            if isinstance(value, bytes):
                return {"binary": base64.b64encode(value).decode()}
            raise TypeError()

        backup_file = tmp_path / "synthetic-app-backup.enc"
        backup_file.write_bytes(
            backup_cipher.seal(
                "recovery",
                "backup",
                "application",
                json.dumps(backup, default=encode).encode(),
            )
        )
        assert b"SYNTHETIC" not in backup_file.read_bytes()
        assert (
            not {"records", "recordings", "sessions", "challenges", "operations"}
            & backup.keys()
        )
        # Acknowledged deletion AFTER the application backup. Fresh ledger must cover it.
        h.now += 1
        assert (
            await consent(
                h,
                participants["bob"][0],
                "template_authentication",
                "DELETE",
                name="bob",
            )
        ).status_code == 200
        with h.owner.engine.connect() as connection:
            ledger = seal_ledger(
                connection, backup_cipher, identity="ledger", now=int(h.now)
            )
        # The original captured expiry remains expired even if purge never ran.
        for item in backup["templates"]:
            if item["id"] == participants["carol"][1]:
                item["expires_at"] = int(h.now)
        # Serialize the expiry fixture before restoration, with the same original-expiry rule.
        backup_file.write_bytes(
            backup_cipher.seal(
                "recovery",
                "backup",
                "application",
                json.dumps(backup, default=encode).encode(),
            )
        )
        fresh_database("facetech_m2_restore")
        gate = RestoreGate(
            required_through=int(h.now),
            required_digest=hashlib.sha256(ledger).hexdigest(),
        )
        restored = PostgresRepository(
            local_url("facetech_m2_restore"), clock=lambda: h.now, restore_gate=gate
        )
        try:
            migrate(restored.engine)

            with restored.engine.begin() as connection:
                quarantine(connection)
            unconfigured = PostgresRepository(local_url("facetech_m2_restore"))
            try:
                with pytest.raises(Unavailable):
                    await unconfigured.target(
                        h.actors["alice"].tenant, "subject", h.subjects["alice"]
                    )
            finally:
                unconfigured.dispose()

            def decode(value):
                if set(value) == {"binary"}:
                    return base64.b64decode(value["binary"], validate=True)
                return value

            recovered = json.loads(
                backup_cipher.open(
                    "recovery", "backup", "application", backup_file.read_bytes()
                ),
                object_hook=decode,
            )
            with restored.engine.begin() as connection:
                for table, _ in backup_queries(int(h.now)):
                    if recovered[table.name]:
                        connection.execute(table.insert(), recovered[table.name])
            with pytest.raises(Unavailable):
                await restored.target(
                    h.actors["alice"].tenant, "subject", h.subjects["alice"]
                )
            for invalid in (stale, b"missing or tampered ledger"):
                with pytest.raises(Unavailable):
                    await enable_restored_reads(
                        restored,
                        gate,
                        backup_cipher,
                        invalid,
                        identity="ledger",
                        template_cipher=h.cipher,
                        model_ids={h.analyzer.format_id},
                    )
                assert not gate.enabled
            with pytest.raises(Unavailable):
                await enable_restored_reads(
                    restored,
                    gate,
                    backup_cipher,
                    ledger,
                    identity="ledger",
                    template_cipher=DataCipher(b"wrong-key".ljust(32, b"0")),
                    model_ids={h.analyzer.format_id},
                )
            assert not gate.enabled
            await enable_restored_reads(
                restored,
                gate,
                backup_cipher,
                ledger,
                identity="ledger",
                template_cipher=h.cipher,
                model_ids={h.analyzer.format_id},
            )
            assert gate.enabled
            assert await restored.target(
                h.actors["alice"].tenant, "subject", h.subjects["alice"]
            )
            surviving = rows(restored, s.templates)
            assert [v["id"] for v in surviving] == [participants["alice"][1]]
            assert (
                h.cipher.open(
                    surviving[0]["tenant"],
                    "template",
                    surviving[0]["id"],
                    surviving[0]["ciphertext"],
                )
                == b"nonbiometric template fixture"
            )
            assert not rows(restored, s.sessions) and not rows(restored, s.challenges)
            assert (
                rows(
                    restored,
                    s.consents,
                    subject_id=h.subjects["bob"],
                    scope="template_authentication",
                )[0]["granted"]
                is False
            )
        finally:
            restored.dispose()
            fresh_database("facetech_m2_restore")

    run(scenario())


def test_p04_template_rotation_commits_key_version_and_ciphertext_together(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        result = await enroll(h, browser)
        identity = result.json()["template_id"]
        before = rows(h.repo, s.templates, id=identity)[0]
        # Limit this drill to an isolated restore database, because other fixtures
        # deliberately have distinct keys even when the version label is identical.
        fresh_database("facetech_m2_restore")
        repo = PostgresRepository(local_url("facetech_m2_restore"))
        try:
            migrate(repo.engine)
            with repo.engine.begin() as connection:
                connection.execute(s.templates.insert().values(**before))
            h.cipher.keyring._keys["v2"] = b"N" * 32
            h.cipher.keyring.active = "v2"
            assert await rotate_templates(repo, h.cipher, limit=1) == 1
            after = rows(repo, s.templates, id=identity)[0]
            assert after["key_version"] == "v2"
            assert (
                json.loads(before["ciphertext"])["ciphertext"]
                == json.loads(after["ciphertext"])["ciphertext"]
            )
            assert (
                h.cipher.open(
                    after["tenant"],
                    "template",
                    identity,
                    after["ciphertext"],
                    expected_version="v2",
                )
                == b"nonbiometric template fixture"
            )
            assert await rotate_templates(repo, h.cipher) == 0
        finally:
            repo.dispose()
            fresh_database("facetech_m2_restore")

    run(scenario())


def test_p03_explicit_legacy_migration_never_runtime_fallback():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key, nonce = b"L" * 32, b"n" * 12
    old = nonce + AESGCM(key).encrypt(
        nonce, b"synthetic", json.dumps(["v1", "t", "template", "r"]).encode()
    )
    cipher = DataCipher(b"E" * 32)
    with pytest.raises(Unavailable):
        cipher.open("t", "template", "r", old)
    new = migrate_legacy(cipher, "t", "template", "r", old, key)
    assert cipher.open("t", "template", "r", new) == b"synthetic"


def test_p03_file_provider_rejects_in_volume_keys_and_wrong_service(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps(
            {
                "purpose": "evaluation",
                "active": "v1",
                "keys": {"v1": base64.b64encode(b"k" * 32).decode()},
            }
        )
    )
    with pytest.raises(ValueError):
        Keyring.from_file(path, purpose="evaluation", forbidden_roots=[tmp_path])
    with pytest.raises(Unavailable):
        Keyring.from_file(path, purpose="template", forbidden_roots=[])
    evaluation = EnvelopeCipher(
        Keyring.from_file(path, purpose="evaluation", forbidden_roots=[])
    )
    with pytest.raises(Unavailable):
        evaluation.seal("t", "template", "r", b"synthetic")
