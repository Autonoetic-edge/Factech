"""Explicit validated Keycloak/BFF configuration; never load environment files."""

import ssl
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def verified_tls(context=None):
    if context is None:
        return True
    if (
        not isinstance(context, ssl.SSLContext)
        or context.verify_mode != ssl.CERT_REQUIRED
        or not context.check_hostname
    ):
        raise ValueError("TLS certificate and hostname validation are mandatory")
    return context


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(ord(c) <= 32 for c in value)
    ):
        raise ValueError("An explicit HTTPS URL without credentials is required")
    return f"https://{parsed.netloc}"


@dataclass(frozen=True)
class Config:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    audience: str
    origin: str
    redirect_uri: str
    engine_origin: str
    engine_key: str = field(repr=False)
    # Rotation overlap: the engine also accepts the retiring key while the
    # gateway already sends the new one. Empty means no key is being retired.
    engine_key_previous: str = field(default="", repr=False)

    @property
    def engine_keys(self) -> tuple[str, ...]:
        """Keys the engine accepts. The gateway always sends `engine_key`."""
        return (
            (self.engine_key, self.engine_key_previous)
            if (self.engine_key_previous)
            else (self.engine_key,)
        )

    def __post_init__(self):
        https_url(self.issuer)
        if self.issuer.endswith("/"):
            raise ValueError("Issuer must exactly match the realm issuer")
        for origin in (self.origin, self.engine_origin):
            if https_url(origin) != origin:
                raise ValueError("Origins must contain no path")
        if self.redirect_uri != self.origin + "/auth/callback":
            raise ValueError("Only the exact configured callback is allowed")
        if (
            not all(
                isinstance(v, str) and v and not any(ord(c) <= 32 for c in v)
                for v in (
                    self.client_id,
                    self.client_secret,
                    self.audience,
                    self.engine_key,
                )
            )
            or len(self.engine_key) < 32
        ):
            raise ValueError("Explicit client credentials and service key required")
        if self.engine_key_previous and (
            len(self.engine_key_previous) < 32
            or any(ord(c) <= 32 for c in self.engine_key_previous)
            or self.engine_key_previous == self.engine_key
        ):
            raise ValueError("A retiring service key must be distinct and as strong")

    @property
    def endpoints(self) -> dict[str, str]:
        base = self.issuer + "/protocol/openid-connect/"
        return {
            "authorization_endpoint": base + "auth",
            "token_endpoint": base + "token",
            "jwks_uri": base + "certs",
            "introspection_endpoint": base + "token/introspect",
            "end_session_endpoint": base + "logout",
        }
