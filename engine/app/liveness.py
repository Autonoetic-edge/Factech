import hashlib
import statistics
from dataclasses import dataclass

import cv2
import numpy as np

from app import challenge as challenge_mod
from app import flags
from app.anti_spoof import SINGLE_POLICY_VERSION
from app.detect import ALIGN_SIZE, decode_jpeg
from app.vendor import face_align

LIVENESS_THRESHOLD = 0.5

PHASH_SIDE = 64

PHASH_NEAR_HAMMING = 8

DUPLICATE_CROP_EPS = 0.75

MAX_IDENTICAL_FRAMES = 3

MOTION_FLOOR = 0.75

MOTION_LIVE = 3.0

# pad-single-v1 (500 ms cadence) only. A still hold of up to SINGLE_TURN_SETTLE_MS_MAX
# gives 5 near-identical frames, and 500 ms neighbours move less than 1400 ms ones, so
# the hold may repeat and motion compares frames MOTION_STRIDE apart (1500 ms).
SINGLE_MAX_IDENTICAL_FRAMES = 5
SINGLE_MOTION_STRIDE = 3

LANDMARK_JITTER_FLOOR = 0.004
LANDMARK_JITTER_LIVE = 0.02
LANDMARK_JITTER_CEILING = 0.60

CENTRE_DRIFT_LIVE = 0.01
CENTRE_DRIFT_CEILING = 0.50

MIN_USABLE_FRAMES = 4

YAW_DELTA_FLOOR = 0.15
YAW_DELTA_LIVE = 0.35

YAW_OPPOSITE_CEILING = 0.20

SCALE_DELTA_FLOOR = 0.22
SCALE_DELTA_LIVE = 0.47

SCALE_OPPOSITE_CEILING = 0.15

SCALE_SETTLE_CEILING = 0.08

YAW_SETTLE_CEILING = 0.10

SCALE_RAMP_SPAN = SCALE_DELTA_LIVE - SCALE_DELTA_FLOOR
YAW_RAMP_SPAN = YAW_DELTA_LIVE - YAW_DELTA_FLOOR

MIN_SETTLE_FRAMES = 2

INSUFFICIENT_SETTLE_REASON = "insufficient frames in settle window"

MIN_APPROACH_FRAMES = 2

DEPTH_FLOOR: float | None = None
DEPTH_LIVE: float | None = None

DEPTH_CEILING: float | None = None

DEPTH_NEAR_FRAMES = 3

DEPTH_MIN_SCALE_GAIN = 0.05

MOIRE_BAND_LO = 0.35
MOIRE_BAND_HI = 0.85

MOIRE_FLOOR: float | None = None
MOIRE_LIVE: float | None = None

CAPTURE_CADENCE_MS = 500

MIN_FRAME_GAP_MS = 12
MAX_FRAME_GAP_MS = 1500

MAX_IMPLAUSIBLE_GAP_FRACTION = 0.25

MAX_SCAN_SPAN_MS = 30_000

ENFORCE_ENV = "LIVENESS_ENFORCE"

def enforcement_enabled() -> bool:
    return flags.enabled(ENFORCE_ENV, True)


@dataclass(frozen=True)
class Signal:
    name: str
    score: float | None
    detail: dict
    advisory: bool = False

    @property
    def ok(self) -> bool | None:
        return None if self.score is None else self.score >= LIVENESS_THRESHOLD

    @property
    def votes(self) -> bool:
        return not self.advisory and self.score is not None

    def as_dict(self) -> dict:
        body = {
            "score": None if self.score is None else round(self.score, 4),
            "ok": self.ok,
            **self.detail,
        }
        if self.advisory:
            body["advisory"] = True
        return body


@dataclass(frozen=True)
class LivenessResult:
    score: float
    signals: dict[str, Signal]

    @property
    def live(self) -> bool:
        return self.score >= LIVENESS_THRESHOLD

    @property
    def failed(self) -> list[str]:
        voting = [s for s in self.signals.values() if s.votes]
        ordered = sorted(voting, key=lambda s: s.score)
        return [s.name for s in ordered if not s.ok]

    @property
    def retryable(self) -> bool:
        failing = [s for s in self.signals.values() if s.votes and not s.ok]
        return bool(failing) and all(s.detail.get("retryable") for s in failing)

    def as_dict(self) -> dict:
        return {
            "live": self.live,
            "score": round(self.score, 4),
            "threshold": LIVENESS_THRESHOLD,
            "signals": {name: s.as_dict() for name, s in self.signals.items()},
        }


