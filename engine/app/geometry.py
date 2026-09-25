"""Nose parallax against a plane (docs/LIVENESS_UPGRADE_PLAN.md Phase 2). Log only.

A print or a screen keeps all five SCRFD keypoints on one plane, so a homography fitted
on the four non-nose points (eyes, mouth corners) carries the nose with them: the
residual stays near 0 however the plane is tilted. A real nose stands in front of that
plane and leaves it as the head turns, so the residual grows with the turn.

Pure function over keypoints and timestamps. It returns a record for the trace and never
a verdict; PAD_GEOMETRY=on is not wired to reject yet (it behaves as log).
"""

import os

import cv2
import numpy as np

from app import liveness

MODE_ENV = "PAD_GEOMETRY"

NOSE = 2
PLANE = (0, 1, 3, 4)

MIN_SETTLE_FRAMES = 2
MIN_TURNED_FRAMES = 3
# |yaw - settle median| in liveness._yaw_proxy units (the challenge gate's floor).
TURN_MIN = liveness.YAW_DELTA_FLOOR
PERCENTILE = 90


def mode() -> str:
    value = os.environ.get(MODE_ENV, "").strip().lower()
    return "log" if value in {"log", "on"} else "off"


def _points(detection) -> np.ndarray | None:
    if detection is None:
        return None
    points = np.asarray(detection.get("landmarks_5", ()), dtype=np.float64)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        return None
    return points


def nose_residual(
    detections: list[dict | None], ts_ms: list[int], settle_ms: int | None
) -> dict:
    """Residual = |projected nose - detected nose| / inter-ocular, per turned frame.

    The homography is fitted by least squares from the plane points of every settle
    frame to the turned frame's plane points; the projected nose is the mean of the
    settle noses carried by it. Score = 90th percentile over turned frames.
    """
    points = [_points(d) for d in detections]
    usable = [i for i, p in enumerate(points) if p is not None]
    record = {
        "outcome": "inconclusive",
        "score": None,
        "settle_frames": 0,
        "turned_frames": 0,
        "samples": [],
    }
    if not usable or len(ts_ms) != len(detections):
        record["reason"] = "no_face"
        return record
    origin = ts_ms[0]
    if settle_ms is None:
        settle = usable[:1]
    else:
        settle = [i for i in usable if ts_ms[i] - origin <= settle_ms]
    yaws = {i: liveness._yaw_proxy(detections[i]) for i in usable}
    settle_yaws = [yaws[i] for i in settle if yaws[i] is not None]
    record["settle_frames"] = len(settle)
    if len(settle) < MIN_SETTLE_FRAMES or not settle_yaws:
        record["reason"] = "settle_frames"
        return record
    baseline = float(np.median(settle_yaws))
    record["baseline_yaw"] = round(baseline, 4)
    turned = [
        i
        for i in usable
        if i not in settle
        and yaws[i] is not None
        and abs(yaws[i] - baseline) >= TURN_MIN
    ]
    source = np.vstack([points[i][list(PLANE)] for i in settle]).astype(np.float32)
    noses = np.asarray([points[i][NOSE] for i in settle], dtype=np.float32)
    residuals = []
    for i in turned:
        target = np.tile(points[i][list(PLANE)], (len(settle), 1)).astype(np.float32)
        homography, _ = cv2.findHomography(source, target, 0)
        interocular = float(np.linalg.norm(points[i][0] - points[i][1]))
        if homography is None or interocular <= 0.0:
            continue
        carried = cv2.perspectiveTransform(noses.reshape(-1, 1, 2), homography)
        projected = carried.reshape(-1, 2).astype(np.float64).mean(axis=0)
        residual = float(np.linalg.norm(projected - points[i][NOSE]) / interocular)
        if not np.isfinite(residual):
            continue
        residuals.append(residual)
        record["samples"].append(
            {
                "index": i,
                "yaw": round(float(yaws[i] - baseline), 4),
                "residual": round(residual, 5),
            }
        )
    record["turned_frames"] = len(residuals)
    if len(residuals) < MIN_TURNED_FRAMES:
        record["reason"] = "turned_frames"
        return record
    record["outcome"] = "scored"
    record["score"] = round(float(np.percentile(residuals, PERCENTILE)), 5)
    return record
