"""Repository ports for the isolated profile; no default/in-memory runtime store.

Implementations must use shared durable state. Consume login attempts, replace
sessions, revoke-and-enqueue and accept logout replay IDs atomically. Repository
failures raise Unavailable. Test doubles live exclusively in tests-hardening.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from .policy import Principal, Resource, ReviewGrant, Role


class Denied(Exception):
    def __init__(
        self, code="AUTHENTICATION_REQUIRED", status=401, message=None, retry_after=None
    ):
        self.code, self.status, self.message = code, status, message
        self.retry_after = retry_after  # seconds, emitted as the Retry-After header
        super().__init__(code)


class Unavailable(Denied):
    def __init__(self):
        super().__init__("DEPENDENCY_UNAVAILABLE", 503)


@dataclass(frozen=True)
class Actor:
    id: str
    issuer: str
    sub: str
    tenant: str
    roles: frozenset[Role]
    epoch: int = 0
    active: bool = True


@dataclass(frozen=True)
class LoginAttempt:
    state_hash: str
    browser_hash: str
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class Session:
    id: str
    cookie_hash: str
    actor_id: str
    epoch: int
    issuer: str
    sub: str
    provider_sid: str
    authenticated_at: int
    created_at: int
    touched_at: int
    expires_at: int
    csrf_hash: str
    token_ciphertext: bytes = field(repr=False)
    token_hash: str = field(repr=False)
    active: bool = True


@dataclass(frozen=True)
class Authenticated:
    principal: Principal
    session: Session
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class Target:
    """Loaded from a tenant-scoped repository lookup, never request ownership."""

    id: str
    resource: Resource
    generation: int = 0
    active: bool = True


@dataclass(frozen=True)
class Access:
    auth: Authenticated
    action: str
    target: Target
    grants: tuple[ReviewGrant, ...]
    request_id: str


@dataclass(frozen=True)
class Audit:
    request_id: str
    actor_id: str | None
    action: str
    resource_id: str | None
    outcome: str
    at: int


class Repository(Protocol):
    async def put_login(self, attempt: LoginAttempt) -> None: ...

    async def consume_login(
        self, state_hash: str, browser_hash: str, now: int
    ) -> LoginAttempt | None:
        """Single-use CAS, expiry checked atomically, browser-bound."""
        ...

    async def actor_by_identity(self, issuer: str, sub: str) -> Actor | None: ...

    async def actor(self, actor_id: str) -> Actor | None: ...

    async def replace_session(
        self, old_cookie_hash: str | None, session: Session
    ) -> None:
        """Insert new session and revoke old session in the same transaction."""
        ...

    async def session_by_cookie(self, cookie_hash: str) -> Session | None: ...

    async def session(self, session_id: str) -> Session | None: ...

    async def touch(self, session_id: str, now: int) -> None:
        """Only active/unexpired sessions; never revive or extend absolute expiry."""
        ...

    async def rotate_token(
        self, session_id: str, token_ciphertext: bytes, token_hash: str
    ) -> None:
        """Store a refreshed provider token on an active, unexpired session only.
        Never changes created_at, touched_at or expires_at."""
        ...

    async def revoke_and_enqueue(self, session_id: str) -> None:
        """Atomically revoke locally and enqueue encrypted remote logout job."""
        ...

    async def logout_delivered(self, session_id: str) -> None: ...

    async def backchannel_revoke(
        self, issuer: str, sub: str | None, sid: str | None, jti: str, expires_at: int
    ) -> bool:
        """Atomic issuer-scoped replay claim + revoke matching sessions.

        If both sub and sid are given both must match. Persist replay IDs through
        expires_at; repeat deliveries return False without resurrecting sessions.
        """
        ...

    async def target(self, tenant: str, kind: str, selector: str) -> Target | None:
        """Read metadata only, tenant filtered before lookup; no capture bytes."""
        ...

    async def grants(self, actor_id: str) -> tuple[ReviewGrant, ...]: ...

    async def audit(self, event: Audit) -> None:
        """Durable append. On failure raise Unavailable, never drop silently."""
        ...


class Operations(Protocol):
    async def execute(
        self,
        access: Access,
        payload: dict,
        reauthorize: Callable[[], Awaitable[Access]],
    ) -> dict:
        """M2/M3 integration port; implementations MUST reauthorize after work.

        Before returning data/committing acceptance call reauthorize, then assert
        its session/actor epoch/subject generation/grants under the same database
        transaction as the result and audit. Lists must query tenant/round from
        access.target before pagination; participant receipts expose status only.
        Challenge/consent/retention/idempotency checks remain mandatory. Never
        connect the legacy inference or capture store without these controls.
        """
        ...


class UnconfiguredOperations:
    async def execute(self, access, payload, reauthorize):
        raise Unavailable()
