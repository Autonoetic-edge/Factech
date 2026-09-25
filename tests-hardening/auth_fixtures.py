"""Synthetic-only repository and Keycloak HTTP double, never a runtime adapter."""

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlsplit

import httpx
import msgpack
from facetech_auth.config import Config
from facetech_auth.contracts import Actor, Target, Unavailable
from facetech_auth.http import create_engine, create_gateway
from facetech_auth.limits import Limits
from facetech_auth.oidc import Provider
from facetech_auth.policy import Resource, ReviewGrant, Role, Scope
from facetech_auth.sessions import COOKIE, Sessions, TokenVault, digest
from joserfc import jwk, jwt


def run(awaitable):
    return asyncio.run(awaitable)


class MemoryRepo:
    def __init__(self):
        self.actors, self.sessions, self.logins, self.targets = {}, {}, {}, {}
        self.review_grants, self.events, self.jobs, self.replays = [], [], set(), set()
        self.down, self.audit_down = False, False
        self.now = lambda: 0  # set by Harness; rotate_token refuses an expired session

    def check(self):
        if self.down:
            raise Unavailable()

    async def put_login(self, attempt):
        self.check()
        self.logins[attempt.state_hash] = attempt

    async def consume_login(self, state_hash, browser_hash, now):
        self.check()
        attempt = self.logins.get(state_hash)
        if (
            not attempt
            or attempt.browser_hash != browser_hash
            or attempt.expires_at <= now
        ):
            return None
        return self.logins.pop(state_hash)

    async def actor_by_identity(self, issuer, sub):
        self.check()
        return next(
            (a for a in self.actors.values() if (a.issuer, a.sub) == (issuer, sub)),
            None,
        )

    async def actor(self, actor_id):
        self.check()
        return self.actors.get(actor_id)

    async def replace_session(self, old_cookie_hash, session):
        self.check()
        for sid, old in tuple(self.sessions.items()):
            if old.cookie_hash == old_cookie_hash:
                self.sessions[sid] = replace(old, active=False)
        self.sessions[session.id] = session

    async def session_by_cookie(self, cookie_hash):
        self.check()
        return next(
            (s for s in self.sessions.values() if s.cookie_hash == cookie_hash), None
        )

    async def session(self, session_id):
        self.check()
        return self.sessions.get(session_id)

    async def touch(self, session_id, now):
        self.check()
        current = self.sessions[session_id]
        if (
            current.active
            and current.expires_at > now
            and now - current.touched_at < 1800
        ):
            self.sessions[session_id] = replace(current, touched_at=now)

    async def rotate_token(self, session_id, token_ciphertext, token_hash):
        self.check()
        current = self.sessions[session_id]
        if not current.active or current.expires_at <= int(self.now()):
            raise Unavailable()
        self.sessions[session_id] = replace(
            current, token_ciphertext=token_ciphertext, token_hash=token_hash
        )

    async def revoke_and_enqueue(self, session_id):
        self.check()
        self.sessions[session_id] = replace(self.sessions[session_id], active=False)
        self.jobs.add(session_id)

    async def logout_delivered(self, session_id):
        self.check()
        self.jobs.discard(session_id)

    async def backchannel_revoke(self, issuer, sub, sid, jti, expires_at):
        self.check()
        if (issuer, jti) in self.replays:
            return False
        self.replays.add((issuer, jti))
        for key, session in tuple(self.sessions.items()):
            if (
                session.issuer == issuer
                and (not sub or session.sub == sub)
                and (not sid or session.provider_sid == sid)
            ):
                self.sessions[key] = replace(session, active=False)
        return True

    async def target(self, tenant, kind, selector):
        self.check()
        return self.targets.get((tenant, kind, selector))

    async def grants(self, actor_id):
        self.check()
        return tuple(g for g in self.review_grants if g.actor_id == actor_id)

    async def audit(self, event):
        self.check()
        if self.audit_down:
            raise Unavailable()
        self.events.append(event)


