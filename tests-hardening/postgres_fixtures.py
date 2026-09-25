"""Opt-in real PostgreSQL fixtures; no caller-supplied database URL is accepted."""

import base64
import json
import secrets
from dataclasses import replace
from pathlib import Path

import httpx
import sqlalchemy as sa
from auth_fixtures import FakeIdP, Harness, unlimited
from facetech_auth import schema as s
from facetech_auth.evaluation_store import EvaluationStore
from facetech_auth.evaluation_store import metadata as evaluation_metadata
from facetech_auth.evaluation_store import records as evaluation_records
from facetech_auth.http import create_engine, create_gateway
from facetech_auth.oidc import Provider
from facetech_auth.operations import Analysis, DataCipher, PostgresOperations
from facetech_auth.policy import Role, Scope
from facetech_auth.postgres import PostgresRepository, migrate
from facetech_auth.provision import Provisioner
from facetech_auth.sessions import Sessions

ROOT = Path(__file__).resolve().parents[1]


def local_secrets():
    values = json.loads(
        (ROOT / ".hardening-runtime/state/synthetic-secrets.json").read_text("utf-8")
    )
    assert values["marker"] == "facetech-m1-synthetic-only"
    return values


DATABASES = {
    "facetech_m1",
    "facetech_eval",
    "facetech_idp",
    "facetech_m2_migrate",
    "facetech_m2_restore",
}


def local_url(database="facetech_m1"):
    assert database in DATABASES
    return sa.URL.create(
        "postgresql+psycopg",
        username="facetech_test",
        password=local_secrets()["pg_password"],
        host="127.0.0.1",
        port=15432,
        database=database,
    )


def prepare_databases():
    owner = PostgresRepository(local_url())
    migrate(owner.engine)
    evaluation = PostgresRepository(local_url("facetech_eval"))
    with evaluation.engine.begin() as connection:
        connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS face_evaluation"))
        evaluation_metadata.create_all(connection)
    return owner, evaluation


class SyntheticAnalyzer:
    format_id = "synthetic-nonbiometric-v1"

    def __init__(self):
        self.calls = 0
        self.hook = None

    async def analyze(
        self, operation, scan, templates, *, request_id, challenge, precheck=None
    ):
        self.calls += 1
        if self.hook:
            await self.hook()
        accepted = operation != "verify" or bool(templates)
        return Analysis(
            accepted,
            {"synthetic_test_only": True},
            b"nonbiometric template fixture" if operation == "enroll" else None,
        )


