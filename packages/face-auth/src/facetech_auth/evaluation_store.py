"""Explicit separate PostgreSQL evaluation port, encrypted and expiry bounded.

No legacy SQLite/store path is accepted. Recording is mediated by the authenticated
M3 reservation/consent transaction; raw data never enters application backups.
"""

import hashlib
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from .contracts import Denied, Unavailable
from .postgres import row

metadata = sa.MetaData(schema="face_evaluation")
records = sa.Table(
    "records",
    metadata,
    sa.Column("tenant", sa.Text, primary_key=True),
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("expires_at", sa.BigInteger, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
)
tombstones = sa.Table(
    "tombstones",
    metadata,
    sa.Column("tenant", sa.Text, primary_key=True),
    sa.Column("id", sa.Text, primary_key=True),
)


class EvaluationStore:
    def __init__(self, repository, cipher):
        self.repo, self.cipher = repository, cipher

    @staticmethod
    def lock(connection, tenant, identity):
        key = int.from_bytes(
            hashlib.sha256(json.dumps([tenant, identity]).encode()).digest()[:8],
            "big",
            signed=True,
        )
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def write_sync(self, tenant, identity, payload, expires_at):
        ciphertext = self.cipher.seal(
            tenant, "evaluation", identity, json.dumps(payload).encode()
        )

        def put(connection):
            self.lock(connection, tenant, identity)
            if expires_at <= int(self.repo.clock()) or row(
                connection,
                sa.select(tombstones).where(
                    tombstones.c.tenant == tenant, tombstones.c.id == identity
                ),
            ):
                raise Denied("RECORDING_DELETED", 409)
            connection.execute(
                insert(records)
                .values(
                    tenant=tenant,
                    id=identity,
                    expires_at=expires_at,
                    ciphertext=ciphertext,
                )
                .on_conflict_do_nothing()
            )

        self.repo.transaction(put)

    async def write(self, tenant, identity, payload, expires_at):
        import asyncio

        await asyncio.to_thread(self.write_sync, tenant, identity, payload, expires_at)

    async def delete(self, tenant, identity):
        def purge(connection):
            self.lock(connection, tenant, identity)
            connection.execute(
                insert(tombstones)
                .values(tenant=tenant, id=identity)
                .on_conflict_do_nothing()
            )
            connection.execute(
                records.delete().where(
                    records.c.tenant == tenant, records.c.id == identity
                )
            )

        await self.repo.run(purge)

    async def rotate(self, *, tenant, after="", limit=100):
        """Bounded cursor walk; rewrap only, preserving ciphertext and original expiry."""

        def rotate(connection):
            values = list(
                connection.execute(
                    sa.select(records)
                    .where(records.c.tenant == tenant, records.c.id > after)
                    .order_by(records.c.id)
                    .limit(max(1, min(limit, 100)))
                    .with_for_update(skip_locked=True)
                ).mappings()
            )
            for value in values:
                rewritten = self.cipher.rewrap(
                    tenant, "evaluation", value["id"], value["ciphertext"]
                )
                connection.execute(
                    records.update()
                    .where(records.c.tenant == tenant, records.c.id == value["id"])
                    .values(ciphertext=rewritten)
                )
            return {
                "visited": len(values),
                "after": values[-1]["id"] if values else after,
            }

        return await self.repo.run(rotate)

    async def read(self, tenant, identity):
        value = await self.repo.run(
            lambda connection: row(
                connection,
                sa.select(records).where(
                    records.c.tenant == tenant,
                    records.c.id == identity,
                    records.c.expires_at > int(self.repo.clock()),
                ),
            )
        )
        if not value:
            raise Denied("NOT_FOUND", 404)
        try:
            return json.loads(
                self.cipher.open(tenant, "evaluation", identity, value["ciphertext"])
            )
        except (ValueError, TypeError) as exc:
            raise Unavailable() from exc
