"""A01/A06/S05: real client/crypto, mocked HTTPS provider, synthetic identities."""

import base64
import hashlib
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from auth_fixtures import Browser, Harness, run
from facetech_auth.contracts import Denied, Unavailable
from facetech_auth.oidc import LOGOUT_EVENT
from facetech_auth.sessions import COOKIE, LOGIN_COOKIE, TokenVault
from joserfc import jwk


@pytest.fixture
def h():
    return Harness()


def test_registration_is_closed_by_default(h):
    response = run(h.request("GET", "/auth/register"))
    assert response.status_code == 403
    options = run(h.request("GET", "/auth/options"))
    assert options.json() == {"registration": False}
    assert options.headers["cache-control"] == "no-store"


def test_registration_preserves_pkce_state_and_binds_only_verified_identity(h):
    calls = []

    async def register(issuer, sub):
        calls.append((issuer, sub))
        actor = replace(h.repo.actors["alice"], id="new-actor", sub=sub)
        h.repo.actors[actor.id] = actor
        return actor

    h.gateway.sessions.registration = register
    # Existing provider identity, but not yet a local participant.
    h.repo.actors.pop("bob")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.gateway), base_url=h.config.origin
        ) as client:
            start = await client.get("/auth/register")
            assert start.status_code == 302
            code, params = h.idp.authorize(start.headers["location"], "bob")
            assert params["prompt"] == "create"
            assert params["code_challenge_method"] == "S256"
            query = {"state": params["state"], "code": code}
            response = await client.get("/auth/callback", params=query)
            assert response.status_code == 303
            assert calls == [(h.config.issuer, "bob")]
            assert (await client.get("/auth/callback", params=query)).status_code == 401
            assert len(calls) == 1

    run(scenario())


def test_login_cookie_and_encrypted_server_session(h):
    browser = run(h.login())
    assert isinstance(browser, Browser)
    session = h.repo.sessions[browser.session_id]
    assert browser.cookie not in repr(session)
    assert browser.token.encode() not in session.token_ciphertext
    assert browser.token not in repr(session)
    assert len(session.cookie_hash) == 64
    response = run(h.request("GET", "/auth/session", browser))
    assert set(response.json()) == {"actor_id", "tenant_id", "csrf_token", "expires_at"}
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert response.json()["actor_id"] == "alice"


def test_login_pkce_cookie_flags_callback_replay_and_rotation(h):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.gateway), base_url=h.config.origin
        ) as client:
            old = await h.login()
            client.cookies.set(COOKIE, old.cookie, domain="gateway.test", path="/")
            start = await client.get("/auth/login")
            flags = start.headers["set-cookie"]
            for expected in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
                assert expected in flags
            assert "Domain=" not in flags
            code, params = h.idp.authorize(start.headers["location"], "alice")
            attempt = next(iter(h.repo.logins.values()))
            expected = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(attempt.verifier.encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert params["code_challenge"] == expected
            callback = {"state": params["state"], "code": code}
            response = await client.get("/auth/callback", params=callback)
            assert response.status_code == 303
            assert client.cookies.get(COOKIE) != old.cookie
            assert not h.repo.sessions[old.session_id].active
            assert (
                await client.get("/auth/callback", params=callback)
            ).status_code == 401

    run(scenario())


@pytest.mark.parametrize(
    "attack", ["state", "browser", "expired", "issuer", "duplicate", "redirect"]
)
def test_callback_binding_failures(h, attack):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.gateway), base_url=h.config.origin
        ) as client:
            start = await client.get("/auth/login")
            code, params = h.idp.authorize(start.headers["location"], "alice")
            query = [("state", params["state"]), ("code", code)]
            if attack == "state":
                query[0] = ("state", "wrong")
            elif attack == "browser":
                client.cookies.clear()
            elif attack == "expired":
                h.now += 300
            elif attack == "issuer":
                query.append(("iss", "https://foreign.test"))
            elif attack == "duplicate":
                query.append(("state", params["state"]))
            else:
                query.append(("redirect_uri", "https://foreign.test"))
            response = await client.get("/auth/callback", params=query)
            assert response.status_code in {400, 401}
            assert not h.repo.sessions

    run(scenario())


