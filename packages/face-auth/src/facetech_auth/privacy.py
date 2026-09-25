"""Gateway recording saga: durable reservation, separate encrypted store, commit.

Reservation and activation serialize on the same subject lock as consent changes.
No raw data enters the app database. A failed/interrupted reservation is unreadable
and reclaimable. Evaluation tombstones prevent a delayed writer outrunning purge.
"""

import base64
import hashlib
import json
import logging

import sqlalchemy as sa

from . import schema as s
from .contracts import Denied, Unavailable
from .lifecycle import EVALUATION_TTL, schedule
from .postgres import resource_filter, row

logger = logging.getLogger(__name__)
VERSIONS = {
    "template_authentication": "template-authentication-v1",
    "evaluation_recording": "evaluation-recording-v1",
}


def consent_revision(connection, tenant, subject, actor, scope):
    value = row(
        connection,
        sa.select(s.consents).where(
            s.consents.c.tenant == tenant,
            s.consents.c.subject_id == subject,
            s.consents.c.scope == scope,
        ),
    )
    if (
        not value
        or not value["granted"]
        or value["actor_id"] != actor
        or value["text_version"] != VERSIONS[scope]
    ):
        return None
    return value["revision"]


class Recording:
    def __init__(self, repository, evaluation, *, round_id):
        if not round_id:
            raise ValueError("Explicit evaluation round required")
        self.repo, self.evaluation, self.round_id = repository, evaluation, round_id

    async def begin(self, access, payload):
        def reserve(connection):
            self.repo.guard(connection, access, "subject")
            tenant, actor, subject = (
                access.auth.principal.tenant_id,
                access.auth.principal.actor_id,
                access.target.id,
            )
            identity = hashlib.sha256(
                json.dumps([tenant, actor, payload["idempotency_key"]]).encode()
            ).hexdigest()
            existing = row(
                connection,
                sa.select(s.recordings).where(
                    s.recordings.c.tenant == tenant, s.recordings.c.id == identity
                ),
            )
            if existing:
                return {**dict(existing), "existing": True}
            # A later privacy choice never retroactively records a completed (or
            # in-progress direct-engine) operation, including rejected replays.
            if row(
                connection,
                sa.select(s.operations.c.id).where(
                    s.operations.c.actor_id == actor,
                    s.operations.c.key == payload["idempotency_key"],
                ),
            ):
                return {"state": "skipped", "reason": "NO_RECORDING_FOR_REPLAY"}
            revision = consent_revision(
                connection, tenant, subject, actor, "evaluation_recording"
            )
            if revision is None:
                return {"state": "skipped", "reason": "NO_EVALUATION_CONSENT"}
            round_row = row(
                connection,
                sa.select(s.resources).where(
                    resource_filter(tenant, "round", self.round_id),
                    s.resources.c.active.is_(True),
                    s.resources.c.expires_at > int(self.repo.clock()),
                ),
            )
            if not round_row:
                return {"state": "skipped", "reason": "ROUND_CLOSED"}
            now = int(self.repo.clock())
            challenge = payload["scan"].get("challenge")
            nonce = challenge.get("nonce") if isinstance(challenge, dict) else None
            issued = row(
                connection,
                sa.select(s.challenges.c.issued_at).where(
                    s.challenges.c.digest
                    == hashlib.sha256(str(nonce).encode()).hexdigest(),
                    s.challenges.c.actor_id == actor,
                    s.challenges.c.subject_id == subject,
                    s.challenges.c.session_id == access.auth.session.id,
                ),
            )
            if not issued:
                return {"state": "skipped", "reason": "NO_BOUND_CHALLENGE"}
            value = {
                "tenant": tenant,
                "id": identity,
                "subject_id": subject,
                "actor_id": actor,
                "round_id": self.round_id,
                "revision": revision,
                "state": "pending",
                "created_at": now,
                "expires_at": min(
                    issued["issued_at"] // 1000 + EVALUATION_TTL,
                    round_row["expires_at"],
                ),
            }
            connection.execute(s.recordings.insert().values(**value))
            return value

        return await self.repo.run(reserve)

    async def finish(self, access, payload, ticket, decision, reauthorize):
        if ticket["state"] == "skipped":
            return {"status": "skipped", "reason": ticket["reason"]}
        tenant, identity = ticket["tenant"], ticket["id"]
        if ticket["state"] == "deleted" or ticket["expires_at"] <= int(
            self.repo.clock()
        ):
            return {"status": "skipped", "reason": "RECORDING_DELETED"}
        try:
            fresh = await reauthorize()

            frames = []
            raw_frames = payload["scan"].get("frames")
            for frame in raw_frames if isinstance(raw_frames, list) else []:
                if isinstance(frame, dict) and isinstance(
                    frame.get("jpeg_bytes"), bytes
                ):
                    frames.append(base64.b64encode(frame["jpeg_bytes"]).decode())
            content = {
                "scan": base64.b64encode(payload["scan_bytes"]).decode(),
                "frames": frames,
                "metadata": {"operation_id": decision.get("operation_id")},
                "diagnostics": {
                    "accepted": decision.get("accepted"),
                    "error": decision.get("error"),
                },
                "recording_status": "stored",
            }

            def activate(connection):
                self.repo.guard(connection, fresh, "subject")
                current = row(
                    connection,
                    sa.select(s.recordings)
                    .where(
                        s.recordings.c.tenant == tenant, s.recordings.c.id == identity
                    )
                    .with_for_update(),
                )
                now = int(self.repo.clock())
                if (
                    not current
                    or current["state"] == "deleted"
                    or current["expires_at"] <= now
                    or consent_revision(
                        connection,
                        tenant,
                        ticket["subject_id"],
                        ticket["actor_id"],
                        "evaluation_recording",
                    )
                    != ticket["revision"]
                ):
                    schedule(
                        connection, tenant, "capture", identity, ticket["actor_id"], now
                    )
                    return {"status": "skipped", "reason": "CONSENT_CHANGED"}
                if current["state"] == "stored":
                    return {"status": "stored", "receipt_id": identity}
                if decision.get("replayed") or ticket.get("existing"):
                    return {"status": "pending", "reason": "RECONCILIATION_REQUIRED"}
                completed = row(
                    connection,
                    sa.select(s.operations).where(
                        s.operations.c.actor_id == ticket["actor_id"],
                        s.operations.c.subject_id == ticket["subject_id"],
                        s.operations.c.key == payload["idempotency_key"],
                        s.operations.c.request_digest == payload["scan_digest"],
                        s.operations.c.state == "completed",
                    ),
                )
                if not completed:
                    schedule(
                        connection, tenant, "capture", identity, ticket["actor_id"], now
                    )
                    return {"status": "skipped", "reason": "NO_COMPLETED_OPERATION"}
                # Keep the subject/consent lock across this bounded separate-store
                # write. Withdrawal either wins before any bytes are stored, or
                # follows a committed recording and schedules deletion. If the app
                # transaction fails after the evaluation commit, the durable pending
                # reservation reclaims the inaccessible ciphertext after restart.
                self.evaluation.write_sync(
                    tenant, identity, content, ticket["expires_at"]
                )
                for kind in ("capture", "receipt"):
                    connection.execute(
                        s.resources.insert().values(
                            tenant=tenant,
                            kind=kind,
                            id=identity,
                            owner_actor_id=ticket["actor_id"],
                            round_id=ticket["round_id"],
                            generation=0,
                            data_ref=identity,
                            active=True,
                            expires_at=ticket["expires_at"],
                        )
                    )
                connection.execute(
                    s.recordings.update()
                    .where(
                        s.recordings.c.tenant == tenant, s.recordings.c.id == identity
                    )
                    .values(state="stored")
                )
                from .contracts import Audit

                self.repo.append_audit(
                    connection,
                    Audit(
                        access.request_id,
                        ticket["actor_id"],
                        "evaluation.record",
                        identity,
                        "stored",
                        now,
                    ),
                )
                return {"status": "stored", "receipt_id": identity}

            return await self.repo.run(activate)
        except (Denied, OSError):
            logger.error("RECORDING_FAILED")
            # The reservation survives a store/disk outage; expiry scanner retries.
            try:
                await self.repo.run(
                    lambda connection: schedule(
                        connection,
                        tenant,
                        "capture",
                        identity,
                        ticket["actor_id"],
                        int(self.repo.clock()),
                    )
                )
            except Unavailable:
                logger.error("RECORDING_CLEANUP_PENDING")
            return {"status": "failed", "reason": "RECORDING_UNAVAILABLE"}