class FakeIdP:
    def __init__(self, config, clock):
        self.config, self.clock = config, clock
        self.key = jwk.RSAKey.generate_key(2048, parameters={"kid": "test-key"})
        self.codes, self.tokens = {}, {}
        self.calls = []
        self.inactive, self.outage, self.logout_outage = False, False, False
        self.overrides, self.id_overrides, self.header_overrides = {}, {}, {}
        self.discovery_overrides = {}
        self.after_introspection = None
        self.refresh_denied, self.refreshes = False, 0

    def signed(self, claims, headers=None, key=None):
        return jwt.encode(
            {"alg": "RS256", "kid": "test-key", **(headers or {})},
            claims,
            key or self.key,
        )

    def claims(self, sub, audience):
        return {
            "iss": self.config.issuer,
            "sub": sub,
            "aud": audience,
            "azp": self.config.client_id,
            "sid": "sid-" + sub,
            "iat": self.clock(),
            "exp": self.clock() + 3600,
        }

    def authorize(self, url, sub):
        params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert params["code_challenge_method"] == "S256"
        assert params["response_type"] == "code"
        assert params["redirect_uri"] == self.config.redirect_uri
        assert params["max_age"] == "0"
        code = secrets.token_urlsafe(12)
        self.codes[code] = (sub, params)
        return code, params

    def handler(self, request):
        self.calls.append(request.url.path)
        if self.outage:
            raise httpx.ConnectError("synthetic outage", request=request)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.config.issuer,
                    **self.config.endpoints,
                    "code_challenge_methods_supported": ["S256"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    **self.discovery_overrides,
                },
            )
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert request.headers["authorization"].startswith("Basic ")
        if (
            request.url.path.endswith("/token")
            and form.get("grant_type") == "refresh_token"
        ):
            sub = form["refresh_token"].removeprefix("synthetic-refresh-")
            if self.refresh_denied:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refreshes += 1
            claims = self.claims(sub, self.config.audience)
            access = self.signed(claims)
            self.tokens[access] = claims
            return httpx.Response(
                200, json={"token_type": "Bearer", "access_token": access}
            )
        if request.url.path.endswith("/token"):
            code = self.codes.pop(form["code"], None)
            if code is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            sub, params = code
            challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(form["code_verifier"].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert challenge == params["code_challenge"]
            assert form["redirect_uri"] == self.config.redirect_uri
            assert form["grant_type"] == "authorization_code"
            claims = {**self.claims(sub, self.config.audience), **self.overrides}
            access = self.signed(claims, self.header_overrides)
            self.tokens[access] = claims
            identity = {
                **self.claims(sub, self.config.client_id),
                "nonce": params["nonce"],
                "auth_time": self.clock(),
                **self.id_overrides,
            }
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": access,
                    "id_token": self.signed(identity),
                    "refresh_token": "synthetic-refresh-" + sub,
                },
            )
        if request.url.path.endswith("/introspect"):
            claims = self.tokens.get(form["token"], {})
            if self.after_introspection:
                self.after_introspection()
            return httpx.Response(
                200,
                json={
                    **claims,
                    "active": bool(claims) and not self.inactive,
                    "client_id": self.config.client_id,
                },
            )
        if request.url.path.endswith("/logout"):
            return httpx.Response(503 if self.logout_outage else 204)
        raise AssertionError("Unexpected provider endpoint")


def unlimited():
    """A limit policy that never trips.

    The M1-M3 harnesses exercise authorization, durability and privacy, and some
    of them deliberately fire a burst of requests as one actor to force a race.
    The M4 resource limits are a separate control with their own tight-policy
    tests in test_resource_limits.py; leaving the shipping policy in place here
    would make those races measure the rate limiter instead.
    """
    limits = Limits()
    for window in (limits.source, limits.auth_source, limits.actor, limits.scan):
        window.limit = 1_000_000
    limits.scans.limit = 1_000_000
    return limits


class RecordingOperations:
    def __init__(self):
        self.calls, self.commits, self.before_commit = [], [], None

    async def execute(self, access, payload, reauthorize):
        self.calls.append((access, payload))
        if self.before_commit:
            self.before_commit()
        fresh = await reauthorize()
        self.commits.append(fresh)
        # An authorization receipt only; never simulate biometric acceptance.
        return {"synthetic_authorized": True, "resource": fresh.target.id}


@dataclass
class Browser:
    cookie: str
    csrf: str
    session_id: str
    token: str