@pytest.mark.parametrize(
    "claim,value",
    [
        ("iss", "https://foreign.test"),
        ("aud", "foreign"),
        ("azp", "other-client"),
        ("sub", "bob"),
        ("sid", "different-session"),
        ("nonce", "wrong"),
        ("exp", 0),
        ("exp", True),
        ("iat", 2_000_000_031),
        ("nbf", 2_000_000_031),
        ("auth_time", 1_999_999_700),
        ("auth_time", 2_000_000_001),
        ("auth_time", None),
        ("at_hash", "invalid"),
        ("aud", ["bff", "other"]),
    ],
)
def test_invalid_id_tokens_do_not_create_sessions(h, claim, value):
    h.idp.id_overrides[claim] = value
    if claim == "aud" and isinstance(value, list):
        h.idp.id_overrides["azp"] = None
    response = run(h.login())
    assert response.status_code in {401, 403}
    assert not h.repo.sessions


@pytest.mark.parametrize(
    "claim,value",
    [
        ("iss", "https://foreign.test"),
        ("aud", "wrong"),
        ("azp", "service-client"),
        ("sub", "service"),
        ("sid", None),
        ("exp", 2_000_000_000),
        ("exp", False),
        ("iat", "yesterday"),
        ("iat", 2_000_000_031),
        ("nbf", 2_000_000_031),
    ],
)
def test_invalid_access_tokens_do_not_create_sessions(h, claim, value):
    h.idp.overrides[claim] = value
    assert run(h.login()).status_code == 401
    assert not h.repo.sessions


def test_unknown_subject_is_not_provisioned_from_email_or_name(h):
    h.idp.id_overrides.update(
        {"email": "alice", "name": "alice", "roles": ["administrator"]}
    )
    assert run(h.login("unknown")).status_code == 401
    assert not h.repo.sessions


@pytest.mark.parametrize(
    "header,value",
    [
        ("jku", "https://evil.test/key"),
        ("x5u", "https://evil.test/key"),
        ("jwk", {"kty": "RSA"}),
    ],
)
def test_untrusted_key_headers_rejected_without_fetch(h, header, value):
    token = h.idp.signed(h.idp.claims("alice", h.config.audience), {header: value})
    with pytest.raises(Denied):
        run(h.gateway_sessions.provider.signed_claims(token, h.config.audience))
    assert not h.idp.calls


def test_algorithm_and_signature_rejected(h):
    provider = h.gateway_sessions.provider
    claims = h.idp.claims("alice", h.config.audience)
    for token in (
        "eyJhbGciOiJub25lIiwia2lkIjoidGVzdC1rZXkifQ.e30.",
        h.idp.signed(claims, key=jwk.RSAKey.generate_key(2048)),
    ):
        with pytest.raises(Denied):
            run(provider.signed_claims(token, h.config.audience))


def test_unknown_key_refresh_is_bounded_and_rotation_works(h):
    provider = h.gateway_sessions.provider
    claims = h.idp.claims("alice", h.config.audience)
    run(provider.signed_claims(h.idp.signed(claims), h.config.audience))
    for kid in ("unknown-1", "unknown-2", "unknown-3"):
        with pytest.raises(Denied):
            run(
                provider.signed_claims(
                    h.idp.signed(claims, {"kid": kid}), h.config.audience
                )
            )
    assert sum(p.endswith("/certs") for p in h.idp.calls) == 2
    h.now += 30
    h.idp.key = jwk.RSAKey.generate_key(2048, parameters={"kid": "rotated"})
    result = run(
        provider.signed_claims(
            h.idp.signed(claims, {"kid": "rotated"}), h.config.audience
        )
    )
    assert result["sub"] == "alice"


@pytest.mark.parametrize(
    "endpoint",
    [
        "issuer",
        "jwks_uri",
        "token_endpoint",
        "authorization_endpoint",
        "introspection_endpoint",
        "end_session_endpoint",
    ],
)
def test_discovery_cannot_redirect_credentials(h, endpoint):
    h.idp.discovery_overrides[endpoint] = "https://evil.test"
    response = run(h.request("GET", "/auth/login"))
    assert response.status_code == 503
    assert all(p.endswith("openid-configuration") for p in h.idp.calls)


