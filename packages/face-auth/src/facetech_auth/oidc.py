"""Pinned Authlib code/PKCE client and joserfc signature verification.

Only configured Keycloak endpoints are fetched. JWT headers select a cached key
by kid, never an issuer/URL. Provider tokens are never returned to the browser.
"""

import asyncio
import base64
import hashlib
import json
import time

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from .config import Config, verified_tls
from .contracts import Denied, Unavailable

LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


def text_claim(claims: dict, name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise Denied()
    return value


def timestamp(claims: dict, name: str) -> int:
    value = claims.get(name)
    if type(value) is not int or value < 0:
        raise Denied()
    return value


def audience_matches(value, expected: str) -> bool:
    return value == expected or (
        isinstance(value, list)
        and all(isinstance(v, str) and v for v in value)
        and expected in value
    )


class Provider:
    def __init__(
        self, config: Config, *, transport=None, clock=time.time, tls_context=None
    ):
        self.config, self.transport, self.clock = config, transport, clock
        self.tls = verified_tls(tls_context)
        self._keys = {}
        self._keys_at = None
        self._unknown_at = None
        self._lock = asyncio.Lock()

    def client(self):
        client = AsyncOAuth2Client(
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            redirect_uri=self.config.redirect_uri,
            scope="openid",
            code_challenge_method="S256",
            token_endpoint_auth_method="client_secret_basic",
            timeout=5,
            follow_redirects=False,
            trust_env=False,
            verify=self.tls,
            transport=self.transport,
        )
        client.register_compliance_hook("access_token_response", self._token_response)
        client.register_compliance_hook("refresh_token_response", self._token_response)
        return client

    @staticmethod
    def _token_response(response):
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise Unavailable()
        try:
            data = response.json()
        except ValueError as exc:
            raise Unavailable() from exc
        if not isinstance(data, dict) or len(response.content) > 65536:
            raise Unavailable()
        if response.status_code != 200 and "error" not in data:
            raise Unavailable()
        return response

    async def discovery(self):
        data = await self._get(self.config.issuer + "/.well-known/openid-configuration")
        if data.get("issuer") != self.config.issuer or any(
            data.get(k) != v for k, v in self.config.endpoints.items()
        ):
            raise Unavailable()
        for name, required in (
            ("code_challenge_methods_supported", "S256"),
            ("id_token_signing_alg_values_supported", "RS256"),
        ):
            supported = data.get(name)
            if not isinstance(supported, list) or required not in supported:
                raise Unavailable()

    async def _get(self, url):
        try:
            async with (
                httpx.AsyncClient(
                    timeout=5,
                    follow_redirects=False,
                    trust_env=False,
                    verify=self.tls,
                    transport=self.transport,
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code != 200:
                    raise Unavailable()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise Unavailable()
            data = json.loads(body)
            if not isinstance(data, dict):
                raise Unavailable()
            return data
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise Unavailable() from exc

    async def authorization_url(self, state, nonce, verifier, *, register=False):
        await self.discovery()
        async with self.client() as client:
            url, _ = client.create_authorization_url(
                self.config.endpoints["authorization_endpoint"],
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                max_age=0,
                **({"prompt": "create"} if register else {}),
            )
            return url

    async def _refresh_keys(self, now):
        data = await self._get(self.config.endpoints["jwks_uri"])
        keys = {}
        try:
            if not isinstance(data.get("keys"), list) or len(data["keys"]) > 16:
                raise Unavailable()
            for key in data["keys"]:
                if not isinstance(key, dict):
                    raise Unavailable()
                if key.get("kty") != "RSA" or key.get("use", "sig") != "sig":
                    continue
                if key.get("alg", "RS256") != "RS256":
                    continue
                kid = text_claim(key, "kid")
                if kid in keys or "d" in key:
                    raise Unavailable()
                keys[kid] = jwk.import_key(key)
            if not keys:
                raise Unavailable()
        except (JoseError, ValueError, TypeError, Denied) as exc:
            raise Unavailable() from exc
        self._keys, self._keys_at = keys, now

    async def signed_claims(self, encoded: str, audience: str, *, logout=False):
        try:
            if not isinstance(encoded, str) or not 1 <= len(encoded) <= 16384:
                raise Denied()
            parts = encoded.split(".")
            if len(parts) != 3:
                raise Denied()
            header = json.loads(
                base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
            )
            if (
                not isinstance(header, dict)
                or header.get("alg") != "RS256"
                or set(header) - {"alg", "kid", "typ"}
                or header.get("typ", "JWT") not in {"JWT", "at+jwt", "logout+jwt"}
            ):
                raise Denied()
            kid = text_claim(header, "kid")
            now = int(self.clock())
            async with self._lock:
                refreshed = self._keys_at is None or now - self._keys_at >= 300
                if refreshed:
                    await self._refresh_keys(now)
                if (
                    kid not in self._keys
                    and not refreshed
                    and (self._unknown_at is None or now - self._unknown_at >= 30)
                ):
                    self._unknown_at = now
                    await self._refresh_keys(now)
                key = self._keys.get(kid)
            if key is None:
                raise Denied()
            claims = jwt.decode(encoded, key, algorithms=["RS256"]).claims
            if not isinstance(claims, dict):
                raise Denied()
            if claims.get("iss") != self.config.issuer or not audience_matches(
                claims.get("aud"), audience
            ):
                raise Denied()
            issued = timestamp(claims, "iat")
            if issued > now + 30:
                raise Denied()
            if "nbf" in claims and timestamp(claims, "nbf") > now + 30:
                raise Denied()
            if not logout or "exp" in claims:
                expiry = timestamp(claims, "exp")
                if expiry <= now or expiry <= issued:
                    raise Denied()
            return claims
        except (JoseError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise Denied() from exc

    async def active_token(self, token):
        claims = await self.signed_claims(token, self.config.audience)
        sub, sid = text_claim(claims, "sub"), text_claim(claims, "sid")
        if claims.get("azp") != self.config.client_id:
            raise Denied()
        try:
            async with self.client() as client:
                response = await client.introspect_token(
                    self.config.endpoints["introspection_endpoint"],
                    token=token,
                    token_type_hint="access_token",
                )
            if response.status_code != 200 or len(response.content) > 65536:
                raise Unavailable()
            data = response.json()
            if not isinstance(data, dict):
                raise Unavailable()
        except (httpx.HTTPError, OAuthError, ValueError) as exc:
            raise Unavailable() from exc
        if (
            data.get("active") is not True
            or data.get("iss") != self.config.issuer
            or data.get("sub") != sub
            or data.get("sid") != sid
            or data.get("client_id") != self.config.client_id
            or not audience_matches(data.get("aud"), self.config.audience)
            or timestamp(data, "exp") <= int(self.clock())
        ):
            raise Denied()
        return claims

    async def exchange(self, code, attempt):
        try:
            async with self.client() as client:
                token = await client.fetch_token(
                    self.config.endpoints["token_endpoint"],
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=attempt.verifier,
                    redirect_uri=self.config.redirect_uri,
                )
        except httpx.HTTPError as exc:
            raise Unavailable() from exc
        except (OAuthError, ValueError, TypeError) as exc:
            raise Denied() from exc
        if (
            not isinstance(token.get("token_type"), str)
            or token["token_type"].lower() != "bearer"
        ):
            raise Denied()
        access = text_claim_token(token, "access_token")
        claims = await self.signed_claims(
            text_claim_token(token, "id_token"), self.config.client_id
        )
        if (
            claims.get("nonce") != attempt.nonce
            or claims.get("azp", self.config.client_id) != self.config.client_id
        ):
            raise Denied()
        if (
            isinstance(claims["aud"], list)
            and len(claims["aud"]) > 1
            and claims.get("azp") != self.config.client_id
        ):
            raise Denied()
        auth_time = timestamp(claims, "auth_time")
        if auth_time > int(self.clock()) or int(self.clock()) - auth_time >= 300:
            raise Denied("RECENT_LOGIN_REQUIRED", 403)
        if "at_hash" in claims:
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(access.encode()).digest()[:16])
                .rstrip(b"=")
                .decode()
            )
            if claims["at_hash"] != expected:
                raise Denied()
        active = await self.active_token(access)
        if (text_claim(claims, "sub"), text_claim(claims, "sid")) != (
            active["sub"],
            active["sid"],
        ):
            raise Denied()
        text_claim_token(token, "refresh_token")
        return dict(token), claims

    async def refresh(self, token):
        """Trade the stored refresh token for a new access token.

        Same endpoint, client authentication and response checks as ``exchange``.
        The caller re-validates the new access token with ``active_token`` and
        binds it to the same subject and provider session before storing it.
        """
        try:
            async with self.client() as client:
                fresh = await client.refresh_token(
                    self.config.endpoints["token_endpoint"],
                    refresh_token=text_claim_token(token, "refresh_token"),
                )
        except httpx.HTTPError as exc:
            raise Unavailable() from exc
        except (OAuthError, ValueError, TypeError) as exc:
            raise Denied() from exc
        if (
            not isinstance(fresh.get("token_type"), str)
            or fresh["token_type"].lower() != "bearer"
        ):
            raise Denied()
        text_claim_token(fresh, "access_token")
        result = dict(fresh)
        # Keycloak rotates the refresh token only when configured to; keep the old one otherwise.
        text_claim_token(result, "refresh_token")
        return result

    async def remote_logout(self, token):
        try:
            async with httpx.AsyncClient(
                timeout=5,
                trust_env=False,
                verify=self.tls,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.config.endpoints["end_session_endpoint"],
                    auth=(self.config.client_id, self.config.client_secret),
                    data={"refresh_token": text_claim_token(token, "refresh_token")},
                )
            if response.status_code not in {200, 204}:
                raise Unavailable()
        except httpx.HTTPError as exc:
            raise Unavailable() from exc

    async def logout_claims(self, token):
        claims = await self.signed_claims(token, self.config.client_id, logout=True)
        now = int(self.clock())
        if (
            claims.get("events") != {LOGOUT_EVENT: {}}
            or "nonce" in claims
            or now - timestamp(claims, "iat") >= 300
            or not (claims.get("sub") or claims.get("sid"))
        ):
            raise Denied()
        for key in ("jti", *(k for k in ("sub", "sid") if k in claims)):
            text_claim(claims, key)
        return claims


def text_claim_token(token, name):
    value = token.get(name)
    if not isinstance(value, str) or not value or len(value) > 16384:
        raise Denied()
    return value
