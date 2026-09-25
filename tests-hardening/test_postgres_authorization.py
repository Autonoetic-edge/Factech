"""A01-A09 database-backed enforcement against the owned PostgreSQL 17 cluster."""

import asyncio
import secrets
from dataclasses import replace

import pytest
import sqlalchemy as sa
from auth_fixtures import run
from facetech_auth import schema as s
from facetech_auth.contracts import Denied, LoginAttempt
from facetech_auth.policy import Scope
from facetech_auth.postgres import PostgresRepository, drain_logout_outbox
from facetech_auth.sessions import digest
from postgres_fixtures import PersistentHarness, local_url, prepare_databases


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


@pytest.fixture
def h(databases):
    harness = run(PersistentHarness().initialize(*databases))
    yield harness
    harness.close()


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("transport", ["json", "msgpack"])
def test_owned_workflow_persists_and_revokes_across_replicas(h, engine, transport):
    async def scenario():
        browser = await h.login()
        assert (await h.consent(browser)).status_code == 200
        challenge = await h.challenge(browser)
        assert challenge["expires_ms"] - challenge["issued_ms"] == 30000
        enrolled = await h.scan(browser, challenge, engine=engine, transport=transport)
        assert enrolled.status_code == 200, enrolled.text
        assert enrolled.json()["accepted"] and enrolled.json()["template_id"]
        # Close all pooled connections; a fresh repository still sees state.
        h.other_repo.dispose()
        listed = await h.request(
            "GET", f"/v2/subjects/{h.subjects['alice']}/templates", browser, engine=True
        )
        assert listed.status_code == 200 and len(listed.json()["templates"]) == 1
        verify = await h.scan(
            browser,
            await h.challenge(browser, operation="verify"),
            operation="verify",
            engine=engine,
            transport=transport,
        )
        assert verify.status_code == 200 and verify.json()["accepted"]
        revoked = await h.request(
            "DELETE", f"/v2/subjects/{h.subjects['alice']}/templates", browser
        )
        assert revoked.status_code == 200 and revoked.json()["revoked"] == 1
        assert (
            await h.request(
                "GET",
                f"/v2/subjects/{h.subjects['alice']}/templates",
                browser,
                engine=True,
            )
        ).json()["templates"] == []

    run(scenario())


@pytest.mark.parametrize("name", ["bob", "carol", "reviewer", "admin", "operator"])
@pytest.mark.parametrize("engine", [False, True])
def test_database_ownership_cannot_be_overridden_by_roles(h, name, engine):
    browser = run(h.login(name))
    response = run(
        h.request(
            "DELETE",
            f"/v2/subjects/{h.subjects['alice']}/templates",
            browser,
            engine=engine,
        )
    )
    assert response.status_code == 404
    assert h.analyzer.calls == 0


def test_atomic_login_consumption_has_one_winner_across_connections(h):
    attempt = LoginAttempt(
        secrets.token_hex(32),
        secrets.token_hex(32),
        "synthetic-nonce",
        "synthetic-verifier",
        h.now + 300,
    )

    async def race():
        await h.repo.put_login(attempt)
        results = await asyncio.gather(
            *(
                repo.consume_login(attempt.state_hash, attempt.browser_hash, h.now)
                for repo in [h.repo, h.other_repo] * 10
            )
        )
        assert sum(result is not None for result in results) == 1

    run(race())


def test_challenge_binding_replay_and_concurrent_claim(h):
    async def race():
        alice, bob = await h.login(), await h.login("bob")
        await h.consent(alice)
        await h.consent(bob, "bob")
        challenge = await h.challenge(alice)
        assert (await h.scan(bob, challenge, name="bob")).status_code == 409
        results = await asyncio.gather(
            *(h.scan(alice, challenge, engine=bool(i % 2)) for i in range(20))
        )
        assert sum(r.status_code == 200 for r in results) == 1
        assert all(r.status_code in {200, 409} for r in results)
        assert h.analyzer.calls == 1

    run(race())


def test_authority_changes_and_logout_are_seen_by_another_repository(h):
    browser = run(h.login())
    h.idp.logout_outage = True
    assert run(h.request("POST", "/auth/logout", browser)).json()[
        "provider_logout_pending"
    ]
    assert not run(h.other_repo.session(browser.session_id)).active
    h.idp.logout_outage = False
    assert run(drain_logout_outbox(h.engine_sessions))["delivered"] == 1
    browser = run(h.login("bob"))
    run(h.provisioner.set_authority(h.actors["bob"].id, active=False, roles=[]))
    assert run(h.request("GET", "/v2/info", browser, engine=True)).status_code == 401


