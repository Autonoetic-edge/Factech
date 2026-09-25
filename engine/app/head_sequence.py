"""Ordered head-rotation evidence from SCRFD landmarks, not client pose flags.

Five-point yaw is a relative anatomical proxy, not metric 3-D pose/depth.
Translation and uniform scaling do not change it. Perspective screen tilting,
video playback and injected frames remain possible; mandatory PAD is separate.
"""

import numpy as np

from app import flags

INTERVAL_MS = 1400
REACTION_MS = 1600
MIN_SPAN_MS = 15000
MAX_SPAN_MS = 20000
MIN_GAP_MS = 1100
MAX_GAP_MS = 1900


def yaw(detection):
    p = np.asarray(detection["landmarks_5"], dtype=np.float64)
    if p.shape != (5, 2) or not np.isfinite(p).all():
        return None
    # Project onto the eye axis: in-plane roll/translation cannot count as yaw.
    axis = p[1] - p[0]
    distance = np.linalg.norm(axis)
    if distance < 8:
        return None
    axis /= distance
    eye_nose = float(np.dot(p[2] - (p[0] + p[1]) / 2, axis) / distance)
    mouth_width = float(np.dot(p[4] - p[3], axis))
    if mouth_width < 4:
        return None
    mouth_nose = float(np.dot(p[2] - (p[3] + p[4]) / 2, axis) / mouth_width)
    return eye_nose * 4, mouth_nose * 4


def validate(frames, detections, params):
    first, switch, target = params["first_sign"], params["switch_ms"], params["target"]
    settle = params["settle_ms"]
    origin = frames[0]["ts_ms"]
    span = frames[-1]["ts_ms"] - origin
    result = {
        "ok": False,
        "reason": "timing",
        "first_sign": first,
        "switch_ms": switch,
        "span_ms": span,
        "transition_ignored_frames": [],
        "phases": [],
        "yaw": [],
    }
    gaps = np.diff([f["ts_ms"] for f in frames])
    if span < MIN_SPAN_MS or span > MAX_SPAN_MS or min(gaps) < MIN_GAP_MS or max(gaps) > MAX_GAP_MS:
        return result
    phases = [[], [], []]
    for i, (frame, det) in enumerate(zip(frames, detections)):
        elapsed = frame["ts_ms"] - origin
        pose = yaw(det) if det is not None else None
        result["yaw"].append(None if pose is None else [round(v, 4) for v in pose])
        # Allow a 1600 ms reaction/transition interval after each cue.
        phase = (
            0
            if elapsed <= settle
            else 1
            if settle + REACTION_MS <= elapsed < switch
            else 2
            if elapsed >= switch + REACTION_MS
            else None
        )
        if pose is not None and phase is not None:
            phases[phase].append((i, pose))
    result["phases"] = [len(p) for p in phases]
    if min(result["phases"]) < 2:
        result["reason"] = "phase_coverage"
        return result
    baseline = np.median([p for _, p in phases[0]], axis=0)
    if (
        max(abs(baseline)) > 0.30
        or np.ptp([p for _, p in phases[0]], axis=0).max() > 0.20
    ):
        result["reason"] = "not_frontal_and_still"
        return result
    # Each action must be held for >=2 successive sampled frames. Both anatomical
    # ratios must agree; a single peak or merely moving the phone is not enough.
    for phase, sign in ((1, first), (2, -first)):
        values = [(i, (np.asarray(p) - baseline) * sign) for i, p in phases[phase]]
        valid = [i for i, delta in values if min(delta) >= target]
        if not any(b == a + 1 for a, b in zip(valid, valid[1:])):
            result["reason"] = "action_order_or_rotation"
            return result
        # One leading sample may still show the previous turn at the switch.
        # A reversal after the new turn starts remains a failure.
        ignored = [i for i, delta in values
                   if phase == 2 and i == values[0][0] and i < valid[0]
                   and min(delta) < -target]
        result["transition_ignored_frames"].extend(ignored)
        if any(min(delta) < -target for i, delta in values if i not in ignored):
            result["reason"] = "opposite_action_in_window"
            return result
    result.update(ok=True, reason=None)
    return result


# --- Development-only policy version. Not the default; not deployed. ---------
#
# `validate()` above is the frozen `pad-sequence-v2-slow` gate. It is unchanged
# and stays the default. Everything below adds a second, separately named gate
# selected only by configuration, for B2 replay work. The phase split, the
# timings, the 0.25 frontality limit, the 0.15 stillness limit and the 0.20
# target are identical; exactly two decision rules differ:
#
#   1. Stillness is judged per frame against the median baseline. At least
#      SETTLE_INLIERS_MIN settle frames must sit within STILL_TOLERANCE of it,
#      instead of the whole settle window having to fit inside that span. One
#      landmark outlier in three samples no longer vetoes an otherwise still
#      head.
#   2. Frames showing the previous turn are tolerated inside a window only as a
#      leading run before that window's first valid frame - a late switch, not
#      a wrong turn. An opposite frame at or after the first valid frame still
#      fails, exactly as before.
#
# The duplication below is deliberate: sharing code with `validate()` would
# mean editing the frozen gate.

