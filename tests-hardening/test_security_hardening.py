"""M4 S04-S05: fail-closed startup, credential rotation, browser and log hardening.

Isolated and synthetic. Every credential here is a test literal; no live value,
no real capture and no embedding appears in this file or in what it asserts on.
"""

import base64
import secrets
from dataclasses import replace

import pytest
from auth_fixtures import Harness, run, scan_request, unlimited
from facetech_auth import serve
from facetech_auth.contracts import Denied
from facetech_auth.http import create_engine
from facetech_auth.oidc import Provider
from facetech_auth.sessions import COOKIE, LOGIN_COOKIE, Sessions, TokenVault

SCAN_PATH = "/v2/subjects/subject-alice/enroll"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@pytest.fixture
def h():
    return Harness()


# ------------------------------------------------------- S04 fail-closed start

REQUIRED = (
    "AMFATEC_ORIGIN",
    "AMFATEC_OIDC_ISSUER",
    "AMFATEC_OIDC_CLIENT_ID",
    "AMFATEC_OIDC_CLIENT_SECRET",
    "AMFATEC_OIDC_AUDIENCE",
    "AMFATEC_ENGINE_ORIGIN",
    "AMFATEC_ENGINE_KEY",
    "AMFATEC_DB_USER",
    "AMFATEC_DB_PASSWORD",
    "AMFATEC_DB_HOST",
    "AMFATEC_DB_PORT",
    "AMFATEC_DB_NAME",
    "AMFATEC_CA_FILE",
    "AMFATEC_TOKEN_KEY_ID",
    "AMFATEC_TOKEN_KEY",
)


