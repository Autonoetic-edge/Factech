"""Transactional access revocation and bounded, crash-recoverable purge jobs."""

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import schema as s
from .contracts import Unavailable

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
EVALUATION_TTL = 604800


def schedule(connection, tenant, kind, identity, actor, now):
    connection.execute(
        insert(s.deletion_ledger)
        .values(tenant=tenant, kind=kind, id=identity, actor_id=actor, at=now)
        .on_conflict_do_nothing()
    )
    connection.execute(
        insert(s.purge_jobs)
        .values(
            tenant=tenant,
            kind=kind,
            id=identity,
            attempts=0,
            next_attempt=now,
            lease_until=0,
            state="pending",
        )
        .on_conflict_do_nothing()
    )
    if kind == "template":
        connection.execute(
            s.templates.update()
            .where(s.templates.c.tenant == tenant, s.templates.c.id == identity)
            .values(active=False, revoked_at=now)
        )
    else:
        connection.execute(
            s.recordings.update()
            .where(s.recordings.c.tenant == tenant, s.recordings.c.id == identity)
            .values(state="deleted")
        )
        connection.execute(
            s.resources.update()
            .where(
                s.resources.c.tenant == tenant,
                s.resources.c.kind.in_(["capture", "receipt"]),
                sa.or_(
                    s.resources.c.data_ref == identity, s.resources.c.id == identity
                ),
                s.resources.c.active.is_(True),
            )
            .values(active=False, generation=s.resources.c.generation + 1)
        )


def withdraw(connection, tenant, subject, actor, scope, now):
    if scope == "template_authentication":
        identities = list(
            connection.execute(
                sa.select(s.templates.c.id).where(
                    s.templates.c.tenant == tenant,
                    s.templates.c.subject_id == subject,
                    s.templates.c.active.is_(True),
                )
            ).scalars()
        )
        for identity in identities:
            schedule(connection, tenant, "template", identity, actor, now)
        connection.execute(
            s.operations.update()
            .where(
                s.operations.c.tenant == tenant,
                s.operations.c.subject_id == subject,
                s.operations.c.state == "processing",
            )
            .values(state="failed", status=409, result={"error": "CONSENT_CHANGED"})
        )
    else:
        identities = list(
            connection.execute(
                sa.select(s.recordings.c.id).where(
                    s.recordings.c.tenant == tenant,
                    s.recordings.c.subject_id == subject,
                    s.recordings.c.state != "deleted",
                )
            ).scalars()
        )
        # Include synthetic/legacy records that predate reservations.
        identities += list(
            connection.execute(
                sa.select(s.resources.c.id).where(
                    s.resources.c.tenant == tenant,
                    s.resources.c.owner_actor_id == actor,
                    s.resources.c.kind == "capture",
                    s.resources.c.active.is_(True),
                )
            ).scalars()
        )
        for identity in set(identities):
            schedule(connection, tenant, "capture", identity, actor, now)


