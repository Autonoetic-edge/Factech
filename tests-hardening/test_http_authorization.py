"""A02/A03/A05/A07/A08: both isolated ASGI boundaries, no service startup."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from auth_fixtures import Harness, run, scan_request
from facetech_auth.http import ROUTES, create_engine
from facetech_auth.policy import Role, Scope


@pytest.fixture
def h():
    return Harness()


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("transport", ["json", "msgpack"])
@pytest.mark.parametrize("operation", ["enroll", "templates", "verify", "liveness"])
def test_self_scan_authorized_by_both_boundaries(h, engine, transport, operation):
    browser = run(h.login())
    before = len(h.idp.calls)
    response = run(
        h.request(
            "POST",
            f"/v2/subjects/subject-alice/{operation}",
            browser,
            engine=engine,
            **scan_request(transport),
        )
    )
    assert response.status_code == 200, response.text
    assert response.json()["synthetic_authorized"] is True
    assert h.engine_ops.commits[-1].target.resource.owner_actor_id == "alice"
    introspections = sum(p.endswith("/introspect") for p in h.idp.calls[before:])
    assert introspections >= (3 if engine else 4)


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize(
    "actor", ["bob", "carol", "reviewer", "admin", "operator", "multi"]
)
@pytest.mark.parametrize(
    "operation",
    [
        "enroll",
        "templates",
        "verify",
        "liveness",
        "revoke",
        "list",
        "challenge",
        "consent",
    ],
)
def test_no_delegation_or_cross_tenant_participant_authority(
    h, engine, actor, operation
):
    browser = run(h.login(actor))
    method = "POST"
    path = "/v2/subjects/subject-alice/" + operation
    kwargs = scan_request()
    if operation in {"revoke", "list"}:
        method = "DELETE" if operation == "revoke" else "GET"
        path, kwargs = "/v2/subjects/subject-alice/templates", {}
    elif operation == "challenge":
        method, kwargs = "GET", {"params": {"operation": "enroll"}}
    elif operation == "consent":
        path, kwargs = (
            "/v2/subjects/subject-alice/consents/template_authentication",
            {"json": {"text_version": "synthetic-v1"}},
        )
    response = run(h.request(method, path, browser, engine=engine, **kwargs))
    assert response.status_code == 404
    assert not h.engine_ops.calls


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("role", [Role.REVIEWER, Role.ADMINISTRATOR, "operator"])
def test_nonparticipant_role_does_not_authorize_even_owned_subject(h, engine, role):
    browser = run(h.login())
    h.repo.actors["alice"] = replace(h.repo.actors["alice"], roles=frozenset({role}))
    assert (
        run(
            h.request(
                "DELETE", "/v2/subjects/subject-alice/templates", browser, engine=engine
            )
        ).status_code
        == 404
    )


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("transport", ["json", "msgpack"])
def test_missing_authentication_never_reads_scan_or_invokes_operations(
    h, engine, transport
):
    response = run(
        h.request(
            "POST",
            "/v2/subjects/subject-alice/enroll",
            engine=engine,
            **scan_request(transport),
        )
    )
    assert response.status_code == 401
    assert not h.engine_ops.calls


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize(
    "attack",
    [
        "body",
        "header",
        "scan",
        "tenant",
        "roles",
        "forwarded",
        "context",
        "session",
        "key-only",
    ],
)
def test_forged_identity_context_cannot_authorize(h, engine, attack):
    browser = run(h.login())
    kwargs = scan_request()
    if attack == "body":
        kwargs["json"]["user_id"] = "subject-bob"
    elif attack == "header":
        kwargs["headers"] = {"X-User-Id": "subject-bob"}
    elif attack == "scan":
        import base64

        import msgpack

        kwargs["json"]["facescan"] = base64.b64encode(
            msgpack.packb({"subject_id": "subject-bob"})
        ).decode()
    elif attack == "tenant":
        kwargs["headers"] = {"X-Tenant-Id": "team-b"}
    elif attack == "roles":
        kwargs["headers"] = {"X-Roles": "administrator"}
    elif attack == "forwarded":
        kwargs["headers"] = {"X-Forwarded-Host": "gateway.test"}
    elif attack == "context":
        kwargs["headers"] = {"X-Auth-Context": "alice"}
    elif attack == "session":
        other = run(h.login("bob"))
        kwargs["headers"] = {"X-Facetech-Session": other.session_id}
    else:
        browser = None
        kwargs["headers"] = {"X-Engine-Key": h.config.engine_key}
    response = run(
        h.request(
            "POST",
            "/v2/subjects/subject-alice/enroll",
            browser,
            engine=engine,
            **kwargs,
        )
    )
    assert response.status_code in {400, 401, 404}
    assert not h.engine_ops.calls


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
        ("POST", "/v2/unregistered"),
        ("HEAD", "/v2/info"),
        ("OPTIONS", "/v2/info"),
        ("PUT", "/v2/subjects/subject-alice/templates"),
        ("GET", "/v2/info/"),
        ("GET", "/v1/info"),
        ("GET", "/sdk/v2/info"),
        ("GET", "/v2/%69nfo"),
        ("GET", "/v2//info"),
        ("TRACE", "/health"),
    ],
)
def test_exact_method_path_registration(h, engine, method, path):
    browser = run(h.login())
    assert run(h.request(method, path, browser, engine=engine)).status_code == 404
    assert not h.engine_ops.calls


@pytest.mark.parametrize(
    "header,value",
    [
        ("Origin", "https://evil.test"),
        ("Origin", "null"),
        ("Origin", ""),
        ("X-CSRF-Token", "wrong"),
        ("X-CSRF-Token", ""),
    ],
)
def test_cookie_mutations_require_exact_origin_and_csrf(h, header, value):
    browser = run(h.login())
    for method, path in (
        ("DELETE", "/v2/subjects/subject-alice/templates"),
        ("POST", "/auth/logout"),
        ("GET", "/v2/subjects/subject-alice/challenge?operation=enroll"),
    ):
        assert (
            run(h.request(method, path, browser, headers={header: value})).status_code
            == 403
        )
    assert not h.engine_ops.calls


@pytest.mark.parametrize("engine", [False, True])
def test_recent_login_exact_boundary(h, engine):
    browser = run(h.login())
    h.now += 300
    assert (
        run(
            h.request(
                "DELETE", "/v2/subjects/subject-alice/templates", browser, engine=engine
            )
        ).status_code
        == 403
    )
    assert (
        run(
            h.request(
                "POST",
                "/v2/subjects/subject-alice/verify",
                browser,
                engine=engine,
                **scan_request(),
            )
        ).status_code
        == 200
    )


def test_receipts_reviews_scopes_and_uniform_foreign_response(h):
    alice, bob, reviewer, admin = (
        run(h.login(a)) for a in ("alice", "bob", "reviewer", "admin")
    )
    assert run(h.request("GET", "/v2/receipts/receipt-alice", alice)).status_code == 200
    denied = run(h.request("GET", "/v2/receipts/receipt-alice", bob))
    absent = run(h.request("GET", "/v2/receipts/not-present", bob))
    assert denied.status_code == absent.status_code == 404
    assert denied.json()["error"] == absent.json()["error"]
    for path in (
        "/v2/captures/capture-alice",
        "/v2/captures/capture-alice/diagnostics",
        "/v2/rounds/round-1/captures?limit=2&offset=3",
    ):
        assert run(h.request("GET", path, reviewer)).status_code == 200
        assert run(h.request("GET", path, admin)).status_code == 404
    path = "/v2/captures/capture-alice/frames/0"
    assert run(h.request("GET", path, reviewer)).status_code == 404
    h.repo.review_grants[0] = replace(
        h.repo.review_grants[0], scopes=frozenset({Scope.METADATA, Scope.FRAMES})
    )
    assert run(h.request("GET", path, reviewer)).status_code == 200
    h.repo.review_grants[0] = replace(h.repo.review_grants[0], revoked=True)
    assert run(h.request("GET", path, reviewer)).status_code == 404
    assert (
        run(h.request("GET", "/v2/rounds/round-1/capture-status", admin)).status_code
        == 200
    )
    assert (
        run(h.request("DELETE", "/v2/captures/capture-alice", reviewer)).status_code
        == 404
    )
    assert (
        run(h.request("DELETE", "/v2/captures/capture-alice", alice)).status_code == 200
    )


@pytest.mark.parametrize(
    "change", ["session", "actor", "generation", "ownership", "role", "provider"]
)
def test_changes_during_work_prevent_synthetic_commit(h, change):
    browser = run(h.login())

    def alter():
        key = ("team-a", "subject", "subject-alice")
        target = h.repo.targets[key]
        if change == "session":
            h.repo.sessions[browser.session_id] = replace(
                h.repo.sessions[browser.session_id], active=False
            )
        elif change == "actor":
            h.repo.actors["alice"] = replace(h.repo.actors["alice"], active=False)
        elif change == "generation":
            h.repo.targets[key] = replace(target, generation=1)
        elif change == "ownership":
            h.repo.targets[key] = replace(
                target, resource=replace(target.resource, owner_actor_id="bob")
            )
        elif change == "role":
            h.repo.actors["alice"] = replace(h.repo.actors["alice"], roles=frozenset())
        else:
            h.idp.inactive = True

    h.engine_ops.before_commit = alter
    response = run(
        h.request(
            "POST", "/v2/subjects/subject-alice/enroll", browser, **scan_request()
        )
    )
    assert response.status_code in {401, 404, 409}
    assert not h.engine_ops.commits


def test_audit_failure_denies_before_operation_and_events_exclude_credentials(h):
    browser = run(h.login())
    assert (
        run(
            h.request("DELETE", "/v2/subjects/subject-alice/templates", browser)
        ).status_code
        == 200
    )
    assert (
        run(
            h.request("DELETE", "/v2/subjects/subject-bob/templates", browser)
        ).status_code
        == 404
    )
    serialized = json.dumps([asdict(e) for e in h.repo.events])
    for value in (
        browser.token,
        browser.cookie,
        browser.csrf,
        h.config.engine_key,
        "facescan",
        "embedding",
        "synthetic-refresh",
    ):
        assert value not in serialized
    count = len(h.engine_ops.calls)
    h.repo.audit_down = True
    assert (
        run(
            h.request("DELETE", "/v2/subjects/subject-alice/templates", browser)
        ).status_code
        == 503
    )
    assert len(h.engine_ops.calls) == count


def test_unconfigured_operations_fail_closed(h):
    browser = run(h.login())
    h.engine = create_engine(h.engine_sessions)
    response = run(
        h.request(
            "DELETE", "/v2/subjects/subject-alice/templates", browser, engine=True
        )
    )
    assert response.status_code == 503


def test_gateway_forwarding_reconstructs_context_and_no_cookie_leaks_to_engine(h):
    browser = run(h.login())
    assert (
        run(
            h.request(
                "POST", "/v2/subjects/subject-alice/enroll", browser, **scan_request()
            )
        ).status_code
        == 200
    )
    access = h.engine_ops.calls[0][0]
    assert access.auth.session.id == browser.session_id
    assert access.auth.principal.actor_id == "alice"
    assert access.auth.access_token == browser.token


def test_registry_is_unique_and_every_engine_route_exists_at_gateway(h):
    pairs = [(r.method, r.path) for r in ROUTES]
    assert len(pairs) == len(set(pairs))
    assert all(r in h.gateway.routes for r in h.engine.routes)
    assert all("*" not in r.path and ":path" not in r.path for r in ROUTES)
    documented = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/hardening/hardened-routes.json"
        ).read_text("utf-8")
    )
    assert [asdict(r) for r in ROUTES] == [
        {key: value for key, value in row.items() if key != "acceptance"}
        for row in documented["routes"]
    ]
    assert all(row["acceptance"] for row in documented["routes"])