POLICY_V2_SLOW = "pad-sequence-v2-slow"
POLICY_V3_GUIDED = "pad-sequence-v3-guided"
POLICY_ENV = "HEAD_GATE_POLICY"

STILL_TOLERANCE = 0.15
FRONTAL_LIMIT = 0.25
SETTLE_INLIERS_MIN = 2

# Coarse retry guidance. Deliberately carries no score, threshold, yaw value or
# PAD detail: it says what to do differently, nothing about how close it was.
HOLD_STILL = "hold_still"
TURN_SOONER = "turn_sooner"
TURN_LESS_OR_MORE = "turn_less_or_more"
TRY_AGAIN = "try_again"


def selected_policy() -> str:
    """The configured head-gate version. Anything but the opt-in name is frozen."""
    return flags.choice(
        POLICY_ENV, (POLICY_V2_SLOW, POLICY_V3_GUIDED), POLICY_V2_SLOW, case_sensitive=True
    )


def validate_for(frames, detections, params, policy=None):
    if (policy or selected_policy()) == POLICY_V3_GUIDED:
        return validate_guided(frames, detections, params)
    result = validate(frames, detections, params)
    result["policy"] = "amfatec-switch-trial-20260922"
    result["limits"] = {"still_range": 0.20, "frontal": 0.30, "turn": params["target"], "hold_frames": 2, "switch_grace_frames": 1}
    return result


def validate_guided(frames, detections, params):
    first, switch, target = params["first_sign"], params["switch_ms"], params["target"]
    settle = params["settle_ms"]
    origin = frames[0]["ts_ms"]
    span = frames[-1]["ts_ms"] - origin
    result = {
        "ok": False,
        "reason": "timing",
        "hint": TRY_AGAIN,
        "policy": POLICY_V3_GUIDED,
        "first_sign": first,
        "switch_ms": switch,
        "span_ms": span,
        "phases": [],
        "settle_inliers": 0,
        "yaw": [],
    }
    gaps = np.diff([f["ts_ms"] for f in frames])
    if (
        span < MIN_SPAN_MS
        or span > MAX_SPAN_MS
        or min(gaps) < MIN_GAP_MS
        or max(gaps) > MAX_GAP_MS
    ):
        return result
    phases = [[], [], []]
    for i, (frame, det) in enumerate(zip(frames, detections)):
        elapsed = frame["ts_ms"] - origin
        pose = yaw(det) if det is not None else None
        result["yaw"].append(None if pose is None else [round(v, 4) for v in pose])
        # Allow a 1600 ms reaction/transition interval after each cue.
        phase = (
            0
            if elapsed <= settle
            else 1
            if settle + REACTION_MS <= elapsed < switch
            else 2
            if elapsed >= switch + REACTION_MS
            else None
        )
        if pose is not None and phase is not None:
            phases[phase].append((i, pose))
    result["phases"] = [len(p) for p in phases]
    if min(result["phases"]) < 2:
        result["reason"] = "phase_coverage"
        return result
    observed = np.asarray([p for _, p in phases[0]], dtype=np.float64)
    baseline = np.median(observed, axis=0)
    # Rule 1: a stable majority, not an unbroken span. One outlier frame is not
    # movement; a head that never settles still has fewer than two agreeing frames.
    inliers = int((np.abs(observed - baseline).max(axis=1) <= STILL_TOLERANCE).sum())
    result["settle_inliers"] = inliers
    if max(abs(baseline)) > FRONTAL_LIMIT or inliers < SETTLE_INLIERS_MIN:
        result.update(reason="not_frontal_and_still", hint=HOLD_STILL)
        return result
    # Each action must be held for >=2 successive sampled frames. Both anatomical
    # ratios must agree; a single peak or merely moving the phone is not enough.
    for phase, sign in ((1, first), (2, -first)):
        values = [(i, (np.asarray(p) - baseline) * sign) for i, p in phases[phase]]
        valid = [i for i, delta in values if min(delta) >= target]
        # Rule 2: the previous turn may trail into this window, but only ahead of
        # the first frame that satisfies this one.
        started = valid[0] if valid else None
        late = started is not None and any(
            min(delta) < -target for i, delta in values if i < started
        )
        if any(
            min(delta) < -target
            for i, delta in values
            if started is None or i >= started
        ):
            result.update(reason="opposite_action_in_window", hint=TRY_AGAIN)
            return result
        if not any(b == a + 1 for a, b in zip(valid, valid[1:])):
            result.update(
                reason="action_order_or_rotation",
                hint=TURN_SOONER if late else TURN_LESS_OR_MORE,
            )
            return result
    result.update(ok=True, reason=None, hint=None)
    return result
