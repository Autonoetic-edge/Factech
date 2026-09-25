"""Durable repository with one PostgreSQL transaction per short state operation.

Lock order: actor, session, resource, grant/operation rows. All changes to actor
authority (including logout) take that actor lock. Provider calls and analysis
are always outside transactions. SQL parameter values are never logged.
"""

import asyncio
import hashlib
import secrets
import time
from dataclasses import asdict
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from . import schema as s
from .contracts import Actor, Audit, Denied, LoginAttempt, Session, Target, Unavailable
from .policy import Principal, Resource, ReviewGrant, Role, Scope, authorize


def row(connection, statement):
    return connection.execute(statement).mappings().first()


def actor_record(value):
    return (
        Actor(**{**dict(value), "roles": frozenset(Role(v) for v in value["roles"])})
        if value
        else None
    )


def session_record(value):
    return Session(**dict(value)) if value else None


def target_record(value):
    if not value:
        return None
    return Target(
        value["id"],
        Resource(value["tenant"], value["owner_actor_id"], value["round_id"]),
        value["generation"],
        value["active"],
    )


def grant_record(value):
    return ReviewGrant(
        **{k: v for k, v in value.items() if k not in {"id", "scopes"}},
        scopes=frozenset(Scope(v) for v in value["scopes"]),
    )


SCHEMA_REVISION = "0004_recovery_gate"


def resource_filter(tenant, kind, selector):
    return sa.and_(
        s.resources.c.tenant == tenant,
        s.resources.c.kind == kind,
        s.resources.c.id == selector,
    )