def check_liveness(
    frames: list[dict],
    detections: list[dict | None],
    challenge: "challenge_mod.ChallengeVerdict | None" = None,
    pad_policy: str | None = None,
) -> LivenessResult:
    if len(frames) != len(detections):
        raise ValueError(
            f"frames/detections length mismatch: {len(frames)} vs {len(detections)}"
        )
    images = [decode_jpeg(f["jpeg_bytes"]) for f in frames]
    crops = _aligned_crops(images, detections)
    single = pad_policy == SINGLE_POLICY_VERSION
    signals = {
        s.name: s
        for s in (
            _duplicate_signal(frames, images, crops, single),
            _motion_signal(crops, single),
            _landmark_signal(detections),
            _timing_signal(frames, challenge),
            _challenge_signal(frames, detections, challenge),
            _depth_signal(frames, detections, challenge),
            _moire_signal(crops),
        )
    }
    voting = [s.score for s in signals.values() if s.votes]

    return LivenessResult(score=min(voting, default=0.0), signals=signals)


def _ramp(value: float, fail_at: float, pass_at: float) -> float:
    return max(0.0, min(1.0, 0.5 + 0.5 * (value - fail_at) / (pass_at - fail_at)))


def _band(value: float, floor: float, live: float, ceiling: float) -> float:
    return min(_ramp(value, floor, live), _ramp(value, ceiling, live))


def _phash(img) -> int:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(
        gray, (PHASH_SIDE, PHASH_SIDE), interpolation=cv2.INTER_AREA
    ).astype(np.float32)
    value = 0
    for bit in (small > small.mean()).ravel():
        value = (value << 1) | int(bit)
    return value


def _same_picture(digests, hashes, crops, i: int, j: int) -> bool:
    if digests[i] == digests[j]:
        return True
    if hashes[i] is None or hashes[j] is None:
        return False
    if (hashes[i] ^ hashes[j]).bit_count() > PHASH_NEAR_HAMMING:
        return False
    if crops[i] is None or crops[j] is None:
        return True
    return float(np.mean(np.abs(crops[i] - crops[j]))) <= DUPLICATE_CROP_EPS


def _duplicate_signal(
    frames: list[dict], images: list, crops: list, single: bool = False
) -> Signal:
    digests = [hashlib.sha256(f["jpeg_bytes"]).hexdigest() for f in frames]
    hashes = [None if img is None else _phash(img) for img in images]

    group = list(range(len(frames)))
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            if group[j] == j and _same_picture(digests, hashes, crops, i, j):
                group[j] = group[i]
    sizes: dict[int, int] = {}
    for owner in group:
        sizes[owner] = sizes.get(owner, 0) + 1

    limit = SINGLE_MAX_IDENTICAL_FRAMES if single else MAX_IDENTICAL_FRAMES
    detail = {
        "frames": len(frames),
        "distinct_sha256": len(set(digests)),
        "distinct_images": len(sizes),
        "largest_identical_group": max(sizes.values(), default=0),
        "undecodable_frames": sum(h is None for h in hashes),
    }
    if single:
        detail["max_identical_frames"] = limit
    return Signal(
        name="duplicates",
        score=_ramp(max(sizes.values(), default=0), limit + 0.5, 1.0),
        detail=detail,
    )


def _aligned_crops(images: list, detections: list[dict | None]) -> list:
    crops = []
    for img, det in zip(images, detections):
        if img is None or det is None:
            crops.append(None)
            continue
        landmark = np.asarray(det["landmarks_5"], dtype=np.float32)
        aligned = face_align.norm_crop(img, landmark, image_size=ALIGN_SIZE)
        crops.append(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY).astype(np.float32))
    return crops


