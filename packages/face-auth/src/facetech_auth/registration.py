"""Opt-in preview registration, limited to the configured participant tenant.

Unlike the offline Provisioner, this cannot grant roles, choose an owner, or
change an existing account. Call only after validating the provider callback.
"""

import secrets

import sqlalchemy as sa

from . import schema as s
from .contracts import Audit, Denied
from .policy import Role
from .postgres import actor_record
from .provision import MAX_SUBJECTS


class ParticipantRegistration:
    def __init__(self, repository, *, tenant):
        if not tenant:
            raise ValueError("Registration tenant required")
        self.repo, self.tenant = repository, tenant

    async def __call__(self, issuer, sub):
        def create(connection):
            # Serialize registration/capacity checks; repeated callbacks converge
            # on the same account without replacing templates or reactivating it.
            self.repo.tenant_lock(connection, self.tenant)
            existing = (
                connection.execute(
                    sa.select(s.actors).where(
                        s.actors.c.issuer == issuer, s.actors.c.sub == sub
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return actor_record(existing)
            count = connection.execute(
                sa.select(sa.func.count())
                .select_from(s.resources)
                .where(
                    s.resources.c.tenant == self.tenant,
                    s.resources.c.kind == "subject",
                    s.resources.c.active.is_(True),
                )
            ).scalar_one()
            if count >= MAX_SUBJECTS:
                raise Denied("CAPACITY_EXCEEDED", 409)
            actor = {
                "id": secrets.token_hex(16),
                "issuer": issuer,
                "sub": sub,
                "tenant": self.tenant,
                "roles": [Role.PARTICIPANT],
                "epoch": 0,
                "active": True,
            }
            connection.execute(s.actors.insert().values(**actor))
            connection.execute(
                s.resources.insert().values(
                    tenant=self.tenant,
                    kind="subject",
                    id=secrets.token_hex(16),
                    owner_actor_id=actor["id"],
                    round_id=None,
                    generation=0,
                    active=True,
                )
            )
            self.repo.append_audit(
                connection,
                Audit(
                    secrets.token_hex(16),
                    actor["id"],
                    "account.register",
                    actor["id"],
                    "committed",
                    int(self.repo.clock()),
                ),
            )
            return actor_record(actor)

        return await self.repo.run(create)
