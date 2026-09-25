"""Historical replay plus comparison of the VPS trial against opt-in v3-guided.

`pad-sequence-v3-guided` is a development-only policy version. It is never the
default: `head_sequence.selected_policy()` retains the legacy routing name unless
`HEAD_GATE_POLICY` names the new one explicitly.
The default validator now uses the authorized September 22 VPS trial rules;
historical recorded outcomes remain unchanged in the fixture.

Nine traces from four or five people are development data. They establish
nothing about recognition accuracy or spoof resistance, and the synthetic
adversarial traces below are replayed numbers, not presentation attacks.
"""

import os

import pytest
from test_head_gate_replay import V2_SLOW, landmarks_for, replay

from app import head_sequence

GUIDED = head_sequence.POLICY_V3_GUIDED

# The four hints the new gate may return. Coarse by design: no score, no
# threshold, no yaw value, no PAD detail.
HINTS = {
    head_sequence.HOLD_STILL,
    head_sequence.TURN_SOONER,
    head_sequence.TURN_LESS_OR_MORE,
    head_sequence.TRY_AGAIN,
}

# Every synthetic trace uses the same cadence: 12 frames, ~1450 ms apart.
TS = [0, 1450, 2900, 4350, 5800, 7250, 8700, 10150, 11600, 13050, 14500, 15950]
PARAMS = {"settle_ms": 3200, "switch_ms": 9200, "first_sign": 1, "target": 0.20}

STILL = [0.0, 0.0]
RIGHT = [1.2, 1.2]
LEFT = [-1.2, -1.2]


def synthetic(pairs, params=PARAMS):
    frames = [{"ts_ms": ts} for ts in TS]
    detections = [{"landmarks_5": landmarks_for(*p)} for p in pairs]
    return frames, detections, params


def guided(pairs, params=PARAMS):
    return head_sequence.validate_guided(*synthetic(pairs, params))


def frozen(pairs, params=PARAMS):
    return head_sequence.validate(*synthetic(pairs, params))


def replay_guided(trace):
    frames = [{"ts_ms": ts} for ts in trace["ts_rel_ms"]]
    detections = [
        None if pair is None else {"landmarks_5": landmarks_for(*pair)}
        for pair in trace["yaw"]
    ]
    return head_sequence.validate_guided(frames, detections, trace["params"])


# --- the frozen gate stays the frozen gate ----------------------------------


def test_the_default_policy_is_the_frozen_one(monkeypatch):
    monkeypatch.delenv(head_sequence.POLICY_ENV, raising=False)
    assert head_sequence.selected_policy() == head_sequence.POLICY_V2_SLOW
    assert head_sequence.POLICY_V2_SLOW == "pad-sequence-v2-slow"


@pytest.mark.parametrize("value", ["", "  ", "v3", "pad-sequence-v3", "1", "true"])
def test_only_the_exact_name_selects_the_new_policy(monkeypatch, value):
    monkeypatch.setenv(head_sequence.POLICY_ENV, value)
    assert head_sequence.selected_policy() == head_sequence.POLICY_V2_SLOW


def test_the_new_policy_is_selectable_by_configuration(monkeypatch):
    monkeypatch.setenv(head_sequence.POLICY_ENV, GUIDED)
    assert head_sequence.selected_policy() == GUIDED


@pytest.mark.parametrize("trace", V2_SLOW, ids=lambda t: f"scan{t['id']}")
def test_default_routing_adds_trial_metadata_to_current_validator(monkeypatch, trace):
    """Routing preserves the verdict and adds the deployed policy and limits."""
    monkeypatch.delenv(head_sequence.POLICY_ENV, raising=False)
    frames = [{"ts_ms": ts} for ts in trace["ts_rel_ms"]]
    detections = [
        None if pair is None else {"landmarks_5": landmarks_for(*pair)}
        for pair in trace["yaw"]
    ]
    result = head_sequence.validate_for(frames, detections, trace["params"])
    assert result.pop("policy") == "amfatec-switch-trial-20260922"
    assert result.pop("limits") == {"still_range": .20, "frontal": .30,
                                    "turn": trace["params"]["target"],
                                    "hold_frames": 2, "switch_grace_frames": 1}
    assert result == replay(trace)


def test_the_frozen_gate_carries_no_policy_or_hint_field():
    """An unlabelled head_sequence block in a trace means v2-slow ran."""
    assert "hint" not in frozen([STILL] * 12)
    assert "policy" not in frozen([STILL] * 12)


# --- old versus new over the nine real traces -------------------------------