def test_review_query_filters_before_pagination_and_decrypts_only_scoped_data(h):
    reviewer, alice, admin = (
        run(h.login(name)) for name in ("reviewer", "alice", "admin")
    )
    listed = run(
        h.request("GET", "/v2/rounds/round-1/captures?offset=1&limit=1", reviewer)
    )
    assert listed.status_code == 200 and listed.json() == {
        "captures": [{"id": "capture-bob"}]
    }
    assert run(h.request("GET", "/v2/receipts/receipt-alice", alice)).json() == {
        "recording_status": "saved"
    }
    assert run(h.request("GET", "/v2/captures/capture-alice", admin)).status_code == 404
    path = "/v2/captures/capture-alice/frames/0"
    assert run(h.request("GET", path, reviewer)).status_code == 404
    run(
        h.provisioner.review_grant(
            h.actors["reviewer"].id,
            h.actors["reviewer"].tenant,
            "round-1",
            {Scope.METADATA, Scope.FRAMES},
            h.now + 3600,
        )
    )
    assert (
        run(h.request("GET", path, reviewer)).content
        == b"nonbiometric synthetic frame fixture"
    )
    assert (
        run(h.request("GET", "/v2/captures/capture-carol", reviewer)).status_code == 404
    )


@pytest.mark.parametrize("change", ["logout", "subject", "suspension", "consent"])
def test_commit_rejects_revocation_after_analysis_started(h, change):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)

        async def alter():
            if change == "logout":
                await h.repo.revoke_and_enqueue(browser.session_id)
            elif change == "suspension":
                await h.provisioner.set_authority(
                    h.actors["alice"].id, active=False, roles=[]
                )
            elif change == "consent":
                await h.request(
                    "DELETE",
                    f"/v2/subjects/{h.subjects['alice']}/consents/template_authentication",
                    browser,
                )
            else:
                await h.request(
                    "DELETE", f"/v2/subjects/{h.subjects['alice']}/templates", browser
                )

        h.analyzer.hook = alter
        result = await h.scan(browser, challenge)
        assert result.status_code in {401, 403, 409}
        with h.owner.engine.connect() as connection:
            assert (
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(s.templates)
                    .where(s.templates.c.subject_id == h.subjects["alice"])
                ).scalar_one()
                == 0
            )

    run(scenario())


def test_database_guard_closes_gap_after_provider_recheck(h):
    # Revoke after a valid Access object exists, before acquiring commit locks.
    async def scenario():
        browser = await h.login()
        auth = await h.gateway_sessions.authenticate(cookie=browser.cookie)
        from facetech_auth.contracts import Access
        from facetech_auth.policy import Action

        target = await h.repo.target(
            auth.principal.tenant_id, "subject", h.subjects["alice"]
        )
        access = Access(
            auth, Action.REVOKE_TEMPLATES, target, (), secrets.token_hex(16)
        )
        await h.other_repo.revoke_and_enqueue(browser.session_id)
        with pytest.raises(Denied):
            await h.repo.run(
                lambda connection: h.repo.guard(connection, access, "subject")
            )

    run(scenario())


def test_backchannel_tombstone_prevents_late_session_insertion(h):
    browser = run(h.login())
    session = run(h.repo.session(browser.session_id))
    assert run(
        h.other_repo.backchannel_revoke(
            session.issuer,
            None,
            session.provider_sid,
            "logout-" + secrets.token_hex(8),
            h.now + 330,
        )
    )
    with pytest.raises(Denied):
        run(
            h.repo.replace_session(
                None,
                replace(
                    session,
                    id=secrets.token_hex(16),
                    cookie_hash=digest(secrets.token_hex(32)),
                ),
            )
        )


def test_audit_is_append_only_and_contains_no_template_or_token(h):
    browser = run(h.login())
    with h.owner.engine.connect() as connection:
        values = list(
            connection.execute(
                sa.select(s.audit).where(s.audit.c.actor_id == h.actors["alice"].id)
            ).mappings()
        )
        assert (
            values
            and browser.token not in str(values)
            and browser.cookie not in str(values)
        )
    with pytest.raises(sa.exc.DBAPIError), h.owner.engine.begin() as connection:
        connection.execute(
            s.audit.delete().where(s.audit.c.actor_id == h.actors["alice"].id)
        )


