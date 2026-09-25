"""Authorization-aware operations; analysis is injected, never bypassed.

Every scan is one durable operation: the idempotency claim and the conditional
nonce spend commit in a short transaction *before* analysis, analysis runs
outside any transaction, then provider revalidation and a locked commit recheck
actor/session/subject generation/consent/capacity with the result and audit.
The default analyzer is unavailable; `inference.FrozenAnalyzer` wires the
unchanged engine pipeline and tests inject only synthetic nonbiometric data.
"""

import base64
import contextlib
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import schema as s
from .contracts import Audit, Denied, Unavailable
from .encryption import DataCipher  # compatibility public import
from .lifecycle import schedule, withdraw
from .policy import Action
from .postgres import resource_filter, row
from .privacy import VERSIONS
from .provision import MAX_SUBJECTS

MAX_TEMPLATES = 5
OPERATION_TTL = 86400
# ponytail: 30 s nonce TTL + 15 s PAD deadline + margin; a worker lost for longer
# leaves the operation "interrupted" and the participant captures again.
PROCESSING_LEASE = 120
KEY_VERSION = "v1"


@dataclass(frozen=True)
class Analysis:
    accepted: bool
    result: dict
    template: bytes | None = field(default=None, repr=False)
    error: str | None = None
    status: int = 200


@dataclass(frozen=True)
class FrameBody:
    content: bytes = field(repr=False)
    media_type: str = "image/jpeg"


class Analyzer(Protocol):
    format_id: str

    async def analyze(
        self,
        operation: str,
        scan: bytes,
        templates: tuple[bytes, ...],
        *,
        request_id: str,
        challenge: dict,
        precheck: str | None = None,
    ) -> Analysis:
        """Enforce all frozen scan/model/PAD/match gates; never trust scan verdicts.

        `scan` is the raw FaceScan bytes; `challenge` is the durable, already
        spent record (subject, action, params, age_ms). `precheck` is the page's
        raw pre-check header, for the decision trace only. A rejection returns
        Analysis(error=code, status=http) so it is persisted as the outcome.
        """
        ...


class MissingAnalyzer:
    format_id = "unconfigured"

    async def analyze(
        self, operation, scan, templates, *, request_id, challenge, precheck=None
    ):
        raise Unavailable()


class MissingEvaluation:
    async def read(self, tenant, identity):
        raise Unavailable()


def head_parameters_match(presented, issued):
    """Preserve the frozen HEAD_SEQUENCE numeric echo semantics (no booleans)."""
    return (
        isinstance(presented, dict)
        and set(presented) == set(issued)
        and all(
            type(presented[k]) in (int, float) and presented[k] == v
            for k, v in issued.items()
        )
    )


def active_templates(tenant, subject, now=None):
    return sa.and_(
        s.templates.c.tenant == tenant,
        s.templates.c.subject_id == subject,
        s.templates.c.active.is_(True),
        sa.true() if now is None else s.templates.c.expires_at > now,
    )


def spend_nonce(connection, nonce_digest, binding, now_ms, operation_id):
    """Conditional single-row spend of a fully bound, unexpired nonce.

    The affected row is the race decision: concurrent callers in any process
    see at most one returned row. Expiry is inclusive, as the frozen engine's.
    """
    return row(
        connection,
        s.challenges.update()
        .where(
            s.challenges.c.digest == nonce_digest,
            s.challenges.c.consumed.is_(False),
            s.challenges.c.expires_at >= now_ms,
            s.challenges.c.actor_id == binding["actor_id"],
            s.challenges.c.session_id == binding["session_id"],
            s.challenges.c.tenant == binding["tenant"],
            s.challenges.c.subject_id == binding["subject_id"],
            s.challenges.c.generation == binding["generation"],
            s.challenges.c.operation == binding["operation"],
        )
        .values(consumed=True, consumed_at=now_ms, operation_id=operation_id)
        .returning(s.challenges.c.parameters, s.challenges.c.issued_at),
    )