def test_old_versus_new_scoreboard():
    old = {t["id"]: t["recorded"] for t in V2_SLOW}
    new = {t["id"]: replay_guided(t) for t in V2_SLOW}

    assert [i for i in old if old[i]["ok"]] == [13, 15, 17, 21]
    assert [i for i in new if new[i]["ok"]] == [13, 15, 16, 17, 18, 19, 20, 21]

    # Every trace the frozen gate passed is still passed. The new gate only
    # relaxes; it never flips a pass to a fail.
    assert all(new[i]["ok"] for i in old if old[i]["ok"])

    # 14 is a genuine late turn, not a tolerance problem: the participant was
    # still turned the wrong way for three of the four second-window frames.
    assert new[14]["ok"] is False
    assert new[14]["reason"] == "action_order_or_rotation"
    assert new[14]["hint"] == head_sequence.TURN_SOONER


@pytest.mark.parametrize("trace", V2_SLOW, ids=lambda t: f"scan{t['id']}")
def test_every_guided_verdict_reports_its_policy_and_a_valid_hint(trace):
    result = replay_guided(trace)
    assert result["policy"] == GUIDED
    if result["ok"]:
        assert result["hint"] is None
    else:
        assert result["hint"] in HINTS


def test_hints_leak_no_numbers():
    for trace in V2_SLOW:
        hint = replay_guided(trace)["hint"]
        assert hint is None or (hint in HINTS and not any(c.isdigit() for c in hint))


# --- adversarial synthetic traces that must still fail ----------------------

ATTACKS = {
    # A photograph, or a person who simply did not move.
    "no_movement": [STILL] * 12,
    # A single-frame spike: a flick, a landmark glitch, one video field.
    "single_peak": [STILL] * 4
    + [RIGHT, STILL, STILL]
    + [STILL]
    + [LEFT, LEFT]
    + [STILL] * 2,
    # Turned correctly, then swung back the wrong way inside the same window.
    # Proves the late-switch tolerance is leading-run only, not a blanket pass.
    "wrong_way_after_valid": [STILL] * 4
    + [RIGHT, RIGHT, RIGHT]
    + [STILL]
    + [LEFT, LEFT, RIGHT, RIGHT],
    # Never settled: no two baseline frames agree.
    "never_still": [[0.0, 0.0], [0.24, 0.24], [-0.24, -0.24]]
    + [RIGHT] * 4
    + [LEFT] * 5,
    # Settled, but not looking at the camera.
    "not_frontal": [[0.4, 0.4]] * 3 + [RIGHT] * 4 + [LEFT] * 5,
    # Only one anatomical ratio moves: a tilted phone or a tilted print, not a
    # head. Both ratios must agree.
    "one_ratio_only": [STILL] * 4 + [[1.2, 0.0]] * 3 + [STILL] + [[-1.2, 0.0]] * 4,
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_adversarial_traces_still_fail_under_the_new_policy(name):
    result = guided(ATTACKS[name])
    assert result["ok"] is False, name
    assert result["hint"] in HINTS


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_adversarial_traces_also_fail_under_the_frozen_policy(name):
    """The new policy is not weaker than the old one on the attack set."""
    assert frozen(ATTACKS[name])["ok"] is False, name


def test_a_re_timed_capture_still_fails_on_cadence():
    frames = [{"ts_ms": ts // 2} for ts in TS]
    detections = [{"landmarks_5": landmarks_for(*STILL)} for _ in TS]
    result = head_sequence.validate_guided(frames, detections, PARAMS)
    assert result["ok"] is False
    assert result["reason"] == "timing"


def test_the_wrong_way_swing_is_named_as_such():
    result = guided(ATTACKS["wrong_way_after_valid"])
    assert result["reason"] == "opposite_action_in_window"


@pytest.mark.parametrize("name", ["never_still", "not_frontal"])
def test_unstable_or_off_centre_baselines_ask_the_user_to_hold_still(name):
    result = guided(ATTACKS[name])
    assert result["reason"] == "not_frontal_and_still"
    assert result["hint"] == head_sequence.HOLD_STILL


# --- and one that must pass -------------------------------------------------


def test_a_clean_synthetic_trace_passes():
    result = guided([STILL] * 4 + [RIGHT] * 3 + [STILL] + [LEFT] * 4)
    assert result["ok"] is True
    assert result["reason"] is None
    assert result["hint"] is None
    assert result["settle_inliers"] == 3


def test_one_settle_outlier_is_tolerated_but_two_disagreements_are_not():
    """Rule 1, in isolation: a majority that agrees, not an unbroken span."""
    turns = [RIGHT] * 3 + [STILL] + [LEFT] * 4
    one_outlier = [[0.0, 0.21], [0.0, 0.0], [0.0, 0.0], STILL] + turns
    assert guided(one_outlier)["ok"] is True
    assert frozen(one_outlier)["ok"] is False
    assert guided(one_outlier)["settle_inliers"] == 2


def test_the_environment_is_not_read_at_import_time(monkeypatch):
    monkeypatch.setenv(head_sequence.POLICY_ENV, GUIDED)
    assert head_sequence.selected_policy() == GUIDED
    monkeypatch.delenv(head_sequence.POLICY_ENV)
    assert head_sequence.selected_policy() == head_sequence.POLICY_V2_SLOW
    assert os.environ.get(head_sequence.POLICY_ENV) is None
