"""Soft colour glow against screen replays (docs/LIVENESS_UPGRADE_PLAN.md Phase 4).

Under CHALLENGE_POLICY=single-turn-glow-v1 the challenge carries a glow schedule: 3-4
soft colours (no saturated red) that the page fades between around the oval while the
person holds still. Real skin reflects that light, so the colour of the forehead and
cheeks follows the schedule. A replayed video or a photo on another screen emits its
own light and does not follow it.

Timing contract with the SDK: every `*_ms` in the schedule is an offset from the capture
time of frame 0 (`frames[0].ts_ms`), the same origin as `settle_ms`. At offset t the page
shows `displayed(schedule, t)`: `neutral` before the first step; at each step's
`start_ms` a linear sRGB fade over `fade_ms` to that step's `rgb`; at `end_ms` the same
fade back to `neutral`. Changes are at least 500 ms apart (at most 2 per second).

FLASH_CHECK: `off` (default, or any unknown value) never runs this module's check;
`log` writes the record to the trace only; `on` also lets main.py refuse an outcome of
`fail`. Too much ambient light or an unmeasurable face gives `inconclusive`, which never
refuses anyone.
"""

import os
import secrets

import cv2
import numpy as np

MODE_ENV = "FLASH_CHECK"
OFF = "off"
LOG = "log"
ON = "on"

VERSION = "glow-v1"

# Soft palette, sRGB. No saturated red (photosensitivity, plan §9).
PALETTE = {
    "blue": (111, 168, 255),
    "green": (111, 224, 160),
    "magenta": (224, 127, 224),
    "amber": (255, 192, 97),
}
NEUTRAL = (255, 255, 255)
COLOURS_MIN = 3
COLOURS_MAX = 4
FADE_MS = 400
# WCAG 2.3.1: at most 2 changes per second.
MIN_CHANGE_MS = 500
START_MS_MIN = 300
START_MS_MAX = 500
STEP_MS_MIN = MIN_CHANGE_MS
STEP_MS_MAX = 700
GRAIN_MS = 50

# Starting guess; the threshold comes from the shadow (log) data.
FLASH_MIN = 0.5
MAX_LAG_FRAMES = 1
SEARCH_LAGS = (-2, -1, 0, 1, 2)
MIN_BASELINE_FRAMES = 1
MIN_GLOW_FRAMES = 3

# A phone screen's glow is lost in sunlight: a pre-glow face this bright is inconclusive.
BRIGHT_LUMA = 200.0
CLIP_LEVEL = 250
MAX_CLIPPED = 0.3
DARK_SUM = 30
LUMA = np.array([0.299, 0.587, 0.114])
MIN_REGION_PIXELS = 16
MIN_EYE_DISTANCE_PX = 12.0
MIN_SHIFT = 1e-4

DECIMALS = 4


def mode() -> str:
    value = os.environ.get(MODE_ENV, "").strip().lower()
    return value if value in {LOG, ON} else OFF


def _pick(choices):
    return choices[secrets.randbelow(len(choices))]