def _motion_signal(crops: list, single: bool = False) -> Signal:
    stride = SINGLE_MOTION_STRIDE if single else 1
    diffs = [
        float(np.mean(np.abs(a - b)))
        for a, b in zip(crops, crops[stride:])
        if a is not None and b is not None
    ]
    usable = sum(c is not None for c in crops)
    if not diffs or usable < MIN_USABLE_FRAMES:
        return Signal(
            name="motion",
            score=0.0,
            detail={
                "usable_frames": usable,
                "pairs": len(diffs),
                "reason": f"fewer than {MIN_USABLE_FRAMES} frames with a usable face",
            },
        )
    mean_diff = statistics.fmean(diffs)
    detail = {
        "usable_frames": usable,
        "pairs": len(diffs),
        "mean_abs_diff": round(mean_diff, 4),
        "min_abs_diff": round(min(diffs), 4),
    }
    if single:
        detail["pair_stride"] = stride
    return Signal(
        name="motion",
        score=_ramp(mean_diff, MOTION_FLOOR, MOTION_LIVE),
        detail=detail,
    )


def _landmark_signal(detections: list[dict | None]) -> Signal:
    usable = [d for d in detections if d is not None]
    if len(usable) < MIN_USABLE_FRAMES:
        return Signal(
            name="landmarks",
            score=0.0,
            detail={
                "usable_frames": len(usable),
                "reason": f"fewer than {MIN_USABLE_FRAMES} frames with a usable face",
            },
        )

    points = np.asarray([d["landmarks_5"] for d in usable], dtype=np.float64)
    boxes = np.asarray([d["bbox"] for d in usable], dtype=np.float64)

    interocular = float(np.mean(np.linalg.norm(points[:, 0] - points[:, 1], axis=1)))
    centres = np.stack(
        [(boxes[:, 0] + boxes[:, 2]) / 2.0, (boxes[:, 1] + boxes[:, 3]) / 2.0], axis=1
    )
    diagonal = float(
        np.mean(np.hypot(boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]))
    )
    if interocular <= 0.0 or diagonal <= 0.0:
        return Signal(
            name="landmarks",
            score=0.0,
            detail={
                "usable_frames": len(usable),
                "reason": "degenerate face geometry (zero inter-ocular or bbox)",
            },
        )

    jitter = float(np.mean(np.std(points, axis=0))) / interocular
    drift = float(np.mean(np.std(centres, axis=0))) / diagonal
    return Signal(
        name="landmarks",
        score=min(
            _band(
                jitter,
                LANDMARK_JITTER_FLOOR,
                LANDMARK_JITTER_LIVE,
                LANDMARK_JITTER_CEILING,
            ),
            _ramp(drift, CENTRE_DRIFT_CEILING, CENTRE_DRIFT_LIVE),
        ),
        detail={
            "usable_frames": len(usable),
            "landmark_jitter": round(jitter, 5),
            "centre_drift": round(drift, 5),
        },
    )


def _timing_signal(frames: list[dict], challenge=None) -> Signal:
    gaps = [b["ts_ms"] - a["ts_ms"] for a, b in zip(frames, frames[1:])]
    if not gaps:
        return Signal(
            name="timing", score=0.0, detail={"reason": "fewer than 2 frames"}
        )
    cadence, max_gap, max_span = CAPTURE_CADENCE_MS, MAX_FRAME_GAP_MS, MAX_SCAN_SPAN_MS
    if challenge is not None and challenge.ok and challenge.action == challenge_mod.HEAD_SEQUENCE:
        from app import head_sequence

        cadence, max_gap, max_span = (
            head_sequence.INTERVAL_MS, head_sequence.MAX_GAP_MS, head_sequence.MAX_SPAN_MS
        )
    implausible = sum(not (MIN_FRAME_GAP_MS <= g <= max_gap) for g in gaps)
    fraction = implausible / len(gaps)
    span = frames[-1]["ts_ms"] - frames[0]["ts_ms"]
    nominal_span = len(gaps) * cadence
    return Signal(
        name="timing",
        score=min(
            _ramp(fraction, MAX_IMPLAUSIBLE_GAP_FRACTION, 0.0),
            _ramp(span, max_span, nominal_span),
        ),
        detail={
            "median_gap_ms": round(statistics.median(gaps), 1),
            "min_gap_ms": min(gaps),
            "max_gap_ms": max(gaps),
            "span_ms": span,
            "implausible_gaps": implausible,
            "expected_cadence_ms": cadence,
        },
    )


