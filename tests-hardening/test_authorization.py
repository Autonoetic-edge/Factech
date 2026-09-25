"""Synthetic subset of A02/A05/A07; not HTTP or identity-provider evidence."""

from dataclasses import replace

import pytest
from facetech_auth.policy import (
    RECENT_LOGIN_ACTIONS,
    SELF_ACTIONS,
    Action,
    Principal,
    Resource,
    ReviewGrant,
    Role,
    Scope,
    authorize,
)

NOW = 10_000
ALICE = Principal(
    "alice", "team-a", frozenset({Role.PARTICIPANT}), True, True, NOW + 60, NOW - 1
)
OWN = Resource("team-a", "alice", "round-2")
BOB_RESOURCE = Resource("team-a", "bob", "round-2")
REVIEWER = replace(ALICE, actor_id="reviewer", roles=frozenset({Role.REVIEWER}))
GRANT = ReviewGrant(
    "reviewer",
    "team-a",
    "round-2",
    frozenset({Scope.METADATA, Scope.FRAMES, Scope.AGGREGATES}),
    NOW + 60,
)


@pytest.mark.parametrize("action", sorted(SELF_ACTIONS))
def test_self_service_actions_never_delegate(action):
    assert authorize(ALICE, action, OWN, now=NOW).allowed
    assert not authorize(ALICE, action, BOB_RESOURCE, now=NOW).allowed
    assert not authorize(
        ALICE, action, replace(OWN, owner_actor_id=None), now=NOW
    ).allowed
    for roles in (
        frozenset(),
        frozenset({Role.REVIEWER}),
        frozenset({Role.ADMINISTRATOR}),
        frozenset({Role.REVIEWER, Role.ADMINISTRATOR}),
        frozenset({"operator"}),
    ):
        assert not authorize(replace(ALICE, roles=roles), action, OWN, now=NOW).allowed
    # Adding any privileged role never gives a participant access to Bob.
    elevated = replace(ALICE, roles=frozenset(Role))
    assert not authorize(elevated, action, BOB_RESOURCE, now=NOW).allowed


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        replace(ALICE, active=False),
        replace(ALICE, session_active=False),
        replace(ALICE, session_expires_at=NOW),
        replace(ALICE, authenticated_at=NOW + 1),
        replace(ALICE, actor_id=""),
        replace(ALICE, tenant_id=""),
    ],
)
def test_invalid_identity_denies_every_action(action, invalid):
    assert not authorize(invalid, action, OWN, now=NOW, grants=(GRANT,)).allowed


@pytest.mark.parametrize("action", list(Action))
def test_cross_tenant_denied_even_with_same_actor_id_and_all_roles(action):
    principal = replace(ALICE, roles=frozenset(Role))
    resource = replace(OWN, tenant_id="team-b")
    forged_scope = replace(GRANT, actor_id="alice", tenant_id="team-b")
    assert not authorize(
        principal, action, resource, now=NOW, grants=(forged_scope,)
    ).allowed


@pytest.mark.parametrize("action", sorted(RECENT_LOGIN_ACTIONS))
def test_recent_login_exact_boundary(action):
    assert authorize(
        replace(ALICE, authenticated_at=NOW - 299), action, OWN, now=NOW
    ).allowed
    assert not authorize(
        replace(ALICE, authenticated_at=NOW - 300), action, OWN, now=NOW
    ).allowed


@pytest.mark.parametrize(
    "action",
    [
        Action.READ_RECEIPT,
        Action.LIST_CAPTURES,
        Action.READ_CAPTURE,
        Action.READ_DIAGNOSTICS,
        Action.READ_FRAME,
        Action.READ_AGGREGATES,
    ],
)
def test_review_requires_matching_live_grant(action):
    assert authorize(REVIEWER, action, OWN, now=NOW, grants=(GRANT,)).allowed
    assert not authorize(REVIEWER, action, OWN, now=NOW).allowed
    for invalid in (
        replace(GRANT, revoked=True),
        replace(GRANT, expires_at=NOW),
        replace(GRANT, actor_id="other"),
        replace(GRANT, tenant_id="other"),
        replace(GRANT, round_id="round-1"),
        replace(GRANT, scopes=frozenset()),
    ):
        assert not authorize(REVIEWER, action, OWN, now=NOW, grants=(invalid,)).allowed
    assert not authorize(
        REVIEWER, action, replace(OWN, round_id=None), now=NOW, grants=(GRANT,)
    ).allowed


def test_frame_permission_does_not_follow_metadata_permission():
    metadata = replace(GRANT, scopes=frozenset({Scope.METADATA}))
    assert authorize(
        REVIEWER, Action.READ_CAPTURE, OWN, now=NOW, grants=(metadata,)
    ).allowed
    assert not authorize(
        REVIEWER, Action.READ_FRAME, OWN, now=NOW, grants=(metadata,)
    ).allowed
    frames = replace(GRANT, scopes=frozenset({Scope.FRAMES}))
    assert not authorize(
        REVIEWER, Action.READ_FRAME, OWN, now=NOW, grants=(frames,)
    ).allowed


def test_participant_receipt_does_not_expose_images_or_diagnostics():
    assert authorize(ALICE, Action.READ_RECEIPT, OWN, now=NOW).allowed
    assert not authorize(ALICE, Action.READ_RECEIPT, BOB_RESOURCE, now=NOW).allowed
    for action in (
        Action.LIST_CAPTURES,
        Action.READ_CAPTURE,
        Action.READ_DIAGNOSTICS,
        Action.READ_FRAME,
        Action.READ_AGGREGATES,
    ):
        assert not authorize(ALICE, action, OWN, now=NOW).allowed


def test_administrator_only_gets_aggregate_access_in_this_policy():
    admin = replace(ALICE, roles=frozenset({Role.ADMINISTRATOR}))
    for action in Action:
        assert authorize(admin, action, OWN, now=NOW).allowed == (
            action == Action.READ_AGGREGATES
        )


@pytest.mark.parametrize(
    "action",
    ["", "template.enroll.other", "capture.export", "admin.grant", "purge", "*", None],
)
def test_unregistered_actions_deny(action):
    assert not authorize(
        replace(ALICE, roles=frozenset(Role)), action, OWN, now=NOW, grants=(GRANT,)
    ).allowed


def test_rechecking_fresh_state_catches_revocation():
    assert authorize(REVIEWER, Action.READ_FRAME, OWN, now=NOW, grants=(GRANT,)).allowed
    revoked = replace(GRANT, revoked=True)
    assert not authorize(
        REVIEWER, Action.READ_FRAME, OWN, now=NOW, grants=(revoked,)
    ).allowed
    assert not authorize(
        replace(ALICE, session_active=False), Action.ENROLL, OWN, now=NOW
    ).allowed


@pytest.mark.parametrize(
    "invalid", [float("nan"), float("inf"), True, -1, "10000", None]
)
def test_invalid_timestamp_values_fail_closed(invalid):
    assert not authorize(ALICE, Action.ENROLL, OWN, now=invalid).allowed
    assert not authorize(
        replace(ALICE, session_expires_at=invalid), Action.ENROLL, OWN, now=NOW
    ).allowed
    assert not authorize(
        replace(ALICE, authenticated_at=invalid), Action.ENROLL, OWN, now=NOW
    ).allowed
    assert not authorize(
        REVIEWER,
        Action.READ_FRAME,
        OWN,
        now=NOW,
        grants=(replace(GRANT, expires_at=invalid),),
    ).allowed