class PostgresRepository:
    def __init__(self, database_url, *, clock=time.time, restore_gate=None):
        url = sa.engine.make_url(database_url)
        if url.drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL + psycopg required; no SQLite fallback")
        self.clock = clock
        self.restore_gate = restore_gate
        self.engine = sa.create_engine(
            url,
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_timeout=5,
            connect_args={"connect_timeout": 5},
        )

    def dispose(self):
        self.engine.dispose()

    def transaction(self, operation):
        """Bounded synchronous transaction; also used for short cross-store writes."""
        try:
            with self.engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL lock_timeout = '3s'"))
                connection.execute(sa.text("SET LOCAL statement_timeout = '8s'"))
                return operation(connection)
        except SQLAlchemyError as exc:
            raise Unavailable() from exc

    async def run(self, operation):
        return await asyncio.to_thread(self.transaction, operation)

    async def ready(self, expected=SCHEMA_REVISION):
        """An application built for another revision must refuse this database."""

        def check(connection):
            revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if revision != expected:
                raise Unavailable()
            return True

        return await self.run(check)

    def actor_lock(self, connection, actor_id):
        actor = actor_record(
            row(
                connection,
                sa.select(s.actors).where(s.actors.c.id == actor_id).with_for_update(),
            )
        )
        if actor is None:
            raise Denied()
        return actor

    @staticmethod
    def identity_locks(connection, issuer, sub, sid):
        # Covers logout racing the very first local session, including sid-only
        # logout where no actor can yet be found through the sessions table.
        for kind, value in (("sub", sub), ("sid", sid)):
            if value:
                digest = hashlib.sha256(f"{issuer}\0{kind}\0{value}".encode()).digest()
                key = int.from_bytes(digest[:8], "big", signed=True)
                connection.execute(
                    sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
                )

    @staticmethod
    def tenant_lock(connection, tenant):
        """Capacity lock; always taken after actor/session/subject row locks."""
        digest = hashlib.sha256(f"tenant\0{tenant}".encode()).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def append_audit(connection, event):
        connection.execute(
            s.audit.insert().values(id=secrets.token_hex(16), **asdict(event))
        )

    async def audit(self, event):
        await self.run(lambda connection: self.append_audit(connection, event))

    async def put_login(self, attempt):
        await self.run(
            lambda connection: connection.execute(
                s.logins.insert().values(**asdict(attempt))
            )
        )

    async def consume_login(self, state_hash, browser_hash, now):
        def consume(connection):
            value = row(
                connection,
                s.logins.delete()
                .where(
                    s.logins.c.state_hash == state_hash,
                    s.logins.c.browser_hash == browser_hash,
                    s.logins.c.expires_at > now,
                )
                .returning(*s.logins.c),
            )
            return LoginAttempt(**value) if value else None

        return await self.run(consume)

    async def actor_by_identity(self, issuer, sub):
        return await self.run(
            lambda connection: actor_record(
                row(
                    connection,
                    sa.select(s.actors).where(
                        s.actors.c.issuer == issuer, s.actors.c.sub == sub
                    ),
                )
            )
        )

    async def actor(self, actor_id):
        return await self.run(
            lambda connection: actor_record(
                row(connection, sa.select(s.actors).where(s.actors.c.id == actor_id))
            )
        )

    async def replace_session(self, old_cookie_hash, session):
        def replace(connection):
            self.identity_locks(
                connection, session.issuer, session.sub, session.provider_sid
            )
            # Serialize cross-account cookie rotation in global actor-ID order.
            old = (
                row(
                    connection,
                    sa.select(s.sessions).where(
                        s.sessions.c.cookie_hash == old_cookie_hash
                    ),
                )
                if old_cookie_hash
                else None
            )
            actors = {session.actor_id}
            if old:
                actors.add(old["actor_id"])
            locked = {key: self.actor_lock(connection, key) for key in sorted(actors)}
            actor = locked[session.actor_id]
            if (
                not actor.active
                or actor.epoch != session.epoch
                or (actor.issuer, actor.sub) != (session.issuer, session.sub)
            ):
                raise Denied()
            now = int(self.clock())
            revoked = row(
                connection,
                sa.select(s.revocations).where(
                    s.revocations.c.issuer == session.issuer,
                    s.revocations.c.expires_at > now,
                    sa.or_(
                        s.revocations.c.sub.is_(None),
                        s.revocations.c.sub == session.sub,
                    ),
                    sa.or_(
                        s.revocations.c.sid.is_(None),
                        s.revocations.c.sid == session.provider_sid,
                    ),
                ),
            )
            if revoked:
                raise Denied()
            if old:
                connection.execute(
                    s.sessions.update()
                    .where(s.sessions.c.id == old["id"])
                    .values(active=False)
                )
            connection.execute(s.sessions.insert().values(**asdict(session)))
            self.append_audit(
                connection,
                Audit(
                    secrets.token_hex(16),
                    actor.id,
                    "session.rotate",
                    session.id,
                    "committed",
                    now,
                ),
            )

        await self.run(replace)

    async def session_by_cookie(self, cookie_hash):
        return await self.run(
            lambda connection: session_record(
                row(
                    connection,
                    sa.select(s.sessions).where(
                        s.sessions.c.cookie_hash == cookie_hash
                    ),
                )
            )
        )

    async def session(self, session_id):
        return await self.run(
            lambda connection: session_record(
                row(
                    connection,
                    sa.select(s.sessions).where(s.sessions.c.id == session_id),
                )
            )
        )

    async def touch(self, session_id, now):
        def touch(connection):
            value = row(
                connection, sa.select(s.sessions).where(s.sessions.c.id == session_id)
            )
            if not value:
                raise Denied()
            actor = self.actor_lock(connection, value["actor_id"])
            if not actor.active or actor.epoch != value["epoch"]:
                raise Denied()
            updated = connection.execute(
                s.sessions.update()
                .where(
                    s.sessions.c.id == session_id,
                    s.sessions.c.active.is_(True),
                    s.sessions.c.expires_at > now,
                    s.sessions.c.created_at > now - 28800,
                    s.sessions.c.touched_at > now - 1800,
                )
                .values(touched_at=sa.func.greatest(s.sessions.c.touched_at, now))
            )
            if updated.rowcount != 1:
                raise Denied()

        await self.run(touch)

    async def rotate_token(self, session_id, token_ciphertext, token_hash):
        def rotate(connection):
            value = row(
                connection, sa.select(s.sessions).where(s.sessions.c.id == session_id)
            )
            if not value:
                raise Denied()
            actor = self.actor_lock(connection, value["actor_id"])
            if not actor.active or actor.epoch != value["epoch"]:
                raise Denied()
            now = int(self.clock())
            updated = connection.execute(
                s.sessions.update()
                .where(
                    s.sessions.c.id == session_id,
                    s.sessions.c.active.is_(True),
                    s.sessions.c.expires_at > now,
                    s.sessions.c.created_at > now - 28800,
                    s.sessions.c.touched_at > now - 1800,
                )
                .values(token_ciphertext=token_ciphertext, token_hash=token_hash)
            )
            if updated.rowcount != 1:
                raise Denied()

        await self.run(rotate)

    async def revoke_and_enqueue(self, session_id):
        def revoke(connection):
            value = row(
                connection, sa.select(s.sessions).where(s.sessions.c.id == session_id)
            )
            if not value:
                raise Denied()
            self.actor_lock(connection, value["actor_id"])
            connection.execute(
                s.sessions.update()
                .where(s.sessions.c.id == session_id)
                .values(active=False)
            )
            connection.execute(
                insert(s.outbox)
                .values(
                    session_id=session_id, attempts=0, next_attempt=0, lease_until=0
                )
                .on_conflict_do_nothing()
            )
            self.append_audit(
                connection,
                Audit(
                    secrets.token_hex(16),
                    value["actor_id"],
                    "session.revoke",
                    session_id,
                    "committed",
                    int(self.clock()),
                ),
            )

        await self.run(revoke)

    async def logout_delivered(self, session_id):
        await self.run(
            lambda connection: connection.execute(
                s.outbox.delete().where(s.outbox.c.session_id == session_id)
            )
        )

    async def claim_logouts(self, *, limit=20):
        def claim(connection):
            now = int(self.clock())
            ids = list(
                connection.execute(
                    sa.select(s.outbox.c.session_id)
                    .where(
                        s.outbox.c.next_attempt <= now,
                        s.outbox.c.lease_until <= now,
                    )
                    .order_by(s.outbox.c.session_id)
                    .limit(min(limit, 100))
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            if not ids:
                return ()
            connection.execute(
                s.outbox.update()
                .where(s.outbox.c.session_id.in_(ids))
                .values(lease_until=now + 30, attempts=s.outbox.c.attempts + 1)
            )
            return tuple(
                session_record(v)
                for v in connection.execute(
                    sa.select(s.sessions).where(
                        s.sessions.c.id.in_(ids), s.sessions.c.active.is_(False)
                    )
                ).mappings()
            )

        return await self.run(claim)

    async def retry_logout_later(self, session_id):
        def failed(connection):
            value = row(
                connection,
                sa.select(s.outbox)
                .where(s.outbox.c.session_id == session_id)
                .with_for_update(),
            )
            if value:
                delay = min(300, 2 ** min(value["attempts"], 8))
                connection.execute(
                    s.outbox.update()
                    .where(s.outbox.c.session_id == session_id)
                    .values(lease_until=0, next_attempt=int(self.clock()) + delay)
                )

        await self.run(failed)

    async def backchannel_revoke(self, issuer, sub, sid, jti, expires_at):
        def revoke(connection):
            now = int(self.clock())
            self.identity_locks(connection, issuer, sub, sid)
            # Lock matching accounts before tracking the provider-session tombstone.
            selector = sa.select(s.actors.c.id).where(s.actors.c.issuer == issuer)
            if sub:
                selector = selector.where(s.actors.c.sub == sub)
            else:
                selector = selector.where(
                    s.actors.c.id.in_(
                        sa.select(s.sessions.c.actor_id).where(
                            s.sessions.c.issuer == issuer,
                            s.sessions.c.provider_sid == sid,
                        )
                    )
                )
            for actor_id in connection.execute(
                selector.order_by(s.actors.c.id)
            ).scalars():
                self.actor_lock(connection, actor_id)
            claimed = connection.execute(
                insert(s.replays)
                .values(issuer=issuer, jti=jti, expires_at=expires_at)
                .on_conflict_do_nothing()
            )
            if claimed.rowcount == 0:
                return False
            connection.execute(
                s.revocations.insert().values(
                    id=secrets.token_hex(16),
                    issuer=issuer,
                    sub=sub,
                    sid=sid,
                    expires_at=now + 28800,
                )
            )
            predicate = [s.sessions.c.issuer == issuer]
            if sub:
                predicate.append(s.sessions.c.sub == sub)
            if sid:
                predicate.append(s.sessions.c.provider_sid == sid)
            connection.execute(
                s.sessions.update().where(*predicate).values(active=False)
            )
            self.append_audit(
                connection,
                Audit(
                    secrets.token_hex(16),
                    None,
                    "session.backchannel",
                    None,
                    "committed",
                    now,
                ),
            )
            return True

        return await self.run(revoke)

    @staticmethod
    def check_reads(connection):
        enabled = connection.execute(
            sa.text("SELECT enabled FROM face_auth.recovery_state WHERE id = 'reads'")
        ).scalar_one_or_none()
        if enabled is not True:
            raise Unavailable()

    async def target(self, tenant, kind, selector):
        if self.restore_gate is not None:
            self.restore_gate.check()
        await self.run(self.check_reads)
        now = int(self.clock())
        return await self.run(
            lambda connection: target_record(
                row(
                    connection,
                    sa.select(s.resources).where(
                        resource_filter(tenant, kind, selector),
                        sa.or_(
                            s.resources.c.expires_at.is_(None),
                            s.resources.c.expires_at > now,
                        ),
                    ),
                )
            )
        )

    def grants_in_transaction(self, connection, actor_id):
        return tuple(
            grant_record(v)
            for v in connection.execute(
                sa.select(s.grants).where(s.grants.c.actor_id == actor_id)
            ).mappings()
        )

    async def grants(self, actor_id):
        return await self.run(
            lambda connection: self.grants_in_transaction(connection, actor_id)
        )

    def guard(self, connection, access, kind):
        """Re-read and lock authorization state in the business commit transaction."""
        if self.restore_gate is not None:
            self.restore_gate.check()
        self.check_reads(connection)
        now = int(self.clock())
        actor = self.actor_lock(connection, access.auth.principal.actor_id)
        session = session_record(
            row(
                connection,
                sa.select(s.sessions)
                .where(s.sessions.c.id == access.auth.session.id)
                .with_for_update(),
            )
        )
        if (
            not session
            or not session.active
            or not actor.active
            or session.actor_id != actor.id
            or session.epoch != actor.epoch
            or session.token_hash != access.auth.session.token_hash
            or (session.issuer, session.sub) != (actor.issuer, actor.sub)
            or actor.tenant != access.auth.principal.tenant_id
            or session.expires_at <= now
            or now - session.created_at >= 28800
            or now - session.touched_at >= 1800
            or access.auth.principal.session_expires_at <= now
        ):
            raise Denied()
        value = row(
            connection,
            sa.select(s.resources)
            .where(resource_filter(actor.tenant, kind, access.target.id))
            .with_for_update(),
        )
        if (
            not value
            or not value["active"]
            or (value["expires_at"] is not None and value["expires_at"] <= now)
        ):
            raise Denied("NOT_FOUND", 404)
        if value["generation"] != access.target.generation:
            raise Denied("RESOURCE_CHANGED", 409)
        target = target_record(value)
        principal = Principal(
            actor.id,
            actor.tenant,
            actor.roles,
            True,
            True,
            min(session.expires_at, session.touched_at + 1800),
            session.authenticated_at,
        )
        decision = authorize(
            principal,
            access.action,
            target.resource,
            now=now,
            grants=self.grants_in_transaction(connection, actor.id),
        )
        if not decision.allowed:
            raise (
                Denied("RECENT_LOGIN_REQUIRED", 403)
                if decision.reason == "recent_login_required"
                else Denied("NOT_FOUND", 404)
            )
        return target


def migrate(owner_engine, revision="head"):
    """Explicit caller-owned DB only. Never called by a service factory/startup."""
    config = AlembicConfig()
    config.set_main_option(
        "script_location", str(Path(__file__).with_name("migrations"))
    )
    with owner_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def recover(connection, ledger, now):
    """Quarantine steps for a restored database, before it may serve requests.

    Revokes every restored session and nonce, fails interrupted operations and
    re-applies the externally kept deletion ledger so an older backup can never
    resurrect a revoked template or a deleted capture. Templates revoked in the
    backup itself stay revoked. Returns counts for the recovery evidence.
    """
    summary = {
        "sessions_revoked": connection.execute(
            s.sessions.update()
            .where(s.sessions.c.active.is_(True))
            .values(active=False)
        ).rowcount,
        "nonces_spent": connection.execute(
            s.challenges.update()
            .where(s.challenges.c.consumed.is_(False))
            .values(consumed=True, consumed_at=now)
        ).rowcount,
        "operations_failed": connection.execute(
            s.operations.update()
            .where(s.operations.c.state == "processing")
            .values(state="failed", status=409, result={"error": "RECOVERED"})
        ).rowcount,
        "ledger_templates": 0,
        "ledger_captures": 0,
    }
    for entry in ledger:
        connection.execute(
            insert(s.deletion_ledger).values(**entry).on_conflict_do_nothing()
        )
        if entry["kind"] == "template":
            summary["ledger_templates"] += connection.execute(
                s.templates.update()
                .where(
                    s.templates.c.tenant == entry["tenant"],
                    s.templates.c.id == entry["id"],
                    s.templates.c.active.is_(True),
                )
                .values(active=False, revoked_at=entry["at"])
            ).rowcount
        else:
            summary["ledger_captures"] += connection.execute(
                s.resources.update()
                .where(
                    resource_filter(entry["tenant"], "capture", entry["id"]),
                    s.resources.c.active.is_(True),
                )
                .values(active=False)
            ).rowcount
    return summary


async def drain_logout_outbox(sessions, *, limit=20):
    delivered = pending = 0
    for session in await sessions.repo.claim_logouts(limit=limit):
        try:
            await sessions.deliver_logout(session)
            delivered += 1
        except Unavailable:
            await sessions.repo.retry_logout_later(session.id)
            pending += 1
    return {"delivered": delivered, "pending": pending}
