"""Registration storage uses the owned loopback PostgreSQL, synthetic identities only."""

import asyncio
import secrets

import pytest
import sqlalchemy as sa
from facetech_auth import schema as s
from facetech_auth.policy import Role
from facetech_auth.postgres import PostgresRepository
from facetech_auth.registration import ParticipantRegistration
from postgres_fixtures import local_url


def test_concurrent_registration_is_isolated_and_does_not_reactivate(request):
    if not request.config.getoption("--hardening-postgres"):
        pytest.skip("owned loopback PostgreSQL required")
    repo = PostgresRepository(local_url())
    tenant = "register-test-" + secrets.token_hex(8)
    issuer = "https://synthetic-registration.test/realm"
    register = ParticipantRegistration(repo, tenant=tenant)

    async def check():
        a, repeated, b = await asyncio.gather(
            register(issuer, tenant + "-a"),
            register(issuer, tenant + "-a"),
            register(issuer, tenant + "-b"),
        )
        assert a.id == repeated.id and a.id != b.id
        assert a.roles == b.roles == frozenset({Role.PARTICIPANT})
        with repo.engine.begin() as c:
            subjects = (
                c.execute(sa.select(s.resources).where(s.resources.c.tenant == tenant))
                .mappings()
                .all()
            )
            assert len(subjects) == 2
            assert {x["owner_actor_id"] for x in subjects} == {a.id, b.id}
            assert len({x["id"] for x in subjects}) == 2
            c.execute(
                s.actors.update().where(s.actors.c.id == a.id).values(active=False)
            )
        assert (await register(issuer, tenant + "-a")).active is False

    try:
        asyncio.run(check())
    finally:
        repo.dispose()