class Harness:
    def __init__(self):
        self.now = 2_000_000_000
        self.config = Config(
            "https://identity.test/realms/test",
            "bff",
            "synthetic-client-secret",
            "facetech-engine",
            "https://gateway.test",
            "https://gateway.test/auth/callback",
            "https://engine.test",
            "synthetic-service-key-0000000000000000",
        )
        self.repo = MemoryRepo()
        self.repo.now = lambda: self.now
        self.idp = FakeIdP(self.config, lambda: self.now)
        self.transport = httpx.MockTransport(self.idp.handler)
        self.vault = TokenVault({"test-v1": secrets.token_bytes(32)}, "test-v1")
        self.engine_sessions = self.sessions()
        self.gateway_sessions = self.sessions()
        self.engine_ops, self.gateway_ops = RecordingOperations(), RecordingOperations()
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
        for sub, tenant, roles in (
            ("alice", "team-a", {Role.PARTICIPANT}),
            ("bob", "team-a", {Role.PARTICIPANT}),
            ("carol", "team-b", {Role.PARTICIPANT}),
            ("reviewer", "team-a", {Role.REVIEWER}),
            ("admin", "team-a", {Role.ADMINISTRATOR}),
            ("operator", "team-a", set()),
            ("multi", "team-a", {Role.PARTICIPANT, Role.REVIEWER, Role.ADMINISTRATOR}),
        ):
            actor = Actor(sub, self.config.issuer, sub, tenant, frozenset(roles))
            self.repo.actors[sub] = actor
            for kind, selector in (
                ("subject", "subject-" + sub),
                ("capture", "capture-" + sub),
                ("receipt", "receipt-" + sub),
            ):
                self.repo.targets[tenant, kind, selector] = Target(
                    selector, Resource(tenant, sub, "round-1")
                )
            self.repo.targets[tenant, "round", "round-1"] = Target(
                "round-1", Resource(tenant, round_id="round-1")
            )
        self.repo.review_grants = [
            ReviewGrant(
                "reviewer",
                "team-a",
                "round-1",
                frozenset({Scope.METADATA}),
                self.now + 3600,
            )
        ]

    def sessions(self):
        return Sessions(
            self.repo,
            Provider(self.config, transport=self.transport, clock=lambda: self.now),
            self.vault,
            clock=lambda: self.now,
        )

    async def login(self, sub="alice", old_cookie=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.gateway), base_url=self.config.origin
        ) as client:
            if old_cookie:
                client.cookies.set(COOKIE, old_cookie)
            first = await client.get("/auth/login")
            assert first.status_code == 302, first.text
            code, params = self.idp.authorize(first.headers["location"], sub)
            callback = await client.get(
                "/auth/callback", params={"state": params["state"], "code": code}
            )
            if callback.status_code != 303:
                return callback
            cookie = client.cookies.get(COOKIE)
            session = await self.repo.session_by_cookie(digest(cookie))
            result = await client.get("/auth/session")
            assert result.status_code == 200, result.text
            return Browser(
                cookie,
                result.json()["csrf_token"],
                session.id,
                self.vault.open(session)["access_token"],
            )

    async def request(
        self, method, path, browser=None, *, engine=False, headers=None, **kwargs
    ):
        base = self.config.engine_origin if engine else self.config.origin
        app = self.engine if engine else self.gateway
        supplied = {}
        if browser:
            if engine:
                supplied = {
                    "Authorization": "Bearer " + browser.token,
                    "X-Engine-Key": self.config.engine_key,
                    "X-Facetech-Session": browser.session_id,
                }
            else:
                supplied = {
                    "Cookie": COOKIE + "=" + browser.cookie,
                    "Origin": self.config.origin,
                    "X-CSRF-Token": browser.csrf,
                }
        supplied.update(headers or {})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base
        ) as client:
            return await client.request(method, path, headers=supplied, **kwargs)


def scan_request(transport="json", subject="subject-alice"):
    raw = msgpack.packb({"version": 1, "nonce": "synthetic-no-capture"})
    headers = {"Idempotency-Key": secrets.token_hex(16)}
    if transport == "msgpack":
        return {
            "content": raw,
            "headers": {**headers, "Content-Type": "application/msgpack"},
        }
    return {
        "json": {"user_id": subject, "facescan": base64.b64encode(raw).decode()},
        "headers": headers,
    }