def _yaw_proxy(detection: dict) -> float | None:
    points = detection.get("landmarks_5")
    if points is None or len(points) < 3:
        return None
    eye_left_x, eye_right_x, nose_x = (
        float(points[0][0]),
        float(points[1][0]),
        float(points[2][0]),
    )
    to_left = nose_x - eye_left_x
    to_right = eye_right_x - nose_x
    if to_left <= 0.0 or to_right <= 0.0:
        return None
    return float(np.log(to_left / to_right))


def _scale_proxy(detection: dict) -> float | None:
    points = detection.get("landmarks_5")
    if points is None or len(points) < 5:
        return None
    p = np.asarray(points, dtype=np.float64)
    interocular = float(np.linalg.norm(p[0] - p[1]))
    eye_mid, mouth_mid = (p[0] + p[1]) / 2.0, (p[3] + p[4]) / 2.0
    eye_to_mouth = float(np.linalg.norm(mouth_mid - eye_mid))
    if interocular <= 0.0 or eye_to_mouth <= 0.0:
        return None
    return float(np.log((interocular + eye_to_mouth) / 2.0))


@dataclass(frozen=True)
class _Verifier:
    proxy: object
    label: str
    default_floor: float
    ramp_span: float

    opposite_ceiling: float

    settle_ceiling: float
    sign: int


_VERIFIERS = {
    challenge_mod.LOOK_LEFT: _Verifier(
        _yaw_proxy,
        "yaw",
        YAW_DELTA_FLOOR,
        YAW_RAMP_SPAN,
        YAW_OPPOSITE_CEILING,
        YAW_SETTLE_CEILING,
        +1,
    ),
    challenge_mod.LOOK_RIGHT: _Verifier(
        _yaw_proxy,
        "yaw",
        YAW_DELTA_FLOOR,
        YAW_RAMP_SPAN,
        YAW_OPPOSITE_CEILING,
        YAW_SETTLE_CEILING,
        -1,
    ),
    challenge_mod.MOVE_CLOSER: _Verifier(
        _scale_proxy,
        "scale",
        SCALE_DELTA_FLOOR,
        SCALE_RAMP_SPAN,
        SCALE_OPPOSITE_CEILING,
        SCALE_SETTLE_CEILING,
        +1,
    ),
}


def _settle_split(
    frames: list[dict],
    values: list[float | None],
    settle_ms: int | None,
) -> tuple[list[float], list[float]]:
    pairs = [(f["ts_ms"], v) for f, v in zip(frames, values) if v is not None]
    if not pairs or not frames:
        return [], []
    if settle_ms is None:
        return [pairs[0][1]], [v for _, v in pairs[1:]]
    origin = frames[0]["ts_ms"]
    settle = [v for ts, v in pairs if ts - origin <= settle_ms]
    approach = [v for ts, v in pairs if ts - origin > settle_ms]
    return settle, approach