def test_database_outage_is_typed_unavailable_without_fallback(h):
    wrong = PostgresRepository(local_url().set(port=15433))
    from facetech_auth.contracts import Unavailable

    with pytest.raises(Unavailable):
        run(wrong.actor("unknown"))
    wrong.dispose()


def test_audit_commit_failure_rolls_back_template_but_does_not_reuse_nonce(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        constraint = "synthetic_audit_failure_" + secrets.token_hex(8)
        actor_id = h.actors["alice"].id
        # Names/IDs are generated hexadecimal fixture values, never request text.
        with h.owner.engine.begin() as connection:
            connection.execute(
                sa.text(
                    f"ALTER TABLE face_auth.audit ADD CONSTRAINT {constraint} CHECK (NOT(actor_id = '{actor_id}' AND action = 'template.enroll' AND outcome = 'committed')) NOT VALID"
                )
            )
        try:
            assert (await h.scan(browser, challenge)).status_code == 503
            with h.owner.engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.select(sa.func.count())
                        .select_from(s.templates)
                        .where(s.templates.c.subject_id == h.subjects["alice"])
                    ).scalar_one()
                    == 0
                )
        finally:
            with h.owner.engine.begin() as connection:
                connection.execute(
                    sa.text(f"ALTER TABLE face_auth.audit DROP CONSTRAINT {constraint}")
                )
        assert (await h.scan(browser, challenge)).status_code == 409

    run(scenario())


def test_grant_revocation_during_evaluation_read_denies_disclosure(h):
    browser = run(h.login("reviewer"))
    original = h.evaluation.read

    async def withdraw(tenant, identity):
        value = await original(tenant, identity)
        await h.provisioner.revoke_grant(h.grant_id, h.actors["reviewer"].id)
        return value

    h.evaluation.read = withdraw
    response = run(h.request("GET", "/v2/captures/capture-alice/diagnostics", browser))
    assert response.status_code == 404


def test_own_deletion_request_invalidates_capture_access(h):
    alice, reviewer = run(h.login()), run(h.login("reviewer"))
    response = run(h.request("DELETE", "/v2/captures/capture-alice", alice))
    assert response.status_code == 200 and response.json()["deletion_requested"]
    assert (
        run(h.request("GET", "/v2/captures/capture-alice", reviewer)).status_code == 404
    )


def test_subject_generation_binding_rejects_nonce_issued_before_revocation(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        old = await h.challenge(browser)
        assert (
            await h.request(
                "DELETE", f"/v2/subjects/{h.subjects['alice']}/templates", browser
            )
        ).status_code == 200
        assert (await h.scan(browser, old)).status_code == 409
        assert h.analyzer.calls == 0

    run(scenario())


@pytest.mark.parametrize(
    "change",
    ["session", "operation", "expiry", "action", "params", "boolean", "liveness"],
)
def test_durable_challenge_bindings_fail_before_analysis(h, change):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(
            browser, operation="liveness" if change == "liveness" else "enroll"
        )
        operation = "enroll"
        if change == "session":
            browser = await h.login()
        elif change == "operation":
            # Verify refuses an unenrolled subject before any nonce is read (the
            # frozen order), so enroll first and then misuse the enroll nonce.
            assert (await h.scan(browser, challenge)).status_code == 200
            h.analyzer.calls = 0
            challenge = await h.challenge(browser)
            operation = "verify"
        elif change == "expiry":
            h.now += 30.001
        elif change == "action":
            challenge["action"] = "LOOK_LEFT"
        elif change == "params":
            challenge["params"]["settle_ms"] += 1
        elif change == "boolean":
            challenge["params"]["first_sign"] = True
        assert (
            await h.scan(browser, challenge, operation=operation)
        ).status_code == 409
        assert h.analyzer.calls == 0

    run(scenario())


def test_frozen_numeric_echo_and_inclusive_expiry_boundary(h):
    async def scenario():
        browser = await h.login()
        await h.consent(browser)
        challenge = await h.challenge(browser)
        challenge["params"] = {
            key: float(value) for key, value in challenge["params"].items()
        }
        h.now += 30
        assert (await h.scan(browser, challenge)).status_code == 200

    run(scenario())
