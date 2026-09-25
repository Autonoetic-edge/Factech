"""Opaque, revocable server sessions with encrypted provider credentials."""

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import replace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import (
    Authenticated,
    Denied,
    LoginAttempt,
    Repository,
    Session,
    Unavailable,
)
from .oidc import Provider
from .policy import Principal

COOKIE = "__Host-facetech"
LOGIN_COOKIE = "__Host-facetech-login"
IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 28800


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class TokenVault:
    """Keys are injected by the host's secret provider; never generate on startup."""

    def __init__(self, keys: dict[str, bytes], active: str):
        if active not in keys or any(len(k) != 32 for k in keys.values()):
            raise ValueError("Explicit AES-256 token keyring required")
        if any(not k or ":" in k for k in keys):
            raise ValueError("Invalid key version")
        self._keys, self.active = dict(keys), active

    def seal(self, session_id: str, token: dict) -> bytes:
        nonce = secrets.token_bytes(12)
        aad = f"facetech:provider-token:v1:{session_id}:{self.active}".encode()
        encrypted = AESGCM(self._keys[self.active]).encrypt(
            nonce, json.dumps(token).encode(), aad
        )
        return self.active.encode() + b":" + nonce + encrypted

    def open(self, session: Session) -> dict:
        try:
            version, data = session.token_ciphertext.split(b":", 1)
            key_id = version.decode()
            aad = f"facetech:provider-token:v1:{session.id}:{key_id}".encode()
            plain = AESGCM(self._keys[key_id]).decrypt(data[:12], data[12:], aad)
            result = json.loads(plain)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (InvalidTag, KeyError, ValueError, UnicodeError) as exc:
            raise Unavailable() from exc


