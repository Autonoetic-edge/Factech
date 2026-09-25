"""Synthetic recovery safety boundary; deployment orchestration belongs to M7.

The caller supplies an independently retained, authenticated ledger and a trusted
latest-acknowledgement checkpoint, not timestamps read from the restored backup.
The gate starts closed and is not stored in the backup it protects.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass, field

import sqlalchemy as sa

from . import schema as s
from .contracts import Unavailable
from .lifecycle import schedule


@dataclass
class RestoreGate:
    required_through: int
    required_digest: str | None = None
    max_age: int = 300
    enabled: bool = field(default=False, init=False)

    def check(self):
        if not self.enabled:
            raise Unavailable()

    def validate(self, snapshot, now, ledger):
        if (
            not self.required_digest
            or not hmac.compare_digest(
                hashlib.sha256(ledger).hexdigest(), self.required_digest
            )
            or not isinstance(snapshot, dict)
            or type(snapshot.get("through")) is not int
            or not isinstance(snapshot.get("entries"), list)
            or not isinstance(snapshot.get("consents"), list)
            or snapshot["through"] < self.required_through
            or snapshot["through"] > now
            or now - snapshot["through"] > self.max_age
        ):
            raise Unavailable()


def quarantine(connection):
    """Commit before loading an allowed backup; keep listeners disconnected."""
    connection.execute(
        sa.text(
            "UPDATE face_auth.recovery_state SET enabled = false WHERE id = 'reads'"
        )
    )


def seal_ledger(connection, cipher, *, identity, now):
    entries = [
        dict(value)
        for value in connection.execute(sa.select(s.deletion_ledger)).mappings()
    ]
    # Current consent states are also necessary: withdrawal may have no template yet.
    consents = [
        dict(value) for value in connection.execute(sa.select(s.consents)).mappings()
    ]
    return cipher.seal(
        "recovery",
        "backup",
        identity,
        json.dumps({"through": now, "entries": entries, "consents": consents}).encode(),
    )


async def enable_restored_reads(
    repository, gate, cipher, ledger, *, identity, template_cipher, model_ids
):
    from .postgres import recover

    gate.enabled = False
    await repository.run(quarantine)
    snapshot = json.loads(cipher.open("recovery", "backup", identity, ledger))
    now = int(repository.clock())
    gate.validate(snapshot, now, ledger)

    def apply(connection):
        from sqlalchemy.dialects.postgresql import insert

        summary = recover(connection, snapshot["entries"], now)
        for consent in snapshot["consents"]:
            connection.execute(
                insert(s.consents)
                .values(**consent)
                .on_conflict_do_update(
                    index_elements=["tenant", "subject_id", "scope"],
                    set_={
                        k: v
                        for k, v in consent.items()
                        if k not in {"tenant", "subject_id", "scope"}
                    },
                    where=s.consents.c.revision <= consent["revision"],
                )
            )
        for template in connection.execute(sa.select(s.templates)).mappings():
            allowed = connection.execute(
                sa.select(s.consents.c.subject_id).where(
                    s.consents.c.tenant == template["tenant"],
                    s.consents.c.subject_id == template["subject_id"],
                    s.consents.c.scope == "template_authentication",
                    s.consents.c.granted.is_(True),
                    s.consents.c.text_version == "template-authentication-v1",
                    s.consents.c.actor_id == template["owner_actor_id"],
                )
            ).first()
            if (
                not template["active"]
                or template["expires_at"] is None
                or template["expires_at"] <= now
                or not allowed
            ):
                schedule(
                    connection,
                    template["tenant"],
                    "template",
                    template["id"],
                    template["owner_actor_id"],
                    now,
                )
                connection.execute(
                    s.templates.delete().where(s.templates.c.id == template["id"])
                )
            else:
                if template["model_fingerprint"] not in model_ids:
                    raise Unavailable()
                template_cipher.open(
                    template["tenant"],
                    "template",
                    template["id"],
                    template["ciphertext"],
                    expected_version=template["key_version"],
                )
        # Restored evaluation links are never readable: raw evaluation backups are forbidden.
        for capture in connection.execute(
            sa.select(s.resources).where(s.resources.c.kind == "capture")
        ).mappings():
            schedule(
                connection,
                capture["tenant"],
                "capture",
                capture["id"],
                capture["owner_actor_id"],
                now,
            )
        connection.execute(
            sa.text(
                "UPDATE face_auth.recovery_state SET enabled = true, checkpoint = :checkpoint WHERE id = 'reads'"
            ),
            {"checkpoint": snapshot["through"]},
        )
        return summary

    summary = await repository.run(apply)
    gate.enabled = True  # only after the recovery transaction committed successfully
    return summary


BACKUP_TABLES = (
    s.actors,
    s.resources,
    s.templates,
    s.consents,
    s.consent_events,
    s.deletion_ledger,
    s.purge_jobs,
    s.audit,
    s.grants,
)


def backup_queries(now):
    """Explicit logical scope. No raw store, sessions/tokens/nonces/results/receipts."""
    for table in BACKUP_TABLES:
        query = sa.select(table)
        if table is s.resources:
            query = query.where(s.resources.c.kind.in_(["subject", "round"]))
        if table is s.templates:
            query = query.where(
                s.templates.c.active.is_(True), s.templates.c.expires_at > now
            )
        if table is s.audit:
            query = query.where(s.audit.c.at > now - 90 * 86400)
        yield table, query


async def rotate_templates(repository, cipher, *, limit=100):
    def rotate(connection):
        rows = list(
            connection.execute(
                sa.select(s.templates)
                .where(s.templates.c.key_version != cipher.active_version)
                .order_by(s.templates.c.id)
                .limit(max(1, min(limit, 100)))
                .with_for_update(skip_locked=True)
            ).mappings()
        )
        for value in rows:
            rewritten = cipher.rewrap(
                value["tenant"], "template", value["id"], value["ciphertext"]
            )
            connection.execute(
                s.templates.update()
                .where(s.templates.c.id == value["id"])
                .values(
                    ciphertext=rewritten,
                    key_version=cipher.decode(rewritten)["key_version"],
                )
            )
        return len(rows)

    return await repository.run(rotate)
