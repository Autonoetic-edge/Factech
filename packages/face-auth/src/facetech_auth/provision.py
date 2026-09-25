"""Offline, audited account provisioning and authority changes.

Requires a separate owner/provisioner DB credential, never exposed as a browser
endpoint. Provisioned issuer/sub pairs must come from a verified identity roster.
No enrollment, template-management, legacy alias or delegation function exists.
"""

import secrets

import sqlalchemy as sa

from . import schema as s
from .contracts import Actor, Audit, Denied
from .policy import Role, Scope

MAX_SUBJECTS = 1000


class Provisioner:
    def __init__(self, repository, *, administrator_id: str):
        if not administrator_id:
            raise ValueError("Attributable provisioner identity required")
        self.repo, self.administrator_id = repository, administrator_id

    def audit(self, connection, action, identity):
        self.repo.append_audit(
            connection,
            Audit(
                secrets.token_hex(16),
                self.administrator_id,
                action,
                identity,
                "committed",
                int(self.repo.clock()),
            ),
        )

    async def create_account(self, issuer, sub, tenant, roles):
        roles = frozenset(Role(role) for role in roles)
        if not issuer.startswith("https://") or not sub or not tenant:
            raise ValueError("Verified issuer/sub and explicit tenant required")
        actor = Actor(secrets.token_hex(16), issuer, sub, tenant, roles)
        subject = secrets.token_hex(16) if Role.PARTICIPANT in roles else None

        def create(connection):
            connection.execute(
                s.actors.insert().values(
                    id=actor.id,
                    issuer=issuer,
                    sub=sub,
                    tenant=tenant,
                    roles=sorted(roles),
                    epoch=0,
                    active=True,
                )
            )
            if subject:
                # Explicit team capacity; the legacy store evicted silently.
                self.repo.tenant_lock(connection, tenant)
                active = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(s.resources)
                    .where(
                        s.resources.c.tenant == tenant,
                        s.resources.c.kind == "subject",
                        s.resources.c.active.is_(True),
                    )
                ).scalar_one()
                if active >= MAX_SUBJECTS:
                    raise Denied("CAPACITY_EXCEEDED", 409)
                connection.execute(
                    s.resources.insert().values(
                        tenant=tenant,
                        kind="subject",
                        id=subject,
                        owner_actor_id=actor.id,
                        round_id=None,
                        generation=0,
                        active=True,
                    )
                )
            self.audit(connection, "account.provision", actor.id)
            return actor, subject

        return await self.repo.run(create)

    async def set_authority(self, actor_id, *, active, roles):
        roles = sorted(Role(role) for role in roles)
        if actor_id == self.administrator_id:
            raise Denied("SELF_ESCALATION_FORBIDDEN", 403)

        def change(connection):
            self.repo.actor_lock(connection, actor_id)
            connection.execute(
                s.actors.update()
                .where(s.actors.c.id == actor_id)
                .values(active=active, roles=roles, epoch=s.actors.c.epoch + 1)
            )
            connection.execute(
                s.sessions.update()
                .where(s.sessions.c.actor_id == actor_id)
                .values(active=False)
            )
            self.audit(connection, "account.authority_changed", actor_id)

        await self.repo.run(change)

    async def review_grant(
        self, actor_id, tenant, round_id, scopes, expires_at, *, revoked=False
    ):
        scopes = sorted(Scope(scope) for scope in scopes)
        identity = secrets.token_hex(16)
        if actor_id == self.administrator_id:
            raise Denied("SELF_ESCALATION_FORBIDDEN", 403)

        def grant(connection):
            actor = self.repo.actor_lock(connection, actor_id)
            if actor.tenant != tenant or Role.REVIEWER not in actor.roles:
                raise Denied("NOT_FOUND", 404)
            connection.execute(
                s.grants.insert().values(
                    id=identity,
                    actor_id=actor_id,
                    tenant_id=tenant,
                    round_id=round_id,
                    scopes=scopes,
                    expires_at=expires_at,
                    revoked=revoked,
                )
            )
            self.audit(connection, "review.grant", identity)
            return identity

        return await self.repo.run(grant)

    async def revoke_grant(self, grant_id, actor_id):
        def revoke(connection):
            self.repo.actor_lock(connection, actor_id)
            connection.execute(
                s.grants.update()
                .where(s.grants.c.id == grant_id, s.grants.c.actor_id == actor_id)
                .values(revoked=True)
            )
            self.audit(connection, "review.revoke", grant_id)

        await self.repo.run(revoke)
