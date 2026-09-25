"""Deny-by-default self-service and review policy.

Inputs MUST come from verified authentication and server-side repositories. This
module does not authenticate tokens or trust headers. It performs no I/O and is
not yet an enforcement layer on the legacy HTTP services. Call again at commit
with freshly loaded session, ownership, consent and grant state.
"""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    PARTICIPANT = "participant"
    REVIEWER = "reviewer"
    ADMINISTRATOR = "administrator"


class Action(StrEnum):
    CHALLENGE_ENROLL = "challenge.enroll"
    CHALLENGE_VERIFY = "challenge.verify"
    CHALLENGE_LIVENESS = "challenge.liveness"
    ENROLL = "template.enroll"
    ADD_TEMPLATE = "template.add"
    VERIFY = "subject.verify"
    LIVENESS = "subject.liveness"
    LIST_TEMPLATES = "template.list"
    REVOKE_TEMPLATES = "template.revoke"
    GRANT_CONSENT = "consent.grant"
    WITHDRAW_CONSENT = "consent.withdraw"
    REQUEST_CAPTURE_DELETION = "capture.request_deletion"
    READ_RECEIPT = "receipt.read"
    LIST_CAPTURES = "capture.list"
    READ_CAPTURE = "capture.read"
    READ_DIAGNOSTICS = "diagnostics.read"
    READ_FRAME = "frame.read"
    READ_AGGREGATES = "capture.aggregates"


class Scope(StrEnum):
    METADATA = "metadata"
    FRAMES = "frames"
    AGGREGATES = "aggregates"


@dataclass(frozen=True)
class Principal:
    actor_id: str
    tenant_id: str
    roles: frozenset[Role]
    active: bool
    session_active: bool
    session_expires_at: int
    authenticated_at: int


@dataclass(frozen=True)
class Resource:
    tenant_id: str
    owner_actor_id: str | None = None
    round_id: str | None = None


@dataclass(frozen=True)
class ReviewGrant:
    actor_id: str
    tenant_id: str
    round_id: str
    scopes: frozenset[Scope]
    expires_at: int
    revoked: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    # Internal audit reason only. HTTP adapters must mask foreign/missing objects.
    reason: str


SELF_ACTIONS = frozenset(
    {
        Action.CHALLENGE_ENROLL,
        Action.CHALLENGE_VERIFY,
        Action.CHALLENGE_LIVENESS,
        Action.ENROLL,
        Action.ADD_TEMPLATE,
        Action.VERIFY,
        Action.LIVENESS,
        Action.LIST_TEMPLATES,
        Action.REVOKE_TEMPLATES,
        Action.GRANT_CONSENT,
        Action.WITHDRAW_CONSENT,
        Action.REQUEST_CAPTURE_DELETION,
    }
)
RECENT_LOGIN_ACTIONS = frozenset(
    {
        Action.CHALLENGE_ENROLL,
        Action.ENROLL,
        Action.ADD_TEMPLATE,
        Action.REVOKE_TEMPLATES,
    }
)
REVIEW_SCOPES = {
    Action.READ_RECEIPT: frozenset({Scope.METADATA}),
    Action.LIST_CAPTURES: frozenset({Scope.METADATA}),
    Action.READ_CAPTURE: frozenset({Scope.METADATA}),
    Action.READ_DIAGNOSTICS: frozenset({Scope.METADATA}),
    Action.READ_FRAME: frozenset({Scope.METADATA, Scope.FRAMES}),
    Action.READ_AGGREGATES: frozenset({Scope.AGGREGATES}),
}
RECENT_LOGIN_SECONDS = 300


def _timestamp(value: object) -> bool:
    return type(value) is int and value >= 0


def authorize(
    principal: Principal | None,
    action: str,
    resource: Resource,
    *,
    now: int,
    grants: tuple[ReviewGrant, ...] = (),
) -> Decision:
    """Evaluate trusted current state, never a browser-selected principal/resource.

    Administrative mutations, service duties and unspecified actions are denied
    until their separate policies exist. Lists must execute a scoped query for
    the authorized tenant/round; this function cannot filter returned rows.
    """
    if (
        not _timestamp(now)
        or principal is None
        or principal.active is not True
        or principal.session_active is not True
        or not principal.actor_id
        or not principal.tenant_id
        or not _timestamp(principal.session_expires_at)
        or not _timestamp(principal.authenticated_at)
        or principal.session_expires_at <= now
        or principal.authenticated_at > now
    ):
        return Decision(False, "authentication_required")
    if not resource.tenant_id or resource.tenant_id != principal.tenant_id:
        return Decision(False, "resource_unavailable")
    try:
        requested = Action(action)
    except (ValueError, TypeError):
        return Decision(False, "unknown_action")

    is_owner = (
        Role.PARTICIPANT in principal.roles
        and bool(resource.owner_actor_id)
        and resource.owner_actor_id == principal.actor_id
    )
    if requested in SELF_ACTIONS:
        if not is_owner:
            return Decision(False, "resource_unavailable")
        if (
            requested in RECENT_LOGIN_ACTIONS
            and now - principal.authenticated_at >= RECENT_LOGIN_SECONDS
        ):
            return Decision(False, "recent_login_required")
        return Decision(True, "self_service")

    if requested == Action.READ_RECEIPT and is_owner:
        return Decision(True, "own_receipt")
    if requested == Action.READ_AGGREGATES and Role.ADMINISTRATOR in principal.roles:
        return Decision(True, "administrative_aggregate")
    if Role.REVIEWER not in principal.roles or not resource.round_id:
        return Decision(False, "review_grant_required")
    required = REVIEW_SCOPES[requested]
    for grant in grants:
        if (
            grant.revoked is False
            and _timestamp(grant.expires_at)
            and grant.expires_at > now
            and grant.actor_id == principal.actor_id
            and grant.tenant_id == resource.tenant_id
            and grant.round_id == resource.round_id
            and required <= grant.scopes
        ):
            return Decision(True, "review_grant")
    return Decision(False, "review_grant_required")