def _challenge_signal(
    frames: list[dict],
    detections: list[dict | None],
    verdict: "challenge_mod.ChallengeVerdict | None",
) -> Signal:
    if verdict is None:
        return Signal(
            name="challenge",
            score=0.0,
            detail={"nonce_ok": False, "reason": "no challenge was checked"},
        )
    if not verdict.ok:
        return Signal(name="challenge", score=0.0, detail=verdict.as_dict())

    if verdict.action == challenge_mod.HEAD_SEQUENCE:
        from app import head_sequence

        # Frozen v2-slow unless HEAD_GATE_POLICY selects the development version.
        detail = head_sequence.validate_for(frames, detections, verdict.params)
        return Signal(
            name="challenge", score=1.0 if detail["ok"] else 0.0, detail=detail
        )

    action = verdict.action
    if action not in _VERIFIERS:
        return Signal(
            name="challenge",
            score=0.0,
            detail={
                **verdict.as_dict(),
                "reason": f"{action} is not verifiable with a 5-point detector",
            },
        )
    v = _VERIFIERS[action]
    settle_ms, target = _challenge_params(verdict, v)

    values = [None if d is None else v.proxy(d) for d in detections]
    usable = [x for x in values if x is not None]
    if len(usable) < MIN_USABLE_FRAMES:
        return Signal(
            name="challenge",
            score=0.0,
            detail={
                **verdict.as_dict(),
                "usable_frames": len(usable),
                "reason": f"fewer than {MIN_USABLE_FRAMES} frames with a usable pose",
            },
        )

    settle, approach = _settle_split(frames, values, settle_ms)
    evidence = {
        **verdict.as_dict(),
        "usable_frames": len(usable),
        "settle_frames": len(settle),
        "approach_frames": len(approach),
        "target": round(target, 4),
    }
    if len(approach) < MIN_APPROACH_FRAMES:
        return Signal(
            name="challenge",
            score=0.0,
            detail={
                **evidence,
                "reason": (
                    f"fewer than {MIN_APPROACH_FRAMES} usable frames after the "
                    "settle window"
                ),
            },
        )

    if len(settle) >= MIN_SETTLE_FRAMES:
        spread = max(settle) - min(settle)
        settle_score = _ramp(spread, v.settle_ceiling, 0.0)
        evidence[f"{v.label}_settle_spread"] = round(spread, 4)
    elif settle_ms is not None:
        return Signal(
            name="challenge",
            score=0.0,
            detail={
                **evidence,
                "settle_checked": False,
                "retryable": True,
                "reason": INSUFFICIENT_SETTLE_REASON,
            },
        )
    else:
        settle_score = 1.0
        evidence["settle_checked"] = False

    baseline = statistics.median(settle) if settle else usable[0]
    deltas = [x - baseline for x in approach]
    rise, fall = max(deltas), -min(deltas)
    toward, away = (rise, fall) if v.sign > 0 else (fall, rise)
    evidence.update(
        {
            f"{v.label}_toward": round(toward + 0.0, 4),
            f"{v.label}_away": round(away + 0.0, 4),
            f"{v.label}_baseline": round(baseline, 4),
        }
    )
    return Signal(
        name="challenge",
        score=min(
            settle_score,
            _ramp(toward, target, target + v.ramp_span),
            _ramp(away, v.opposite_ceiling, 0.0),
        ),
        detail=evidence,
    )


def _challenge_params(
    verdict: "challenge_mod.ChallengeVerdict",
    verifier: _Verifier,
) -> tuple[int | None, float]:
    params = verdict.params if isinstance(verdict.params, dict) else {}
    settle = params.get("settle_ms")
    target = params.get("target")
    return (
        settle if isinstance(settle, int) and not isinstance(settle, bool) else None,
        float(target)
        if isinstance(target, (int, float)) and not isinstance(target, bool)
        else verifier.default_floor,
    )


def _shape_ratios(detection: dict) -> tuple[float, float, float] | None:
    points = detection.get("landmarks_5")
    if points is None or len(points) < 5:
        return None
    p = np.asarray(points, dtype=np.float64)
    interocular = float(np.linalg.norm(p[0] - p[1]))
    eye_mid, mouth_mid = (p[0] + p[1]) / 2.0, (p[3] + p[4]) / 2.0
    eye_to_mouth = float(np.linalg.norm(mouth_mid - eye_mid))
    mouth_width = float(np.linalg.norm(p[3] - p[4]))
    nose_offset = float(np.linalg.norm(p[2] - eye_mid))
    if interocular <= 0.0 or eye_to_mouth <= 0.0:
        return None
    return (
        interocular / eye_to_mouth,
        nose_offset / eye_to_mouth,
        mouth_width / interocular,
    )


