"""Registry-wide authentication and parser boundaries for the isolated profile."""

from dataclasses import replace

import httpx
import pytest
from auth_fixtures import Harness, run, scan_request
from facetech_auth.http import ROUTES
from facetech_auth.policy import Scope
from facetech_auth.sessions import COOKIE

PROTECTED = tuple(
    r for r in ROUTES if r.kind or r.action in {"info", "session", "readiness"}
)


@pytest.fixture
def h():
    return Harness()


@pytest.mark.parametrize("route", PROTECTED, ids=lambda r: r.method + " " + r.path)
@pytest.mark.parametrize(
    "state", ["missing", "revoked", "expired", "provider-inactive"]
)
def test_every_protected_registration_requires_current_auth(h, route, state):
    browser = None if state == "missing" else run(h.login())
    if state == "revoked":
        h.repo.sessions[browser.session_id] = replace(
            h.repo.sessions[browser.session_id], active=False
        )
    elif state == "expired":
        h.repo.sessions[browser.session_id] = replace(
            h.repo.sessions[browser.session_id], expires_at=h.now
        )
    elif state == "provider-inactive":
        h.idp.inactive = True
    path = (
        route.path.replace("{id}", "subject-alice")
        .replace("{scope}", "template_authentication")
        .replace("{index}", "0")
    )
    for engine in [False, True] if route.engine else [False]:
        assert (
            run(h.request(route.method, path, browser, engine=engine)).status_code
            == 401
        )
    assert not h.engine_ops.calls and not h.gateway_ops.calls


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"[]",
        b'{"facescan":null}',
        b'{"facescan":true}',
        b'{"facescan":"%"}',
        b'{"facescan":"e30=","facescan":"e30="}',
    ],
)
@pytest.mark.parametrize("engine", [False, True])
def test_malformed_scans_return_typed_error_without_operations(h, body, engine):
    browser = run(h.login())
    response = run(
        h.request(
            "POST",
            "/v2/subjects/subject-alice/enroll",
            browser,
            engine=engine,
            content=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "0123456789abcdef0123456789abcdef",
            },
        )
    )
    assert response.status_code == 400
    assert response.json()["error"] == "MALFORMED_REQUEST"
    assert not h.engine_ops.calls


@pytest.mark.parametrize("key", [None, "short", "bad key!", "x" * 129])
@pytest.mark.parametrize("engine", [False, True])
def test_scan_without_valid_idempotency_key_is_rejected_before_operations(
    h, key, engine
):
    browser = run(h.login())
    kwargs = scan_request()
    kwargs["headers"].pop("Idempotency-Key")
    if key is not None:
        kwargs["headers"]["Idempotency-Key"] = key
    response = run(
        h.request(
            "POST",
            "/v2/subjects/subject-alice/enroll",
            browser,
            engine=engine,
            **kwargs,
        )
    )
    assert response.status_code == 400
    assert response.json()["error"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert not h.engine_ops.calls and not h.gateway_ops.calls


def test_duplicate_cookie_and_query_and_encoded_subject_deny(h):
    browser = run(h.login())
    assert (
        run(
            h.request(
                "GET",
                "/v2/info",
                browser,
                headers={"Cookie": f"{COOKIE}={browser.cookie}; {COOKIE}=other"},
            )
        ).status_code
        == 400
    )
    assert (
        run(
            h.request(
                "GET",
                "/v2/subjects/subject-alice/challenge?operation=enroll&operation=verify",
                browser,
            )
        ).status_code
        == 400
    )
    assert (
        run(
            h.request("GET", "/v2/subjects/subject%2Dalice/templates", browser)
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "change",
    ["frames-only", "expired", "other-round", "other-tenant", "revoked-during-read"],
)
def test_review_grants_are_rechecked_before_disclosure(h, change):
    reviewer = run(h.login("reviewer"))
    grant = h.repo.review_grants[0]
    changes = {
        "frames-only": {"scopes": frozenset({Scope.FRAMES})},
        "expired": {"expires_at": h.now},
        "other-round": {"round_id": "other"},
        "other-tenant": {"tenant_id": "team-b"},
    }
    if change == "revoked-during-read":
        h.gateway_ops.before_commit = lambda: h.repo.review_grants.clear()
    else:
        h.repo.review_grants[0] = replace(grant, **changes[change])
    response = run(h.request("GET", "/v2/captures/capture-alice", reviewer))
    assert response.status_code == 404
    assert not h.gateway_ops.commits


def test_provider_malformed_discovery_is_dependency_failure(h):
    h.idp.discovery_overrides["code_challenge_methods_supported"] = None
    assert run(h.request("GET", "/auth/login")).status_code == 503


def test_login_logout_audit_is_attributable(h):
    browser = run(h.login())
    assert run(h.request("POST", "/auth/logout", browser)).status_code == 200
    completed = [
        e for e in h.repo.events if e.action == "logout" and e.outcome == "completed"
    ]
    assert completed and completed[-1].actor_id == "alice"


def test_failed_dependency_never_follows_redirects_or_shared_auth_fallback(h):
    browser = run(h.login())
    h.gateway.engine_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"Location": "https://foreign.test"}
        )
    )
    assert (
        run(
            h.request("DELETE", "/v2/subjects/subject-alice/templates", browser)
        ).status_code
        == 503
    )
    assert not h.engine_ops.calls


def test_absolute_session_expiry_not_extended_by_activity(h):
    browser = run(h.login())
    session = h.repo.sessions[browser.session_id]
    h.now += 28800
    h.repo.sessions[browser.session_id] = replace(session, touched_at=h.now)
    assert run(h.request("GET", "/v2/info", browser)).status_code == 401