class PostgresOperations:
    def __init__(
        self,
        repository,
        cipher: DataCipher,
        *,
        analyzer=None,
        evaluation=None,
        recording=None,
        template_expires_at=None,
    ):
        self.repo, self.cipher = repository, cipher
        self.recording = recording
        self.template_expires_at = template_expires_at
        self.analyzer = analyzer if analyzer is not None else MissingAnalyzer()
        self.evaluation = evaluation if evaluation is not None else MissingEvaluation()

    def challenge_parameters(self):
        """The analyzer's challenge draw under CHALLENGE_POLICY single-turn-v1/-glow-v1
        (`inference.FrozenAnalyzer`); None (no such analyzer, or any other policy) keeps
        today's HEAD_SEQUENCE challenge."""
        draw = getattr(self.analyzer, "challenge_parameters", None)
        return draw() if draw is not None else None

    def audit(self, connection, access, outcome="committed"):
        self.repo.append_audit(
            connection,
            Audit(
                access.request_id,
                access.auth.principal.actor_id,
                access.action,
                access.target.id,
                outcome,
                int(self.repo.clock()),
            ),
        )

    @staticmethod
    def kind(action):
        if action == Action.READ_RECEIPT:
            return "receipt"
        if action in {Action.LIST_CAPTURES, Action.READ_AGGREGATES}:
            return "round"
        if action in {
            Action.READ_CAPTURE,
            Action.READ_DIAGNOSTICS,
            Action.READ_FRAME,
            Action.REQUEST_CAPTURE_DELETION,
        }:
            return "capture"
        return "subject"

    def consent(self, connection, access):
        value = row(
            connection,
            sa.select(s.consents).where(
                s.consents.c.tenant == access.auth.principal.tenant_id,
                s.consents.c.subject_id == access.target.id,
                s.consents.c.scope == "template_authentication",
            ),
        )
        if (
            not value
            or not value["granted"]
            or value["actor_id"] != access.auth.principal.actor_id
            or value["text_version"] != "template-authentication-v1"
        ):
            raise Denied("CONSENT_REQUIRED", 403)
        return value["revision"]

    def capacity(self, connection, tenant, subject, active, *, lock):
        """Explicit CAPACITY_EXCEEDED; the legacy store evicted the oldest silently."""
        if active >= MAX_TEMPLATES:
            raise Denied("CAPACITY_EXCEEDED", 409)
        if active == 0:
            if lock:
                self.repo.tenant_lock(connection, tenant)
            # ponytail: count(distinct) per first enrollment; a maintained capacity
            # row if enrollment volume ever matters.
            enrolled = connection.execute(
                sa.select(sa.func.count(sa.distinct(s.templates.c.subject_id))).where(
                    s.templates.c.tenant == tenant, s.templates.c.active.is_(True)
                )
            ).scalar_one()
            if enrolled >= MAX_SUBJECTS:
                raise Denied("CAPACITY_EXCEEDED", 409)

    async def execute(self, access, payload, reauthorize):
        action = access.action
        if action in {
            Action.ENROLL,
            Action.ADD_TEMPLATE,
            Action.VERIFY,
            Action.LIVENESS,
        }:
            return await self.scan(access, payload, reauthorize)
        # Fetch ciphertext through the separate evaluation port only after an
        # authorized audit write; decode no biometric payload during identity lookup.
        evaluation = None
        if action in {
            Action.READ_RECEIPT,
            Action.READ_CAPTURE,
            Action.READ_DIAGNOSTICS,
            Action.READ_FRAME,
        }:

            def reference(connection):
                self.repo.guard(connection, access, self.kind(action))
                return row(
                    connection,
                    sa.select(s.resources.c.data_ref).where(
                        resource_filter(
                            access.auth.principal.tenant_id,
                            self.kind(action),
                            access.target.id,
                        )
                    ),
                )["data_ref"]

            reference_id = await self.repo.run(reference)
            evaluation = await self.evaluation.read(
                access.auth.principal.tenant_id, reference_id
            )
        fresh = await reauthorize()

        def operation(connection):
            target = self.repo.guard(connection, fresh, self.kind(action))
            tenant, subject = fresh.auth.principal.tenant_id, target.id
            actor_id = fresh.auth.principal.actor_id
            now = int(self.repo.clock())
            if action.startswith("challenge."):
                issued_ms = int(self.repo.clock() * 1000)
                nonce = secrets.token_hex(16)
                # Single-turn policies: the engine's own draw, glow schedule included,
                # stored below so it is bound to the nonce. Otherwise today's challenge.
                parameters = self.challenge_parameters() or {
                    "action": "HEAD_SEQUENCE",
                    "params": {
                        "settle_ms": 3200,
                        "switch_ms": 9000 + secrets.randbelow(3) * 200,
                        "first_sign": secrets.choice((-1, 1)),
                        "target": 0.18,
                    },
                }
                connection.execute(
                    s.challenges.insert().values(
                        digest=hashlib.sha256(nonce.encode()).hexdigest(),
                        actor_id=actor_id,
                        session_id=fresh.auth.session.id,
                        tenant=tenant,
                        subject_id=subject,
                        generation=target.generation,
                        operation=action.split(".")[1],
                        parameters=parameters,
                        issued_at=issued_ms,
                        expires_at=issued_ms + 30000,
                        consumed=False,
                    )
                )
                result = {
                    "nonce": nonce,
                    **parameters,
                    "issued_ms": issued_ms,
                    "expires_ms": issued_ms + 30000,
                }
            elif action == Action.LIST_TEMPLATES:
                rows = connection.execute(
                    sa.select(
                        s.templates.c.id,
                        s.templates.c.created_at,
                        s.templates.c.model_fingerprint,
                    )
                    .where(active_templates(tenant, subject, now))
                    .order_by(s.templates.c.id)
                ).mappings()
                result = {"templates": [dict(v) for v in rows]}
            elif action == Action.REVOKE_TEMPLATES:
                connection.execute(
                    s.resources.update()
                    .where(resource_filter(tenant, "subject", subject))
                    .values(generation=s.resources.c.generation + 1)
                )
                ids = list(
                    connection.execute(
                        sa.select(s.templates.c.id).where(
                            active_templates(tenant, subject, now)
                        )
                    ).scalars()
                )
                if ids:
                    connection.execute(
                        s.templates.update()
                        .where(s.templates.c.id.in_(ids))
                        .values(active=False, revoked_at=now)
                    )
                    connection.execute(
                        insert(s.deletion_ledger)
                        .values(
                            [
                                {
                                    "tenant": tenant,
                                    "kind": "template",
                                    "id": identity,
                                    "actor_id": actor_id,
                                    "at": now,
                                }
                                for identity in ids
                            ]
                        )
                        .on_conflict_do_nothing()
                    )
                for identity in ids:
                    schedule(connection, tenant, "template", identity, actor_id, now)
                # In-flight enrollments/verifications for this subject can no
                # longer commit; their nonces stay spent.
                connection.execute(
                    s.operations.update()
                    .where(
                        s.operations.c.tenant == tenant,
                        s.operations.c.subject_id == subject,
                        s.operations.c.state == "processing",
                    )
                    .values(
                        state="failed", status=409, result={"error": "SUBJECT_REVOKED"}
                    )
                )
                result = {"revoked": len(ids), "generation": target.generation + 1}
            elif action in {Action.GRANT_CONSENT, Action.WITHDRAW_CONSENT}:
                scope = payload["params"]["scope"]
                version = payload.get("body", {}).get("text_version")
                expected = VERSIONS[scope]
                if action == Action.GRANT_CONSENT and version != expected:
                    raise Denied("CONSENT_VERSION_REQUIRED", 400)
                values = {
                    "tenant": tenant,
                    "subject_id": subject,
                    "scope": scope,
                    "actor_id": actor_id,
                    "text_version": expected,
                    "granted": action == Action.GRANT_CONSENT,
                    "revision": 1,
                    "at": now,
                }
                statement = (
                    insert(s.consents)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["tenant", "subject_id", "scope"],
                        set_={
                            k: v
                            for k, v in values.items()
                            if k not in {"tenant", "subject_id", "scope", "revision"}
                        }
                        | {"revision": s.consents.c.revision + 1},
                    )
                    .returning(s.consents.c.revision)
                )
                revision = connection.execute(statement).scalar_one()
                connection.execute(
                    s.consent_events.insert().values(
                        **{**values, "revision": revision}, authority="self"
                    )
                )
                if action == Action.WITHDRAW_CONSENT:
                    withdraw(connection, tenant, subject, actor_id, scope, now)
                result = {
                    "scope": scope,
                    "granted": values["granted"],
                    "revision": revision,
                }
            elif action in {Action.LIST_CAPTURES, Action.READ_AGGREGATES}:
                # Apply authority + expiry in SQL before ORDER/LIMIT/OFFSET.
                filters = (
                    s.resources.c.tenant == tenant,
                    s.resources.c.kind == "capture",
                    s.resources.c.round_id == target.resource.round_id,
                    s.resources.c.active.is_(True),
                    s.resources.c.expires_at > now,
                )
                if action == Action.READ_AGGREGATES:
                    result = {
                        "count": connection.execute(
                            sa.select(sa.func.count())
                            .select_from(s.resources)
                            .where(*filters)
                        ).scalar_one()
                    }
                else:
                    query = payload.get("query", {})
                    rows = connection.execute(
                        sa.select(s.resources.c.id)
                        .where(*filters)
                        .order_by(s.resources.c.id)
                        .limit(int(query.get("limit", 20)))
                        .offset(int(query.get("offset", 0)))
                    ).scalars()
                    result = {"captures": [{"id": identity} for identity in rows]}
            elif action == Action.REQUEST_CAPTURE_DELETION:
                schedule(connection, tenant, "capture", target.id, actor_id, now)
                result = {
                    "deletion_requested": True,
                    "generation": target.generation + 1,
                }
            elif action == Action.READ_RECEIPT:
                # Owner and reviewer receipts deliberately contain storage status only.
                result = {"recording_status": evaluation["recording_status"]}
            elif action == Action.READ_CAPTURE:
                result = {"id": target.id, "metadata": evaluation["metadata"]}
            elif action == Action.READ_DIAGNOSTICS:
                result = {"id": target.id, "diagnostics": evaluation["diagnostics"]}
            elif action == Action.READ_FRAME:
                index = int(payload["params"]["index"])
                if index >= len(evaluation["frames"]):
                    raise Denied("NOT_FOUND", 404)
                result = FrameBody(
                    base64.b64decode(evaluation["frames"][index], validate=True)
                )
            else:
                raise Denied("NOT_FOUND", 404)
            self.audit(connection, fresh)
            return result

        return await self.repo.run(operation)

    async def scan(self, access, payload, reauthorize):
        action = access.action
        operation = (
            "enroll"
            if action in {Action.ENROLL, Action.ADD_TEMPLATE}
            else "verify"
            if action == Action.VERIFY
            else "liveness"
        )
        challenge = payload["scan"].get("challenge")
        if not isinstance(challenge, dict) or not isinstance(
            challenge.get("nonce"), str
        ):
            raise Denied("CHALLENGE_INVALID", 409)
        nonce_digest = hashlib.sha256(challenge["nonce"].encode()).hexdigest()
        key, digest = payload["idempotency_key"], payload["scan_digest"]
        # Admission precedes the nonce spend, as the engine's BUSY precedes its
        # nonce consumption; a refused request keeps its challenge.
        admit = getattr(self.analyzer, "admit", None)
        async with admit() if admit else contextlib.nullcontext():
            return await self.operate(
                access, payload, reauthorize, operation, nonce_digest, key, digest
            )

    async def operate(
        self, access, payload, reauthorize, operation, nonce_digest, key, digest
    ):
        challenge = payload["scan"]["challenge"]
        actor_id = access.auth.principal.actor_id
        tenant = access.auth.principal.tenant_id

        def claim(connection):
            target = self.repo.guard(connection, access, "subject")
            now, now_ms = int(self.repo.clock()), int(self.repo.clock() * 1000)
            revision = (
                self.consent(connection, access)
                if operation in {"enroll", "verify"}
                else None
            )
            existing = row(
                connection,
                sa.select(s.operations)
                .where(s.operations.c.actor_id == actor_id, s.operations.c.key == key)
                .with_for_update(),
            )
            if existing:
                if (
                    existing["request_digest"],
                    existing["subject_id"],
                    existing["operation"],
                ) != (digest, target.id, operation):
                    raise Denied("IDEMPOTENCY_CONFLICT", 409)
                if existing["expires_at"] <= now:
                    raise Denied("OPERATION_EXPIRED", 409)
                if existing["state"] == "processing":
                    if existing["created_at"] + PROCESSING_LEASE > now:
                        raise Denied("OPERATION_IN_PROGRESS", 409)
                    connection.execute(
                        s.operations.update()
                        .where(s.operations.c.id == existing["id"])
                        .values(
                            state="failed",
                            status=409,
                            result={"error": "OPERATION_INTERRUPTED"},
                        )
                    )
                    self.audit(connection, access, "interrupted")
                    raise Denied("OPERATION_INTERRUPTED", 409)
                self.audit(connection, access, "replayed")
                return "replay", existing
            templates = (
                [
                    dict(v)
                    for v in connection.execute(
                        sa.select(s.templates).where(
                            active_templates(tenant, target.id, int(self.repo.clock()))
                        )
                    ).mappings()
                ]
                if operation != "liveness"
                else []
            )
            if any(
                t["model_fingerprint"] != self.analyzer.format_id for t in templates
            ):
                raise Denied("MODEL_VERSION_MISMATCH", 409)
            if operation == "verify" and not templates:
                raise Denied("USER_NOT_FOUND", 404)  # frozen: before nonce spend
            if operation == "enroll":
                self.capacity(connection, tenant, target.id, len(templates), lock=False)
            operation_id = secrets.token_hex(16)
            connection.execute(
                s.operations.insert().values(
                    id=operation_id,
                    tenant=tenant,
                    actor_id=actor_id,
                    subject_id=target.id,
                    key=key,
                    operation=operation,
                    request_digest=digest,
                    state="processing",
                    created_at=now,
                    expires_at=now + OPERATION_TTL,
                )
            )
            claimed = spend_nonce(
                connection,
                nonce_digest,
                {
                    "actor_id": actor_id,
                    "session_id": access.auth.session.id,
                    "tenant": tenant,
                    "subject_id": target.id,
                    "generation": target.generation,
                    "operation": operation,
                },
                now_ms,
                operation_id,
            )
            if (
                not claimed
                or challenge.get("action") != claimed["parameters"]["action"]
                or not head_parameters_match(
                    challenge.get("params"), claimed["parameters"]["params"]
                )
            ):
                raise Denied("CHALLENGE_INVALID", 409)
            self.audit(connection, access, "nonce_claimed")
            return "claimed", {
                "operation_id": operation_id,
                "revision": revision,
                "templates": templates,
                "challenge": {
                    "subject": target.id,
                    **claimed["parameters"],
                    "age_ms": now_ms - claimed["issued_at"],
                },
            }

        state, data = await self.repo.run(claim)
        if state == "replay":
            return self.replayed(data)
        try:
            raw_templates = tuple(
                self.cipher.open(
                    t["tenant"],
                    "template",
                    t["id"],
                    t["ciphertext"],
                    expected_version=t["key_version"],
                )
                for t in data["templates"]
            )
            result = await self.analyzer.analyze(
                operation,
                payload["scan_bytes"],
                raw_templates,
                request_id=access.request_id,
                challenge=data["challenge"],
                precheck=payload.get("precheck"),
            )
            if type(result.accepted) is not bool or not isinstance(result.result, dict):
                raise Unavailable()
        except Denied as exc:
            await self.abandon(data["operation_id"], access, exc.code)
            raise
        if result.error:
            await self.finish(
                data["operation_id"],
                access,
                "completed",
                result.status,
                result.result,
                "rejected:" + result.error,
            )
            raise Denied(result.error, result.status, result.result.get("message"))
        try:
            fresh = await reauthorize()
        except Denied as exc:
            await self.abandon(data["operation_id"], access, exc.code)
            raise

        def commit(connection):
            target = self.repo.guard(connection, fresh, "subject")
            if (
                data["revision"] is not None
                and self.consent(connection, fresh) != data["revision"]
            ):
                raise Denied("CONSENT_CHANGED", 409)
            if any(
                t["expires_at"] is None or t["expires_at"] <= int(self.repo.clock())
                for t in data["templates"]
            ):
                raise Denied("TEMPLATE_EXPIRED", 409)
            if self.template_expires_at is not None and self.template_expires_at <= int(
                self.repo.clock()
            ):
                raise Denied("ROUND_CLOSED", 409)
            current = row(
                connection,
                sa.select(s.operations)
                .where(s.operations.c.id == data["operation_id"])
                .with_for_update(),
            )
            if not current or current["state"] != "processing":
                raise Denied(
                    (current["result"] or {}).get("error", "OPERATION_INTERRUPTED")
                    if current
                    else "OPERATION_INTERRUPTED",
                    409,
                )
            response = {
                "operation": operation,
                "operation_id": data["operation_id"],
                "accepted": result.accepted,
                "decision": result.result,
            }
            if operation == "enroll" and result.accepted:
                if not isinstance(result.template, bytes) or not result.template:
                    raise Unavailable()
                if self.template_expires_at is None:
                    raise Denied("ROUND_UNCONFIGURED", 503)
                active = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(s.templates)
                    .where(active_templates(tenant, target.id, int(self.repo.clock())))
                ).scalar_one()
                self.capacity(connection, tenant, target.id, active, lock=True)
                identity = secrets.token_hex(16)
                ciphertext = self.cipher.seal(
                    tenant, "template", identity, result.template
                )
                connection.execute(
                    s.templates.insert().values(
                        id=identity,
                        tenant=tenant,
                        subject_id=target.id,
                        owner_actor_id=fresh.auth.principal.actor_id,
                        created_at=int(self.repo.clock()),
                        active=True,
                        ciphertext=ciphertext,
                        format=self.analyzer.format_id,
                        model_fingerprint=self.analyzer.format_id,
                        key_version=self.cipher.decode(ciphertext)["key_version"],
                        expires_at=self.template_expires_at,
                    )
                )
                response["template_id"] = identity
            connection.execute(
                s.operations.update()
                .where(s.operations.c.id == data["operation_id"])
                .values(state="completed", status=200, result=response)
            )
            self.audit(connection, fresh)
            return response

        try:
            return await self.repo.run(commit)
        except Denied as exc:
            if not isinstance(exc, Unavailable):
                await self.abandon(data["operation_id"], fresh, exc.code)
            raise

    @staticmethod
    def replayed(existing):
        """Saved outcome after the claim transaction's current authority checks."""
        result = existing["result"] or {}
        if existing["state"] == "failed" or "error" in result:
            raise Denied(
                result.get("error", "OPERATION_INTERRUPTED"),
                existing["status"] or 409,
                result.get("message"),
            )
        return {**result, "replayed": True}

    async def finish(self, operation_id, access, state, status, result, outcome):
        def update(connection):
            connection.execute(
                s.operations.update()
                .where(
                    s.operations.c.id == operation_id,
                    s.operations.c.state == "processing",
                )
                .values(state=state, status=status, result=result)
            )
            self.audit(connection, access, outcome)

        await self.repo.run(update)

    async def abandon(self, operation_id, access, code):
        """Best effort; if the store is down the lease expiry ends the operation."""
        with contextlib.suppress(Unavailable):
            await self.finish(
                operation_id, access, "failed", 409, {"error": code}, "failed:" + code
            )