def synthetic_ca(path):
    """A throwaway self-signed certificate; only its parseability matters here."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic test CA")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return path


def startup_env(monkeypatch, tmp_path):
    """A complete synthetic environment up to the point each test removes from."""
    ca = synthetic_ca(tmp_path / "ca.pem")
    values = {
        "AMFATEC_ORIGIN": "https://gateway.test",
        "AMFATEC_OIDC_ISSUER": "https://identity.test/realms/test",
        "AMFATEC_OIDC_CLIENT_ID": "bff",
        "AMFATEC_OIDC_CLIENT_SECRET": "synthetic-client-secret",
        "AMFATEC_OIDC_AUDIENCE": "facetech-engine",
        "AMFATEC_ENGINE_ORIGIN": "https://engine.test",
        "AMFATEC_ENGINE_KEY": "synthetic-service-key-0000000000000000",
        "AMFATEC_DB_USER": "amfatec",
        "AMFATEC_DB_PASSWORD": "synthetic-db-password",
        "AMFATEC_DB_HOST": "127.0.0.1",
        "AMFATEC_DB_PORT": "15432",
        "AMFATEC_DB_NAME": "amfatec",
        "AMFATEC_CA_FILE": str(ca),
        "AMFATEC_TOKEN_KEY_ID": "test-v1",
        "AMFATEC_TOKEN_KEY": base64.b64encode(b"k" * 32).decode(),
    }
    for name in list(values) + [
        "AMFATEC_ENGINE_KEY_PREVIOUS",
        "AMFATEC_TOKEN_KEY_RETIRING_ID",
        "AMFATEC_TOKEN_KEY_RETIRING",
        "AMFATEC_SELF_REGISTRATION",
        "AMFATEC_TRUSTED_PROXY",
    ]:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.mark.parametrize("missing", REQUIRED)
def test_s04_a_missing_secret_or_deployment_fact_stops_startup(
    monkeypatch, tmp_path, missing
):
    values = startup_env(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as stop:
        serve.application("engine")
    assert missing in str(stop.value)
    for value in values.values():  # the message names the variable, never a value
        assert value not in str(stop.value) or value == values[missing] == ""


def test_s04_a_half_rotation_pair_stops_startup(monkeypatch, tmp_path):
    startup_env(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "AMFATEC_TOKEN_KEY_RETIRING", base64.b64encode(b"r" * 32).decode()
    )
    with pytest.raises(SystemExit) as stop:
        serve.application("engine")
    assert "AMFATEC_TOKEN_KEY_RETIRING_ID" in str(stop.value)


def test_s04_a_short_or_unpadded_key_stops_startup(monkeypatch, tmp_path):
    startup_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AMFATEC_TOKEN_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(SystemExit) as stop:
        serve.application("engine")
    assert "AMFATEC_TOKEN_KEY" in str(stop.value) and "32 bytes" in str(stop.value)


def test_s04_optional_pair_is_empty_or_complete(monkeypatch):
    monkeypatch.delenv("PAIR_ID", raising=False)
    monkeypatch.delenv("PAIR_KEY", raising=False)
    assert serve.optional_pair("PAIR_ID", "PAIR_KEY") == {}
    monkeypatch.setenv("PAIR_ID", "old")
    monkeypatch.setenv("PAIR_KEY", base64.b64encode(b"o" * 32).decode())
    assert serve.optional_pair("PAIR_ID", "PAIR_KEY") == {"old": b"o" * 32}


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "http://identity.test/realms/test"),
        ("issuer", "https://identity.test/realms/test/"),
        ("origin", "https://gateway.test/base"),
        ("engine_origin", "https://user:pw@engine.test"),
        ("engine_key", "too-short"),
        ("client_secret", ""),
    ],
)
def test_s04_configuration_refuses_a_weaker_deployment(h, field, value):
    with pytest.raises(ValueError):
        replace(h.config, **{field: value})


@pytest.mark.parametrize(
    "previous", ["short", "synthetic-service-key-0000000000000000", " " * 40]
)
def test_s04_a_retiring_service_key_must_be_distinct_and_strong(h, previous):
    with pytest.raises(ValueError):
        replace(h.config, engine_key_previous=previous)


def test_s04_service_key_rotation_overlaps_then_retires(h):
    """During overlap the engine accepts both keys; after retirement, only the new."""
    retiring = h.config.engine_key
    rotated = replace(
        h.config,
        engine_key="synthetic-service-key-1111111111111111",
        engine_key_previous=retiring,
    )
    overlap = create_engine(
        Sessions(
            h.repo,
            Provider(rotated, transport=h.transport, clock=lambda: h.now),
            h.vault,
            clock=lambda: h.now,
        ),
        operations=h.engine_ops,
        limits=unlimited(),
    )
    browser = run(h.login())
    assert run(_engine_call(h, overlap, browser, retiring)).status_code == 200
    assert run(_engine_call(h, overlap, browser, rotated.engine_key)).status_code == 200

    retired = create_engine(
        Sessions(
            h.repo,
            Provider(
                replace(rotated, engine_key_previous=""),
                transport=h.transport,
                clock=lambda: h.now,
            ),
            h.vault,
            clock=lambda: h.now,
        ),
        operations=h.engine_ops,
        limits=unlimited(),
    )
    assert run(_engine_call(h, retired, browser, retiring)).status_code == 401
    assert run(_engine_call(h, retired, browser, rotated.engine_key)).status_code == 200


async def _engine_call(h, app, browser, key):
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=h.config.engine_origin
    ) as client:
        return await client.get(
            "/v2/info",
            headers={
                "Authorization": "Bearer " + browser.token,
                "X-Engine-Key": key,
                "X-Facetech-Session": browser.session_id,
            },
        )


def test_s04_token_key_rotation_reads_old_sessions_and_writes_new(h):
    old, new = secrets.token_bytes(32), secrets.token_bytes(32)
    vault = TokenVault({"v1": old}, "v1")
    sealed = vault.seal("session-1", {"access_token": "synthetic-token"})
    overlap = TokenVault({"v1": old, "v2": new}, "v2")
    session = replace(
        next(iter(h.repo.sessions.values()), None) or _blank_session(),
        id="session-1",
        token_ciphertext=sealed,
    )
    assert overlap.open(session)["access_token"] == "synthetic-token"
    fresh = overlap.seal("session-1", {"access_token": "synthetic-token"})
    assert fresh.startswith(b"v2:"), "new writes must use the active key"
    retired = TokenVault({"v2": new}, "v2")
    with pytest.raises(Denied):
        retired.open(session)
    assert retired.open(replace(session, token_ciphertext=fresh))


def _blank_session():
    from facetech_auth.contracts import Session

    return Session("session-1", "", "alice", 0, "", "", "", 0, 0, 0, 0, "", b"", "")


def test_s04_a_vault_without_its_active_key_refuses_to_start():
    with pytest.raises(ValueError):
        TokenVault({"v1": secrets.token_bytes(32)}, "v2")
    with pytest.raises(ValueError):
        TokenVault({"v1": b"short"}, "v1")


# ------------------------------------------------ S05 browser and log surface


@pytest.mark.parametrize("engine", [False, True])
def test_s05_every_response_carries_the_security_headers(h, engine):
    browser = run(h.login())
    for path, expect in (("/health", 200), ("/v2/info", 200), ("/nope", 404)):
        response = run(h.request("GET", path, browser, engine=engine))
        assert response.status_code == expect
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value, (path, name)
        assert response.headers["X-Request-Id"]


def test_s05_no_response_ever_carries_a_cors_header(h):
    browser = run(h.login())
    calls = [
        ("GET", "/v2/info", {}),
        ("OPTIONS", "/v2/info", {}),
        ("GET", "/health", {"Origin": "https://evil.test"}),
        ("POST", SCAN_PATH, {"Origin": "https://evil.test"}),
    ]
    for method, path, headers in calls:
        response = run(h.request(method, path, browser, headers=headers))
        leaked = [
            n for n in response.headers if n.lower().startswith("access-control-")
        ]
        assert not leaked, (path, leaked)


def scan_with(extra):
    kwargs = scan_request()
    kwargs["headers"] = {**kwargs["headers"], **extra}
    return kwargs


def test_s05_a_foreign_origin_cannot_mutate(h):
    browser = run(h.login())
    response = run(
        h.request(
            "POST", SCAN_PATH, browser, **scan_with({"Origin": "https://evil.test"})
        )
    )
    assert response.status_code == 403
    assert response.json()["error"] == "CSRF_REQUIRED"
    assert not h.engine_ops.calls


def test_s05_a_mutation_without_the_csrf_token_is_refused(h):
    browser = run(h.login())
    response = run(
        h.request(
            "POST", SCAN_PATH, browser, **scan_with({"X-CSRF-Token": "not-the-token"})
        )
    )
    assert response.status_code == 403
    assert not h.engine_ops.calls


def test_s05_session_cookies_are_host_prefixed_and_locked_down(h):
    import httpx

    async def flow():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.gateway), base_url=h.config.origin
        ) as client:
            start = await client.get("/auth/login")
            code, params = h.idp.authorize(start.headers["location"], "alice")
            done = await client.get(
                "/auth/callback", params={"state": params["state"], "code": code}
            )
            return start, done

    start, done = run(flow())
    for response, name in ((start, LOGIN_COOKIE), (done, COOKIE)):
        header = next(
            v
            for v in response.headers.get_list("set-cookie")
            if v.startswith(name + "=")
        )
        assert name.startswith("__Host-")
        assert "Secure" in header and "HttpOnly" in header
        assert "samesite=lax" in header.lower()
        assert "Path=/" in header and "Domain=" not in header


def test_s05_logout_clears_the_cookie_and_the_old_cookie_cannot_replay(h):
    browser = run(h.login())
    out = run(
        h.request(
            "POST",
            "/auth/logout",
            browser,
            headers={"Content-Type": "application/json"},
        )
    )
    assert out.status_code == 200 and out.json()["logged_out"] is True
    cleared = next(
        v for v in out.headers.get_list("set-cookie") if v.startswith(COOKIE + "=")
    )
    assert "Max-Age=0" in cleared or "expires=Thu, 01 Jan 1970" in cleared.lower()
    assert run(h.request("GET", "/v2/info", browser)).status_code == 401


def test_s05_another_subject_is_indistinguishable_from_one_that_does_not_exist(h):
    browser = run(h.login())
    other = run(h.request("GET", "/v2/subjects/subject-bob/templates", browser))
    absent = run(h.request("GET", "/v2/subjects/subject-nobody/templates", browser))
    assert other.status_code == absent.status_code == 404
    # The v1-style code; the architecture's RESOURCE_NOT_FOUND rename is M5's
    # decision envelope work, not an M4 control.
    assert other.json()["error"] == absent.json()["error"] == "NOT_FOUND"
    assert set(other.json()) == set(absent.json()) == {"error", "request_id"}


def test_s05_no_error_body_reveals_a_credential_or_a_payload(h):
    browser = run(h.login())
    secretish = (
        browser.cookie,
        browser.csrf,
        browser.token,
        h.config.engine_key,
        h.config.client_secret,
    )
    responses = [
        run(h.request("GET", "/v2/subjects/subject-bob/templates", browser)),
        run(h.request("GET", "/nope", browser)),
        run(h.request("POST", SCAN_PATH, browser, content=b"\xff\xfe\xfd",
                      headers={"Content-Type": "application/msgpack",
                               "Idempotency-Key": "0123456789abcdef0123456789abcdef"})),
        run(h.request("POST", SCAN_PATH, browser, **scan_with({"Origin": "https://evil.test"}))),
        run(h.request("GET", "/v2/info", browser, headers={"X-Actor-Id": "bob"})),
        run(h.request("GET", "/v2/info", None)),
    ]  # fmt: skip
    for response in responses:
        text = response.text
        assert set(response.json()) <= {"error", "request_id", "message"}
        for value in secretish:
            assert value not in text
        for marker in ("facescan", "embedding", "template", "Traceback", "psycopg"):
            assert marker not in text


def test_s05_audit_records_identifiers_and_outcomes_only(h):
    browser = run(h.login())
    run(h.request("POST", SCAN_PATH, browser, **scan_request()))
    run(h.request("GET", "/v2/subjects/subject-bob/templates", browser))
    assert h.repo.events, "no audit event was recorded"
    for event in h.repo.events:
        text = repr(event)
        for value in (browser.cookie, browser.csrf, browser.token, h.config.engine_key):
            assert value not in text
        for marker in ("facescan", "nonce", "embedding"):
            assert marker not in text.lower()


def test_s05_client_supplied_identity_headers_are_refused_outright(h):
    browser = run(h.login())
    for header in ("X-Actor-Id", "X-Tenant-Id", "X-Role", "X-Roles", "X-Auth-Context"):
        response = run(h.request("GET", "/v2/info", browser, headers={header: "bob"}))
        assert response.status_code == 400
        assert response.json()["error"] == "UNTRUSTED_CONTEXT"


def test_s05_a_browser_cannot_present_engine_credentials_to_the_gateway(h):
    browser = run(h.login())
    for header in ("Authorization", "X-Engine-Key", "X-Facetech-Session"):
        response = run(
            h.request("GET", "/v2/info", browser, headers={header: "anything"})
        )
        assert response.status_code == 400
        assert response.json()["error"] == "UNTRUSTED_CONTEXT"


# ------------------------------------------------------- S07 supply chain


def test_s07_every_runtime_requirement_is_exactly_pinned():
    from tools_inventory import report

    declared = report()["declared"]
    for path, entry in declared.items():
        assert not entry["unpinned"], (path, entry["unpinned"])
        assert entry["pinned"], path


def test_s07_the_application_base_image_is_pinned_by_digest():
    from tools_inventory import report

    for image in report()["images"]["deploy/amfatec/Dockerfile"]:
        assert image["digest_pinned"], image["reference"]


def test_s07_no_writable_host_mount_is_shared_with_a_service():
    from tools_inventory import report

    for mount in report()["mounts"]["deploy/amfatec/compose.yml"]:
        if not mount["read_only"]:
            # Only the named database volume may be writable; no host bind, and
            # never a docker socket.
            assert mount["mount"].startswith("db:"), mount
        assert "docker.sock" not in mount["mount"]


def test_s07_every_service_refuses_privilege_escalation():
    import re
    from pathlib import Path

    compose = Path(__file__).resolve().parents[1] / "deploy/amfatec/compose.yml"
    text = compose.read_text("utf-8")
    services = re.findall(r"^  (\w+):$", text.split("services:", 1)[1], re.M)
    assert set(services) == {"db", "auth", "engine", "gateway"}, services
    # Two occurrences come from the shared x-app anchor plus db and auth.
    assert text.count("no-new-privileges:true") == 3


def test_s07_the_inventory_never_claims_a_scan_it_did_not_run():
    from tools_inventory import report

    scan = report()["scan"]
    assert scan["performed"] is False and scan["reason"]