class Lifecycle:
    def __init__(self, repository, evaluation, *, tenant=None):
        self.repo, self.evaluation = repository, evaluation
        self.tenant = tenant

    def tenant_filter(self, table):
        return sa.true() if self.tenant is None else table.c.tenant == self.tenant

    async def expire(self, *, limit=100):
        def mark(connection):
            now = int(self.repo.clock())
            # Limits apply per class, never an unbounded deletion transaction.
            templates = list(
                connection.execute(
                    sa.select(s.templates)
                    .where(
                        self.tenant_filter(s.templates),
                        sa.or_(
                            s.templates.c.expires_at <= now,
                            s.templates.c.active.is_(False),
                        ),
                        ~sa.exists(
                            sa.select(s.purge_jobs.c.id).where(
                                s.purge_jobs.c.tenant == s.templates.c.tenant,
                                s.purge_jobs.c.kind == "template",
                                s.purge_jobs.c.id == s.templates.c.id,
                            )
                        ),
                    )
                    .limit(max(1, min(limit, 100)))
                ).mappings()
            )
            captures = list(
                connection.execute(
                    sa.select(s.resources)
                    .where(
                        self.tenant_filter(s.resources),
                        s.resources.c.kind == "capture",
                        sa.or_(
                            s.resources.c.expires_at <= now,
                            s.resources.c.active.is_(False),
                        ),
                        ~sa.exists(
                            sa.select(s.purge_jobs.c.id).where(
                                s.purge_jobs.c.tenant == s.resources.c.tenant,
                                s.purge_jobs.c.kind == "capture",
                                s.purge_jobs.c.id == s.resources.c.id,
                            )
                        ),
                    )
                    .limit(max(1, min(limit, 100)))
                ).mappings()
            )
            pending = list(
                connection.execute(
                    sa.select(s.recordings)
                    .where(
                        self.tenant_filter(s.recordings),
                        s.recordings.c.state == "pending",
                        s.recordings.c.created_at <= now - 120,
                    )
                    .limit(max(1, min(limit, 100)))
                ).mappings()
            )
            for value in templates:
                schedule(
                    connection,
                    value["tenant"],
                    "template",
                    value["id"],
                    value["owner_actor_id"],
                    now,
                )
            for value in captures:
                schedule(
                    connection,
                    value["tenant"],
                    "capture",
                    value["id"],
                    value["owner_actor_id"],
                    now,
                )
            for value in pending:
                schedule(
                    connection,
                    value["tenant"],
                    "capture",
                    value["id"],
                    value["actor_id"],
                    now,
                )
            return len(templates) + len(captures) + len(pending)

        return await self.repo.run(mark)

    async def drain(self, *, limit=20):
        def claim(connection):
            now = int(self.repo.clock())
            jobs = list(
                connection.execute(
                    sa.select(s.purge_jobs)
                    .where(
                        self.tenant_filter(s.purge_jobs),
                        s.purge_jobs.c.state == "pending",
                        s.purge_jobs.c.attempts < MAX_ATTEMPTS,
                        s.purge_jobs.c.next_attempt <= now,
                        s.purge_jobs.c.lease_until <= now,
                    )
                    .order_by(s.purge_jobs.c.id)
                    .limit(max(1, min(limit, 100)))
                    .with_for_update(skip_locked=True)
                ).mappings()
            )
            for job in jobs:
                connection.execute(
                    s.purge_jobs.update()
                    .where(self.selector(job))
                    .values(attempts=job["attempts"] + 1, lease_until=now + 30)
                )
            # Crashes count toward the bound, including the last leased attempt.
            exhausted = connection.execute(
                s.purge_jobs.update()
                .where(
                    self.tenant_filter(s.purge_jobs),
                    s.purge_jobs.c.state == "pending",
                    s.purge_jobs.c.attempts >= MAX_ATTEMPTS,
                    s.purge_jobs.c.lease_until <= now,
                )
                .values(state="blocked", last_error="PURGE_RETRY_EXHAUSTED")
            )
            if exhausted.rowcount:
                logger.error("PURGE_RETRY_EXHAUSTED count=%s", exhausted.rowcount)
            return [dict(job) for job in jobs]

        try:
            jobs = await self.repo.run(claim)
        except Unavailable:
            logger.error("PURGE_STORE_UNAVAILABLE")
            raise
        completed = failed = 0
        for job in jobs:
            try:
                if job["kind"] == "capture":
                    await self.evaluation.delete(job["tenant"], job["id"])

                def finish(connection, job=job):
                    if job["kind"] == "template":
                        connection.execute(
                            s.templates.delete().where(
                                s.templates.c.tenant == job["tenant"],
                                s.templates.c.id == job["id"],
                            )
                        )
                    else:
                        connection.execute(
                            s.resources.delete().where(
                                s.resources.c.tenant == job["tenant"],
                                s.resources.c.kind.in_(["capture", "receipt"]),
                                sa.or_(
                                    s.resources.c.data_ref == job["id"],
                                    s.resources.c.id == job["id"],
                                ),
                            )
                        )
                    connection.execute(
                        s.purge_jobs.update()
                        .where(self.selector(job))
                        .values(state="done", lease_until=0, last_error=None)
                    )

                await self.repo.run(finish)
                completed += 1
            except (Unavailable, OSError):
                failed += 1
                logger.error(
                    "PURGE_FAILED kind=%s attempt=%s", job["kind"], job["attempts"] + 1
                )

                def retry(connection, job=job):
                    connection.execute(
                        s.purge_jobs.update()
                        .where(
                            self.selector(job),
                            s.purge_jobs.c.state == "pending",
                            s.purge_jobs.c.attempts == job["attempts"] + 1,
                        )
                        .values(
                            state="blocked"
                            if job["attempts"] + 1 >= MAX_ATTEMPTS
                            else "pending",
                            last_error="PURGE_FAILED",
                            lease_until=0,
                            next_attempt=int(self.repo.clock())
                            + min(300, 2 ** (job["attempts"] + 1)),
                        )
                    )

                await self.repo.run(retry)
        return {"completed": completed, "failed": failed}

    @staticmethod
    def selector(job):
        return sa.and_(
            s.purge_jobs.c.tenant == job["tenant"],
            s.purge_jobs.c.kind == job["kind"],
            s.purge_jobs.c.id == job["id"],
        )

    async def status(self):
        return await self.repo.run(
            lambda connection: dict(
                connection.execute(
                    sa.select(s.purge_jobs.c.state, sa.func.count())
                    .where(self.tenant_filter(s.purge_jobs))
                    .group_by(s.purge_jobs.c.state)
                ).all()
            )
        )