def _grained(low: int, high: int) -> int:
    return low + secrets.randbelow((high - low) // GRAIN_MS + 1) * GRAIN_MS


def draw_schedule() -> dict:
    count = COLOURS_MIN + secrets.randbelow(COLOURS_MAX - COLOURS_MIN + 1)
    at = _grained(START_MS_MIN, START_MS_MAX)
    steps, previous = [], None
    for _ in range(count):
        name = _pick([n for n in PALETTE if n != previous])
        steps.append({"colour": name, "rgb": list(PALETTE[name]), "start_ms": at})
        at += _grained(STEP_MS_MIN, STEP_MS_MAX)
        previous = name
    return {
        "version": VERSION,
        "neutral": list(NEUTRAL),
        "fade_ms": FADE_MS,
        "steps": steps,
        "end_ms": at,
    }


def displayed(schedule: dict, t_ms: float) -> np.ndarray:
    """The sRGB colour the page shows at offset t_ms from frame 0."""
    neutral = np.asarray(schedule["neutral"], np.float64)
    fade = float(schedule["fade_ms"])
    keys = [(s["start_ms"], s["rgb"]) for s in schedule["steps"]]
    keys.append((schedule["end_ms"], schedule["neutral"]))
    colour = previous = neutral
    for start, rgb in keys:
        if t_ms < start:
            break
        target = np.asarray(rgb, np.float64)
        weight = 1.0 if fade <= 0 else min(1.0, (t_ms - start) / fade)
        colour = previous + (target - previous) * weight
        previous = target
    return colour


def _chroma(rgb) -> np.ndarray:
    rgb = np.asarray(rgb, np.float64)
    total = float(rgb.sum())
    return rgb[:2] / total if total > 0 else np.zeros(2)


def expected_shift(schedule: dict, t_ms: float) -> np.ndarray:
    return _chroma(displayed(schedule, t_ms)) - _chroma(schedule["neutral"])


def regions(detection) -> list[tuple[float, float, float, float]] | None:
    """Forehead, left cheek, right cheek boxes (x0, y0, x1, y1) from the 5 keypoints."""
    if detection is None:
        return None
    points = np.asarray(detection.get("landmarks_5", ()), np.float64)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        return None
    left_eye, right_eye, _nose, left_mouth, right_mouth = points
    eye_distance = float(np.hypot(*(right_eye - left_eye)))
    if eye_distance < MIN_EYE_DISTANCE_PX:
        return None
    eye_y = (left_eye[1] + right_eye[1]) / 2.0
    mouth_y = (left_mouth[1] + right_mouth[1]) / 2.0
    mid_x = (left_eye[0] + right_eye[0]) / 2.0
    boxes = [
        (
            mid_x - 0.45 * eye_distance,
            eye_y - 0.75 * eye_distance,
            mid_x + 0.45 * eye_distance,
            eye_y - 0.35 * eye_distance,
        )
    ]
    half = 0.18 * eye_distance
    cheek_y = eye_y + 0.55 * (mouth_y - eye_y)
    for eye, mouth in ((left_eye, left_mouth), (right_eye, right_mouth)):
        cx = (eye[0] + mouth[0]) / 2.0
        boxes.append((cx - half, cheek_y - half, cx + half, cheek_y + half))
    return boxes


def measure(image, detection) -> dict | None:
    """Mean chromaticity per region, face luma and clipped fraction; None = unmeasurable."""
    boxes = regions(detection)
    if boxes is None or image is None:
        return None
    height, width = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    chroma, lumas, clipped, total = [], [], 0, 0
    for x0, y0, x1, y1 in boxes:
        x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
        x1, y1 = min(width, int(round(x1))), min(height, int(round(y1)))
        if (x1 - x0) * (y1 - y0) < MIN_REGION_PIXELS:
            return None
        pixels = rgb[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
        hot = (pixels >= CLIP_LEVEL).any(axis=1)
        keep = ~hot & (pixels.sum(axis=1) >= DARK_SUM)
        clipped += int(hot.sum())
        total += len(pixels)
        lumas.append(float((pixels @ LUMA).mean()))
        if keep.sum() < max(MIN_REGION_PIXELS, len(pixels) // 2):
            chroma.append(None)
            continue
        chroma.append(_chroma(pixels[keep].mean(axis=0)))
    return {
        "chroma": chroma,
        "luma": float(np.mean(lumas)),
        "clipped": clipped / total if total else 1.0,
    }


def _complete(measurement: dict) -> bool:
    return all(c is not None for c in measurement["chroma"])


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < MIN_SHIFT or nb < MIN_SHIFT:
        return 0.0
    return float(a @ b / (na * nb))


def _round(value) -> float:
    return round(float(value), DECIMALS)


def _summary(schedule: dict) -> dict:
    return {
        "version": schedule.get("version"),
        "steps": [[s["colour"], s["start_ms"]] for s in schedule["steps"]],
        "end_ms": schedule["end_ms"],
    }


def check(frames, detections, schedule, decode) -> dict:
    """Correlate the face's colour shift with the issued glow.

    Evidence too weak to judge (too bright, no baseline, too few measurable frames) is
    `inconclusive`, never `fail`.
    """
    record = {"outcome": "inconclusive", "reason": None, "flash_min": FLASH_MIN}

    def finish(outcome, reason=None):
        record["outcome"], record["reason"] = outcome, reason
        return record

    if not isinstance(schedule, dict) or not schedule.get("steps"):
        return finish("inconclusive", "no_glow_issued")
    record["schedule"] = _summary(schedule)
    if not frames:
        return finish("inconclusive", "no_frames")

    origin = frames[0]["ts_ms"]
    offsets = [f["ts_ms"] - origin for f in frames]
    first = schedule["steps"][0]["start_ms"]
    window_end = schedule["end_ms"] + schedule["fade_ms"] + MIN_CHANGE_MS
    in_window = [i for i, t in enumerate(offsets) if t < window_end]

    measured = {}
    for i in in_window:
        detection = detections[i] if i < len(detections) else None
        if detection is None:
            continue
        m = measure(decode(frames[i]["jpeg_bytes"]), detection)
        if m is not None:
            measured[i] = m

    before = [i for i in in_window if offsets[i] < first and i in measured]
    if not before:
        return finish("inconclusive", "no_baseline")
    base_luma = float(np.mean([measured[i]["luma"] for i in before]))
    base_clipped = float(np.mean([measured[i]["clipped"] for i in before]))
    record["baseline_luma"] = _round(base_luma)
    record["clipped"] = _round(base_clipped)
    if base_luma > BRIGHT_LUMA or base_clipped > MAX_CLIPPED:
        return finish("inconclusive", "too_bright")
    baseline = [i for i in before if _complete(measured[i])]
    record["baseline_frames"] = len(baseline)
    if len(baseline) < MIN_BASELINE_FRAMES:
        return finish("inconclusive", "no_baseline")

    reference = [
        np.mean([measured[i]["chroma"][r] for i in baseline], axis=0) for r in range(3)
    ]
    usable = [i for i in in_window if i in measured and _complete(measured[i])]
    glow = [i for i in usable if offsets[i] >= first]
    record["glow_frames"] = len(glow)
    if len(glow) < MIN_GLOW_FRAMES:
        return finish("inconclusive", "too_few_frames")

    def expected_at(index):
        if index < 0 or index >= len(offsets):
            return np.zeros(2)
        return expected_shift(schedule, offsets[index])

    shifts = {
        i: np.concatenate([measured[i]["chroma"][r] - reference[r] for r in range(3)])
        for i in usable
    }
    observed = np.concatenate([shifts[i] for i in usable])
    correlations = {}
    for lag in SEARCH_LAGS:
        expected = np.concatenate([np.tile(expected_at(i - lag), 3) for i in usable])
        correlations[lag] = _cosine(observed, expected)
    best = max(SEARCH_LAGS, key=lambda lag: (correlations[lag], -abs(lag)))
    aligned = np.concatenate([np.tile(expected_at(i), 3) for i in usable])
    power = float(aligned @ aligned)
    record["gain"] = _round(observed @ aligned / power) if power > 0 else None
    record["correlations"] = {str(k): _round(v) for k, v in correlations.items()}
    record["lag"] = best
    record["score"] = _round(correlations[best])
    record["frames"] = [
        {
            "index": i,
            "t_ms": offsets[i],
            "expected": [_round(v) for v in expected_at(i)],
            "shift": [_round(v) for v in shifts[i]],
            "luma": _round(measured[i]["luma"]),
        }
        for i in usable
    ]
    if abs(best) > MAX_LAG_FRAMES:
        return finish("fail", "lag")
    if correlations[best] < FLASH_MIN:
        return finish("fail", "low_correlation")
    return finish("pass")