def _depth_signal(
    frames: list[dict],
    detections: list[dict | None],
    verdict: "challenge_mod.ChallengeVerdict | None",
) -> Signal:
    settle_ms = (
        _challenge_params(verdict, _VERIFIERS[challenge_mod.MOVE_CLOSER])[0]
        if verdict is not None and verdict.ok
        else None
    )
    ratios = [None if d is None else _shape_ratios(d) for d in detections]
    scales = [None if d is None else _scale_proxy(d) for d in detections]

    usable = [
        (f["ts_ms"], r, sc)
        for f, r, sc in zip(frames, ratios, scales)
        if r is not None and sc is not None
    ]
    detail: dict = {"usable_frames": len(usable)}
    if len(usable) < MIN_USABLE_FRAMES:
        detail["reason"] = (
            f"fewer than {MIN_USABLE_FRAMES} frames with usable landmarks"
        )
        return Signal(name="depth", score=None, detail=detail, advisory=True)

    origin = frames[0]["ts_ms"]
    far = [
        (r, sc) for ts, r, sc in usable if settle_ms is None or ts - origin <= settle_ms
    ]
    if len(far) < MIN_SETTLE_FRAMES:
        far = [(r, sc) for _, r, sc in usable[: max(1, len(usable) // 3)]]
        detail["far_end"] = "leading third"
    else:
        detail["far_end"] = "settle window"
    near = [(r, sc) for _, r, sc in usable[-DEPTH_NEAR_FRAMES:]]

    far_ratios = [statistics.median([r[k] for r, _ in far]) for k in range(3)]
    near_ratios = [statistics.median([r[k] for r, _ in near]) for k in range(3)]
    scale_gain = statistics.median([sc for _, sc in near]) - statistics.median(
        [sc for _, sc in far]
    )
    changes = [
        abs(n - f) / f if f > 0.0 else 0.0 for f, n in zip(far_ratios, near_ratios)
    ]
    deformation = statistics.fmean(changes)
    detail.update(
        {
            "far_frames": len(far),
            "near_frames": len(near),
            "scale_gain": round(scale_gain, 4),
            "ratio_change": [round(c, 5) for c in changes],
            "deformation": round(deformation, 5),
        }
    )
    if scale_gain < DEPTH_MIN_SCALE_GAIN:
        detail["reason"] = (
            f"scale gain {scale_gain:.3f} below {DEPTH_MIN_SCALE_GAIN}: no approach "
            "to measure shape change against"
        )
        return Signal(name="depth", score=None, detail=detail, advisory=True)
    detail["depth_index"] = round(deformation / scale_gain, 5)

    if DEPTH_FLOOR is None or DEPTH_LIVE is None or DEPTH_CEILING is None:
        detail["reason"] = "no measured threshold yet (README.md)"
        return Signal(name="depth", score=None, detail=detail, advisory=True)
    return Signal(
        name="depth",
        score=_band(detail["depth_index"], DEPTH_FLOOR, DEPTH_LIVE, DEPTH_CEILING),
        detail=detail,
        advisory=True,
    )


def _ring_mask(shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    yy = np.arange(h).reshape(-1, 1) - h / 2.0
    xx = np.arange(w).reshape(1, -1) - w / 2.0
    radius = np.hypot(yy / (h / 2.0), xx / (w / 2.0))
    return (radius >= MOIRE_BAND_LO) & (radius <= MOIRE_BAND_HI)


def _moire_peakiness(crop: np.ndarray) -> float | None:
    h, w = crop.shape[:2]
    if h < 8 or w < 8:
        return None
    window = np.outer(np.hanning(h), np.hanning(w))
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(crop * window)))
    band = spectrum[_ring_mask((h, w))]
    median = float(np.median(band))
    if median <= 0.0:
        return None
    return float(band.max() / median)


def _moire_signal(crops: list) -> Signal:
    scores = [
        peak
        for crop in crops
        if crop is not None and (peak := _moire_peakiness(crop)) is not None
    ]
    detail: dict = {"usable_frames": len(scores)}
    if len(scores) < MIN_USABLE_FRAMES:
        detail["reason"] = f"fewer than {MIN_USABLE_FRAMES} frames with a usable crop"
        return Signal(name="moire", score=None, detail=detail, advisory=True)
    peakiness = float(statistics.median(scores))
    detail.update(
        {
            "peakiness": round(peakiness, 3),
            "max_peakiness": round(max(scores), 3),
            "band": [MOIRE_BAND_LO, MOIRE_BAND_HI],
        }
    )
    if MOIRE_FLOOR is None or MOIRE_LIVE is None:
        detail["reason"] = "no measured threshold yet (README.md)"
        return Signal(name="moire", score=None, detail=detail, advisory=True)

    return Signal(
        name="moire",
        score=_ramp(peakiness, MOIRE_FLOOR, MOIRE_LIVE),
        detail=detail,
        advisory=True,
    )