@pytest.mark.parametrize(
    "state",
    [
        "inactive",
        "outage",
        "database",
        "expired",
        "idle",
        "suspended",
        "epoch",
        "revoked",
        "roles",
    ],
)
def test_fresh_session_checks_on_each_service(h, state):
    browser = run(h.login())
    if state == "inactive":
        h.idp.inactive = True
    elif state == "outage":
        h.idp.outage = True
    elif state == "database":
        h.repo.down = True
    elif state == "expired":
        h.repo.sessions[browser.session_id] = replace(
            h.repo.sessions[browser.session_id], expires_at=h.now
        )
    elif state == "idle":
        h.now += 1800
    elif state == "suspended":
        h.repo.actors["alice"] = replace(h.repo.actors["alice"], active=False)
    elif state == "epoch":
        h.repo.actors["alice"] = replace(h.repo.actors["alice"], epoch=1)
    elif state == "revoked":
        run(h.repo.revoke_and_enqueue(browser.session_id))
    else:
        h.repo.actors["alice"] = replace(h.repo.actors["alice"], roles=frozenset())
    for engine in (False, True):
        response = run(
            h.request(
                "GET", "/v2/subjects/subject-alice/templates", browser, engine=engine
            )
        )
        assert response.status_code == (
            503 if state in {"outage", "database"} else 404 if state == "roles" else 401
        )
    assert not h.engine_ops.calls


def test_login_always_asks_for_credentials_again(h):
    """max_age=0 on the authorize URL: a silent SSO redirect can never refresh auth_time."""
    response = run(h.request("GET", "/auth/login"))
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["max_age"] == ["0"]


def test_expired_access_token_is_refreshed_inside_the_session_limits(h):
    h.idp.overrides["exp"] = h.now + 300  # the realm's accessTokenLifespan
    browser = run(h.login())
    before = h.repo.sessions[browser.session_id].token_hash
    h.now += 400
    session = run(h.request("GET", "/auth/session", browser))
    assert session.status_code == 200
    assert (
        session.json()["expires_at"] == h.now + 1800
    )  # idle limit, not the token's exp
    assert h.idp.refreshes == 1
    rotated = h.repo.sessions[browser.session_id]
    assert rotated.token_hash != before
    assert rotated.expires_at == browser_expiry(h, browser)  # absolute limit untouched
    assert h.vault.open(rotated)["csrf"] == browser.csrf
    # the gateway forwards the new token, and the engine accepts it
    assert (
        run(
            h.request("GET", "/v2/subjects/subject-alice/templates", browser)
        ).status_code
        == 200
    )
    assert h.engine_ops.calls
    # the old token is dead everywhere; a second request within the new token's life does not refresh again
    assert run(h.request("GET", "/v2/info", browser, engine=True)).status_code == 401
    assert run(h.request("GET", "/v2/info", browser)).status_code == 200
    assert h.idp.refreshes == 1


def browser_expiry(h, browser):
    return h.repo.sessions[browser.session_id].created_at + 28800


@pytest.mark.parametrize("state", ["idle", "absolute", "refused", "outage"])
def test_refresh_never_revives_an_ended_session_or_a_refused_grant(h, state):
    h.idp.overrides["exp"] = h.now + 300
    browser = run(h.login())
    before = h.repo.sessions[browser.session_id].token_hash
    if state == "idle":
        h.now += 1800
    elif state == "absolute":
        h.repo.sessions[browser.session_id] = replace(
            h.repo.sessions[browser.session_id], created_at=h.now - 28800 + 400
        )
        h.now += 400
    elif state == "refused":
        h.idp.refresh_denied = True
        h.now += 400
    else:
        h.now += 400
        h.idp.outage = True
    response = run(h.request("GET", "/auth/session", browser))
    assert response.status_code == (503 if state == "outage" else 401)
    assert h.repo.sessions[browser.session_id].token_hash == before
    assert h.idp.refreshes == 0


def test_revocation_during_introspection_cannot_reactivate_session(h):
    browser = run(h.login())
    h.idp.after_introspection = lambda: h.repo.sessions.update(
        {browser.session_id: replace(h.repo.sessions[browser.session_id], active=False)}
    )
    assert run(h.request("GET", "/v2/info", browser)).status_code == 401


