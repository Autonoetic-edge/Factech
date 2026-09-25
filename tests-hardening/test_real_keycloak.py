"""Opt-in real Keycloak + PostgreSQL + two HTTPS ASGI processes, synthetic only."""

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import ssl
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx
import msgpack
import pytest
from facetech_auth.policy import Role
from facetech_auth.provision import Provisioner
from facetech_auth.sessions import TokenVault
from postgres_fixtures import local_secrets, prepare_databases

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".hardening-runtime/state"
ORIGIN = "https://127.0.0.1:18444"
ISSUER = "https://127.0.0.1:18443/realms/facetech-hardening"


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action, self.fields = None, {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs.get("action")
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")


@pytest.fixture(scope="module")
def staged(request):
    if not request.config.getoption("--hardening-keycloak"):
        pytest.skip(
            "Explicit --hardening-keycloak required; no default identity network access"
        )
    values = local_secrets()
    if "evaluation_key" not in values:
        values["evaluation_key"] = base64.b64encode(secrets.token_bytes(32)).decode()
        (STATE / "synthetic-secrets.json").write_text(
            json.dumps(values), encoding="utf-8"
        )
    for side, excluded in (("gateway", "data_key"), ("engine", "evaluation_key")):
        process_values = {
            k: v for k, v in values.items() if k not in {excluded, "users"}
        }
        (STATE / f"{side}-secrets.json").write_text(
            json.dumps(process_values), encoding="utf-8"
        )
    owner, evaluation = prepare_databases()
    provisioner = Provisioner(owner, administrator_id="synthetic-staging-provisioner")
    subjects = {}

    async def accounts():
        for name in ("alice", "bob", "carol", "reviewer", "admin", "operator"):
            identity = values["users"][name]["id"]
            actor = await owner.actor_by_identity(ISSUER, identity)
            if actor is None:
                roles = (
                    {Role.PARTICIPANT}
                    if name in {"alice", "bob", "carol"}
                    else {Role.REVIEWER}
                    if name == "reviewer"
                    else {Role.ADMINISTRATOR}
                    if name == "admin"
                    else set()
                )
                actor, subject = await provisioner.create_account(
                    ISSUER,
                    identity,
                    "real-idp-synthetic-b"
                    if name == "carol"
                    else "real-idp-synthetic-a",
                    roles,
                )
            else:
                import sqlalchemy as sa
                from facetech_auth import schema as s

                with owner.engine.connect() as connection:
                    subject = connection.execute(
                        sa.select(s.resources.c.id).where(
                            s.resources.c.kind == "subject",
                            s.resources.c.owner_actor_id == actor.id,
                        )
                    ).scalar_one_or_none()
            subjects[name] = subject

    asyncio.run(accounts())
    tls = ssl.create_default_context(cafile=str(STATE / "tls.crt"))
    with httpx.Client(verify=tls, trust_env=False, timeout=5) as client:
        assert (
            client.get(ISSUER + "/.well-known/openid-configuration").status_code == 200
        )
    processes = []
    try:
        for side, port in (("engine", 18445), ("gateway", 18444)):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
            with (STATE / f"{side}.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "tools/hardening_synthetic_service.py"),
                        side,
                        "--synthetic-only",
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            processes.append(process)
            (STATE / f"{side}-process.json").write_text(
                json.dumps({"pid": process.pid, "exe": sys.executable}),
                encoding="utf-8",
            )
            deadline = time.monotonic() + 20
            with httpx.Client(verify=tls, trust_env=False, timeout=2) as client:
                while True:
                    try:
                        if (
                            client.get(f"https://127.0.0.1:{port}/health").status_code
                            == 200
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    assert process.poll() is None and time.monotonic() < deadline, (
                        "Owned synthetic service did not become ready"
                    )
                    time.sleep(0.1)
        yield values, subjects, tls, owner
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=15)
        owner.dispose()
        evaluation.dispose()


def login(client, values, name):
    start = client.get(ORIGIN + "/auth/login")
    assert start.status_code == 302
    authorize = client.get(start.headers["location"], follow_redirects=True)
    assert authorize.status_code == 200
    form = LoginForm()
    form.feed(authorize.text)
    assert form.action, (
        "Keycloak login form required; password/direct grant is forbidden"
    )
    action = urljoin(str(authorize.url), form.action)
    assert action.startswith(ISSUER + "/")
    signed_in = client.post(
        action,
        data={
            **form.fields,
            "username": name,
            "password": values["users"][name]["password"],
            "credentialId": "",
        },
        follow_redirects=False,
    )
    assert signed_in.status_code == 302, (
        "Keycloak did not complete interactive authorization"
    )
    callback_url = signed_in.headers["location"]
    assert callback_url.startswith(ORIGIN + "/auth/callback?")
    callback = client.get(callback_url)
    assert callback.status_code == 303, callback.text
    session = client.get(ORIGIN + "/auth/session")
    assert session.status_code == 200
    return session.json(), callback_url


def test_real_https_code_pkce_login_owned_workflow_and_logout(staged):
    values, subjects, tls, owner = staged
    with httpx.Client(verify=tls, trust_env=False, timeout=15) as client:
        session, callback = login(client, values, "alice")
        headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        subject = subjects["alice"]
        assert client.get(callback).status_code == 401
        assert (
            client.post(
                ORIGIN + f"/v2/subjects/{subject}/consents/template_authentication",
                headers=headers,
                json={"text_version": "template-authentication-v1"},
            ).status_code
            == 200
        )
        challenge = client.get(
            ORIGIN + f"/v2/subjects/{subject}/challenge?operation=enroll",
            headers=headers,
        )
        assert challenge.status_code == 200
        # The staged engine runs the frozen pipeline: nonbiometric bytes reach
        # the unchanged FaceScan parser after authorization and are rejected
        # with its own code; the nonce is spent and no template exists.
        scan = msgpack.packb({"challenge": challenge.json(), "synthetic": True})
        scan_headers = {
            **headers,
            "Content-Type": "application/msgpack",
            "Idempotency-Key": secrets.token_hex(16),
        }
        response = client.post(
            ORIGIN + f"/v2/subjects/{subject}/enroll",
            headers=scan_headers,
            content=scan,
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "MALFORMED_SCAN"
        replay = client.post(
            ORIGIN + f"/v2/subjects/{subject}/enroll",
            headers=scan_headers,
            content=scan,
        )
        assert replay.status_code == 422 and replay.json()["error"] == "MALFORMED_SCAN"
        fresh_key = client.post(
            ORIGIN + f"/v2/subjects/{subject}/enroll",
            headers={**scan_headers, "Idempotency-Key": secrets.token_hex(16)},
            content=scan,
        )
        assert fresh_key.status_code == 409, fresh_key.text
        assert fresh_key.json()["error"] == "CHALLENGE_INVALID"
        listed = client.get(
            ORIGIN + f"/v2/subjects/{subject}/templates", headers=headers
        )
        assert listed.status_code == 200 and listed.json()["templates"] == []
        assert (
            client.delete(
                ORIGIN + f"/v2/subjects/{subjects['bob']}/templates", headers=headers
            ).status_code
            == 404
        )
        revoked = client.delete(
            ORIGIN + f"/v2/subjects/{subject}/templates", headers=headers
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] == 0
        cookie = client.cookies.get("__Host-facetech")
        old = asyncio.run(
            owner.session_by_cookie(hashlib.sha256(cookie.encode()).hexdigest())
        )
        vault = TokenVault(
            {"synthetic-v1": base64.b64decode(values["token_key"])}, "synthetic-v1"
        )
        tokens = vault.open(old)
        logout = client.post(ORIGIN + "/auth/logout", headers=headers)
        assert (
            logout.status_code == 200 and not logout.json()["provider_logout_pending"]
        )
        assert not asyncio.run(owner.session(old.id)).active
        introspection = client.post(
            ISSUER + "/protocol/openid-connect/token/introspect",
            auth=("facetech-bff", values["client_secret"]),
            data={"token": tokens["access_token"]},
        )
        assert (
            introspection.status_code == 200 and introspection.json()["active"] is False
        )
        client.cookies.set("__Host-facetech", cookie, domain="127.0.0.1", path="/")
        assert client.get(ORIGIN + "/auth/session").status_code == 401


@pytest.mark.parametrize("name", ["bob", "carol", "reviewer", "admin", "operator"])
def test_real_provider_roles_do_not_confer_participant_delegation(staged, name):
    values, subjects, tls, _ = staged
    with httpx.Client(verify=tls, trust_env=False, timeout=15) as client:
        session, _ = login(client, values, name)
        headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        assert (
            client.delete(
                ORIGIN + f"/v2/subjects/{subjects['alice']}/templates", headers=headers
            ).status_code
            == 404
        )
        assert client.post(ORIGIN + "/auth/logout", headers=headers).status_code == 200


def test_m3_real_provider_separate_services_record_rejection_then_withdraw(staged):
    import sqlalchemy as sa
    from facetech_auth import schema as s
    from facetech_auth.encryption import DataCipher
    from facetech_auth.evaluation_store import EvaluationStore, records
    from facetech_auth.lifecycle import Lifecycle
    from facetech_auth.postgres import PostgresRepository
    from postgres_fixtures import local_url
    from sqlalchemy.dialects.postgresql import insert

    values, subjects, tls, owner = staged
    with httpx.Client(verify=tls, trust_env=False, timeout=15) as client:
        session, _ = login(client, values, "alice")
        headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        subject, tenant = subjects["alice"], session["tenant_id"]
        with owner.engine.begin() as connection:
            connection.execute(
                insert(s.resources)
                .values(
                    tenant=tenant,
                    kind="round",
                    id="m3-synthetic-round",
                    round_id="m3-synthetic-round",
                    generation=0,
                    active=True,
                    expires_at=int(time.time()) + 3600,
                )
                .on_conflict_do_update(
                    index_elements=["tenant", "kind", "id"],
                    set_={"expires_at": int(time.time()) + 3600},
                )
            )
        for scope, version in (
            ("template_authentication", "template-authentication-v1"),
            ("evaluation_recording", "evaluation-recording-v1"),
        ):
            response = client.post(
                ORIGIN + f"/v2/subjects/{subject}/consents/{scope}",
                headers=headers,
                json={"text_version": version},
            )
            assert response.status_code == 200
        challenge_response = client.get(
            ORIGIN + f"/v2/subjects/{subject}/challenge?operation=enroll",
            headers=headers,
        )
        assert challenge_response.status_code == 200, challenge_response.text
        challenge = challenge_response.json()
        scan = msgpack.packb({"challenge": challenge, "synthetic": True})
        response = client.post(
            ORIGIN + f"/v2/subjects/{subject}/enroll",
            headers={
                **headers,
                "Content-Type": "application/msgpack",
                "Idempotency-Key": secrets.token_hex(16),
            },
            content=scan,
        )
        assert (
            response.status_code == 422 and response.json()["error"] == "MALFORMED_SCAN"
        )
        assert response.json()["recording"]["status"] == "stored"
        identity = response.json()["recording"]["receipt_id"]
        receipt = client.get(ORIGIN + f"/v2/receipts/{identity}")
        assert receipt.status_code == 200 and receipt.json() == {
            "recording_status": "stored"
        }
        assert client.get(ORIGIN + f"/v2/captures/{identity}").status_code == 404
        withdrawal = client.delete(
            ORIGIN + f"/v2/subjects/{subject}/consents/evaluation_recording",
            headers=headers,
        )
        assert withdrawal.status_code == 200
        assert client.get(ORIGIN + f"/v2/receipts/{identity}").status_code == 404
        evaluation_repo = PostgresRepository(local_url("facetech_eval"))
        try:
            evaluation = EvaluationStore(
                evaluation_repo,
                DataCipher(
                    base64.b64decode(values["evaluation_key"]), purpose="evaluation"
                ),
            )
            assert (
                asyncio.run(Lifecycle(owner, evaluation, tenant=tenant).drain())[
                    "completed"
                ]
                >= 1
            )
            with evaluation_repo.engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.select(records.c.id).where(
                            records.c.tenant == tenant, records.c.id == identity
                        )
                    ).first()
                    is None
                )
        finally:
            evaluation_repo.dispose()
        assert client.post(ORIGIN + "/auth/logout", headers=headers).status_code == 200