class PersistentHarness(Harness):
    async def initialize(self, owner, evaluation, analyzer=None):
        self.namespace = secrets.token_hex(8)
        self.config = replace(
            self.config, issuer="https://identity.test/realms/" + self.namespace
        )
        self.idp = FakeIdP(self.config, lambda: self.now)
        self.transport = httpx.MockTransport(self.idp.handler)
        self.owner = owner
        self.repo = PostgresRepository(local_url(), clock=lambda: self.now)
        self.other_repo = PostgresRepository(local_url(), clock=lambda: self.now)
        self.provisioner = Provisioner(
            self.repo, administrator_id="synthetic-provisioner"
        )
        self.actors, self.subjects = {}, {}
        for name, roles in (
            ("alice", {Role.PARTICIPANT}),
            ("bob", {Role.PARTICIPANT}),
            ("carol", {Role.PARTICIPANT}),
            ("reviewer", {Role.REVIEWER}),
            ("admin", {Role.ADMINISTRATOR}),
            ("operator", set()),
        ):
            tenant = self.namespace + ("-b" if name == "carol" else "-a")
            actor, subject = await self.provisioner.create_account(
                self.config.issuer, name, tenant, roles
            )
            self.actors[name], self.subjects[name] = actor, subject
        self.engine_sessions = Sessions(
            self.other_repo,
            Provider(self.config, transport=self.transport, clock=lambda: self.now),
            self.vault,
            clock=lambda: self.now,
        )
        self.gateway_sessions = Sessions(
            self.repo,
            Provider(self.config, transport=self.transport, clock=lambda: self.now),
            self.vault,
            clock=lambda: self.now,
        )
        self.analyzer = analyzer if analyzer is not None else SyntheticAnalyzer()
        self.cipher, self.evaluation_cipher = (
            DataCipher(secrets.token_bytes(32)),
            DataCipher(secrets.token_bytes(32), purpose="evaluation"),
        )
        self.evaluation_repo = PostgresRepository(
            local_url("facetech_eval"), clock=lambda: self.now
        )
        self.evaluation = EvaluationStore(self.evaluation_repo, self.evaluation_cipher)
        self.engine_ops = PostgresOperations(
            self.other_repo,
            self.cipher,
            analyzer=self.analyzer,
            template_expires_at=int(self.now) + 3600,
        )
        self.gateway_ops = PostgresOperations(
            self.repo, self.cipher, evaluation=self.evaluation
        )
        self.limits = unlimited()
        self.engine = create_engine(
            self.engine_sessions, operations=self.engine_ops, limits=self.limits
        )
        self.gateway = create_gateway(
            self.gateway_sessions,
            operations=self.gateway_ops,
            engine_transport=httpx.ASGITransport(app=self.engine),
            limits=self.limits,
        )
        self.captures, self.receipts = {}, {}
        for name in ("alice", "bob", "carol"):
            actor = self.actors[name]
            capture, receipt = "capture-" + name, "receipt-" + name
            self.captures[name], self.receipts[name] = capture, receipt
            with owner.engine.begin() as connection:
                for kind, identity, owner_id in (
                    ("round", "round-1", None),
                    ("capture", capture, actor.id),
                    ("receipt", receipt, actor.id),
                ):
                    statement = s.resources.insert().values(
                        tenant=actor.tenant,
                        kind=kind,
                        id=identity,
                        owner_actor_id=owner_id,
                        round_id="round-1",
                        generation=0,
                        active=True,
                        expires_at=self.now + 3600,
                        data_ref=capture if kind != "round" else None,
                    )
                    if (
                        kind == "round"
                        and connection.execute(
                            sa.select(s.resources.c.id).where(
                                s.resources.c.tenant == actor.tenant,
                                s.resources.c.kind == "round",
                            )
                        ).first()
                    ):
                        continue
                    connection.execute(statement)
            payload = {
                "recording_status": "saved",
                "metadata": {"fixture": name},
                "diagnostics": {"synthetic": True},
                "frames": [
                    base64.b64encode(b"nonbiometric synthetic frame fixture").decode()
                ],
            }
            with evaluation.engine.begin() as connection:
                connection.execute(
                    evaluation_records.insert().values(
                        tenant=actor.tenant,
                        id=capture,
                        expires_at=self.now + 3600,
                        ciphertext=self.evaluation_cipher.seal(
                            actor.tenant,
                            "evaluation",
                            capture,
                            json.dumps(payload).encode(),
                        ),
                    )
                )
        self.grant_id = await self.provisioner.review_grant(
            self.actors["reviewer"].id,
            self.actors["reviewer"].tenant,
            "round-1",
            {Scope.METADATA},
            self.now + 3600,
        )
        return self

    async def consent(self, browser, name="alice"):
        return await self.request(
            "POST",
            f"/v2/subjects/{self.subjects[name]}/consents/template_authentication",
            browser,
            json={"text_version": "template-authentication-v1"},
        )

    async def challenge(self, browser, name="alice", operation="enroll"):
        response = await self.request(
            "GET",
            f"/v2/subjects/{self.subjects[name]}/challenge?operation={operation}",
            browser,
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def scan(
        self,
        browser,
        challenge,
        *,
        name="alice",
        operation="enroll",
        engine=False,
        transport="json",
        key=None,
        raw=None,
    ):
        import msgpack

        if raw is None:
            raw = msgpack.packb({"challenge": challenge, "synthetic": True})
        headers = {"Idempotency-Key": key or secrets.token_hex(16)}
        if transport == "msgpack":
            kwargs = {
                "content": raw,
                "headers": {**headers, "Content-Type": "application/msgpack"},
            }
        else:
            kwargs = {
                "json": {
                    "user_id": self.subjects[name],
                    "facescan": base64.b64encode(raw).decode(),
                },
                "headers": headers,
            }
        return await self.request(
            "POST",
            f"/v2/subjects/{self.subjects[name]}/{operation}",
            browser,
            engine=engine,
            **kwargs,
        )

    def close(self):
        for repo in (self.repo, self.other_repo, self.evaluation_repo):
            repo.dispose()
