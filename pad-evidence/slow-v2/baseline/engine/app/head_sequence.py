"""Ordered head-rotation evidence from SCRFD landmarks, not client pose flags.

Five-point yaw is a relative anatomical proxy, not metric 3-D pose/depth.
Translation and uniform scaling do not change it. Perspective screen tilting,
video playback and injected frames remain possible; mandatory PAD is separate.
"""

import numpy as np


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
        "phases": [],
        "yaw": [],
    }
    gaps = np.diff([f["ts_ms"] for f in frames])
    if span < 7400 or span > 11000 or min(gaps) < 400 or max(gaps) > 1100:
        return result
    phases = [[], [], []]
    for i, (frame, det) in enumerate(zip(frames, detections)):
        elapsed = frame["ts_ms"] - origin
        pose = yaw(det) if det is not None else None
        result["yaw"].append(None if pose is None else [round(v, 4) for v in pose])
        # Allow a 700 ms reaction/transition interval after each cue.
        phase = (
            0
            if elapsed <= settle
            else 1
            if settle + 700 <= elapsed < switch
            else 2
            if elapsed >= switch + 700
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
        max(abs(baseline)) > 0.25
        or np.ptp([p for _, p in phases[0]], axis=0).max() > 0.15
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
        if any(min(delta) < -target for _, delta in values):
            result["reason"] = "opposite_action_in_window"
            return result
    result.update(ok=True, reason=None)
    return result