class Sessions:
    def __init__(
        self,
        repository: Repository,
        provider: Provider,
        vault: TokenVault,
        *,
        clock=time.time,
        registration=None,
    ):
        self.registration = registration
        self.repo, self.provider, self.vault, self.clock = (
            repository,
            provider,
            vault,
            clock,
        )

    async def begin(self, *, register=False):
        if register and self.registration is None:
            raise Denied("REGISTRATION_CLOSED", 403)
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        url = await self.provider.authorization_url(
            state, nonce, verifier, register=register
        )
        await self.repo.put_login(
            LoginAttempt(
                digest(state),
                digest(browser),
                nonce,
                verifier,
                int(self.clock()) + 300,
            )
        )
        return url, browser

    async def callback(self, state, browser, code, old_cookie=None):
        if not all(
            isinstance(v, str) and 1 <= len(v) <= 2048 for v in (state, browser, code)
        ):
            raise Denied()
        attempt = await self.repo.consume_login(
            digest(state), digest(browser), int(self.clock())
        )
        if attempt is None:
            raise Denied()
        token, claims = await self.provider.exchange(code, attempt)
        actor = await self.repo.actor_by_identity(
            self.provider.config.issuer, claims["sub"]
        )
        if actor is None and self.registration is not None:
            # Only the verified provider subject reaches registration; never browser IDs.
            actor = await self.registration(self.provider.config.issuer, claims["sub"])
        if actor is None or not actor.active:
            raise Denied()
        now = int(self.clock())
        cookie, csrf, sid = (secrets.token_urlsafe(32) for _ in range(3))
        token["csrf"] = csrf
        session = Session(
            id=sid,
            cookie_hash=digest(cookie),
            actor_id=actor.id,
            epoch=actor.epoch,
            issuer=actor.issuer,
            sub=actor.sub,
            provider_sid=claims["sid"],
            authenticated_at=claims["auth_time"],
            created_at=now,
            touched_at=now,
            expires_at=now + ABSOLUTE_SECONDS,
            csrf_hash=digest(csrf),
            token_ciphertext=self.vault.seal(sid, token),
            token_hash=digest(token["access_token"]),
        )
        await self.repo.replace_session(
            digest(old_cookie) if old_cookie else None, session
        )
        return cookie, session

    def _valid(self, session):
        now = int(self.clock())
        if (
            session is None
            or session.active is not True
            or any(
                type(t) is not int or t < 0
                for t in (
                    session.expires_at,
                    session.created_at,
                    session.touched_at,
                    session.authenticated_at,
                )
            )
            or session.expires_at <= now
            or session.created_at > now
            or session.touched_at > now
            or session.authenticated_at > now
            or now - session.created_at >= ABSOLUTE_SECONDS
            or now - session.touched_at >= IDLE_SECONDS
        ):
            raise Denied()

    async def authenticate(
        self, *, cookie=None, session_id=None, access_token=None, touch=True
    ):
        if cookie:
            session = await self.repo.session_by_cookie(digest(cookie))
        elif session_id and access_token:
            session = await self.repo.session(session_id)
        else:
            raise Denied()
        self._valid(session)
        token = access_token or self.vault.open(session)["access_token"]
        if not hmac.compare_digest(digest(token), session.token_hash):
            raise Denied()
        try:
            claims = await self.provider.active_token(token)
        except Unavailable:
            raise
        except Denied:
            # The provider's short access token (5 min) ran out inside a gateway session
            # that is still within its idle/absolute limits (_valid above). Only the
            # cookie holder refreshes; the engine is handed a token and never refreshes.
            if access_token:
                raise
            token, claims = await self._refresh(session)
        # Network checks can race logout/suspension. Always reload local state.
        session = await self.repo.session(session.id)
        self._valid(session)
        actor = await self.repo.actor(session.actor_id)
        if (
            actor is None
            or actor.active is not True
            or actor.epoch != session.epoch
            or (actor.issuer, actor.sub) != (session.issuer, session.sub)
            or (claims["iss"], claims["sub"], claims["sid"])
            != (session.issuer, session.sub, session.provider_sid)
            or not hmac.compare_digest(digest(token), session.token_hash)
        ):
            raise Denied()
        now = int(self.clock())
        if touch:
            await self.repo.touch(session.id, now)
            session = replace(session, touched_at=now)
        principal = Principal(
            actor.id,
            actor.tenant,
            actor.roles,
            True,
            True,
            # Not capped by the access token's own exp any more: it is refreshed above.
            min(session.expires_at, session.touched_at + IDLE_SECONDS),
            session.authenticated_at,
        )
        return Authenticated(principal, session, token)

    async def _refresh(self, session):
        # ponytail: no lock; two requests refreshing at once can race and one is denied.
        stored = self.vault.open(session)
        fresh = await self.provider.refresh(stored)
        token = fresh["access_token"]
        claims = await self.provider.active_token(token)
        if (claims["sub"], claims["sid"]) != (session.sub, session.provider_sid):
            raise Denied()
        document = {**stored, **fresh}  # keeps csrf and anything the provider left out
        await self.repo.rotate_token(
            session.id, self.vault.seal(session.id, document), digest(token)
        )
        return token, claims

    @staticmethod
    def csrf(session, value):
        if not value or not hmac.compare_digest(digest(value), session.csrf_hash):
            raise Denied("CSRF_REQUIRED", 403)

    async def logout(self, cookie, csrf):
        session = await self.repo.session_by_cookie(digest(cookie or ""))
        if session is None:
            raise Denied()
        self.csrf(session, csrf)
        await self.repo.revoke_and_enqueue(session.id)
        try:
            await self.deliver_logout(session)
        except Unavailable:
            return False
        return True

    async def deliver_logout(self, session):
        """Retry worker entry point: only fetch already-revoked queued sessions."""
        current = await self.repo.session(session.id)
        if current is None or current.active:
            raise Denied()
        await self.provider.remote_logout(self.vault.open(current))
        await self.repo.logout_delivered(current.id)

    async def backchannel(self, token):
        claims = await self.provider.logout_claims(token)
        return await self.repo.backchannel_revoke(
            claims["iss"],
            claims.get("sub"),
            claims.get("sid"),
            claims["jti"],
            claims["iat"] + 330,
        )