def test_logout_is_local_first_and_remote_retry_does_not_restore_access(h):
    browser = run(h.login())
    h.idp.logout_outage = True
    response = run(h.request("POST", "/auth/logout", browser))
    assert response.status_code == 200
    assert response.json() == {"logged_out": True, "provider_logout_pending": True}
    assert browser.session_id in h.repo.jobs
    assert run(h.request("GET", "/v2/info", browser, engine=True)).status_code == 401
    h.idp.logout_outage = False
    run(h.gateway_sessions.deliver_logout(h.repo.sessions[browser.session_id]))
    assert browser.session_id not in h.repo.jobs
    assert not h.repo.sessions[browser.session_id].active


@pytest.mark.parametrize(
    "invalid", [None, "nonce", "events", "old", "wrong-audience", "no-sub-sid"]
)
def test_backchannel_signature_replay_and_claims(h, invalid):
    alice, bob = run(h.login()), run(h.login("bob"))
    claims = {
        "iss": h.config.issuer,
        "aud": h.config.client_id,
        "sub": "alice",
        "sid": "sid-alice",
        "iat": h.now,
        "jti": "unique-logout",
        "events": {LOGOUT_EVENT: {}},
    }
    if invalid == "nonce":
        claims["nonce"] = "not-allowed"
    elif invalid == "events":
        claims["events"] = {}
    elif invalid == "old":
        claims["iat"] -= 300
    elif invalid == "wrong-audience":
        claims["aud"] = "wrong"
    elif invalid == "no-sub-sid":
        del claims["sub"], claims["sid"]
    token = h.idp.signed(claims)
    response = run(
        h.request("POST", "/auth/backchannel-logout", data={"logout_token": token})
    )
    assert response.status_code == (401 if invalid else 200)
    assert h.repo.sessions[alice.session_id].active is bool(invalid)
    assert h.repo.sessions[bob.session_id].active
    if not invalid:
        assert (
            run(
                h.request(
                    "POST", "/auth/backchannel-logout", data={"logout_token": token}
                )
            ).status_code
            == 200
        )
        assert len(h.repo.replays) == 1


def test_token_vault_tamper_wrong_session_and_missing_key_fail_closed(h):
    browser = run(h.login())
    session = h.repo.sessions[browser.session_id]
    for changed in (
        replace(session, id="other-session"),
        replace(
            session,
            token_ciphertext=session.token_ciphertext[:-1]
            + bytes([session.token_ciphertext[-1] ^ 1]),
        ),
    ):
        with pytest.raises(Unavailable):
            h.vault.open(changed)
    with pytest.raises(Unavailable):
        TokenVault({"different": b"x" * 32}, "different").open(session)


def test_login_cookies_and_states_are_not_plaintext_in_repository(h):
    response = run(h.request("GET", "/auth/login"))
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    browser = response.cookies[LOGIN_COOKIE]
    assert state not in repr(h.repo.logins)
    assert browser not in repr(h.repo.logins)


@pytest.mark.parametrize(
    "status,payload",
    [
        (503, {"error": "temporarily_unavailable"}),
        (302, {}),
        (200, []),
        (400, {}),
        (200, "malformed-json"),
    ],
)
def test_token_endpoint_protocol_failures_are_typed_unavailable(h, status, payload):
    original = h.idp.handler

    def handler(request):
        if request.url.path.endswith("/token"):
            if isinstance(payload, str):
                return httpx.Response(status, content=payload)
            return httpx.Response(status, json=payload)
        return original(request)

    h.gateway_sessions.provider.transport = httpx.MockTransport(handler)
    assert run(h.login()).status_code == 503
    assert not h.repo.sessions


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "http://identity.test/realms/test"),
        ("issuer", "https://user:password@identity.test/realms/test"),
        ("origin", "https://gateway.test/"),
        ("redirect_uri", "https://evil.test/auth/callback"),
        ("engine_origin", "http://engine.test"),
        ("client_secret", ""),
        ("engine_key", "short"),
    ],
)
def test_insecure_configuration_fails_before_any_network(h, field, value):
    with pytest.raises(ValueError):
        replace(h.config, **{field: value})
    assert not h.idp.calls
