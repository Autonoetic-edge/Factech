"""AENet beside MiniFASNet (docs/LIVENESS_UPGRADE_PLAN.md Phase 3). Log only.

NON-COMMERCIAL, DEMO ONLY. The CelebA-Spoof AENet weights are released for
non-commercial research use only. This module exists for the demo engine and must not
ship in a product. See models/anti_spoof/POLICY.md, "AENet".

PAD_ENSEMBLE:
- `minifas` (default, or any unknown value): this module's model is never loaded or run.
- `minifas+aenet:log` and `minifas+aenet`: AENet scores 3-5 of the sharpest, most frontal
  settle frames and the record goes to the trace. Neither value rejects in this phase;
  `minifas+aenet` behaves as log until the team round sets a threshold.

The model and its preprocessing come from models/anti_spoof/aenet/manifest.json, written by
engine/scripts/export_aenet.py. It is never read from the MiniFASNet manifest.json. A
missing or mismatched model gives `unavailable` in the trace, never an error response.
"""

import hashlib
import json
import threading
import time

import cv2
import numpy as np
import onnxruntime as ort

from app import anti_spoof, flags, liveness

MODE_ENV = "PAD_ENSEMBLE"
MINIFAS = "minifas"
LOG = "minifas+aenet:log"
ON = "minifas+aenet"

MODEL_DIR = anti_spoof.MODEL_DIR / "aenet"
LICENSE = (
    "CelebA-Spoof AENet: non-commercial research only; demo only, not for a product"
)

MIN_FRAMES = 3
MAX_FRAMES = 5
# The sharpest frames are picked from this many of the most frontal settle frames.
FRONTAL_POOL = 8
# Without the challenge's settle time, the first third of the scan (4 of 12 frames).
FALLBACK_SETTLE_FRAMES = 4
BUDGET_MS = 250
RUN_TIMEOUT_S = anti_spoof.RUN_TIMEOUT_S
PREPROCESS_KEYS = {
    "size",
    "crop_scale",
    "channels",
    "divide",
    "mean",
    "std",
    "live_index",
}

_model = None
_failure = None
_lock = threading.Lock()


def mode() -> str:
    return flags.choice(MODE_ENV, (MINIFAS, LOG, ON), MINIFAS)


def crop(image, bbox, scale, size):
    """SCRFD xyxy box grown by `scale` about its centre, shifted inside the frame, resized."""
    x1, y1, x2, y2 = map(float, bbox)
    if not np.isfinite([x1, y1, x2, y2]).all() or x2 <= x1 or y2 <= y1:
        raise ValueError("invalid face box")
    h, w = image.shape[:2]
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    if bw < 16 or bh < 16 or x1 < 0 or y1 < 0 or x2 >= w or y2 >= h:
        raise ValueError("face box outside frame or too small")
    scale = min((h - 1) / bh, (w - 1) / bw, scale)
    nw, nh = bw * scale, bh * scale
    left = min(max((x1 + x2) / 2 - nw / 2, 0), w - 1 - nw)
    top = min(max((y1 + y2) / 2 - nh / 2, 0), h - 1 - nh)
    patch = image[int(top) : int(top + nh) + 1, int(left) : int(left + nw) + 1]
    return cv2.resize(patch, (size, size), interpolation=cv2.INTER_LINEAR)


def sharpness(patch) -> float:
    return float(
        cv2.Laplacian(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    )


def frontal_pool(detections, ts_ms, settle_ms) -> list[tuple[int, float]]:
    """(index, yaw) of single-face settle frames, most frontal first, at most FRONTAL_POOL."""
    if len(ts_ms) != len(detections) or not ts_ms:
        return []
    if settle_ms is None:
        settle = range(min(FALLBACK_SETTLE_FRAMES, len(detections)))
    else:
        settle = [i for i in range(len(detections)) if ts_ms[i] - ts_ms[0] <= settle_ms]
    pool = []
    for i in settle:
        if detections[i] is None:
            continue
        yaw = liveness._yaw_proxy(detections[i])
        if yaw is not None and np.isfinite(yaw):
            pool.append((i, float(yaw)))
    return sorted(pool, key=lambda item: (abs(item[1]), item[0]))[:FRONTAL_POOL]


class AENetPAD:
    """CelebA-Spoof AENet, NON-COMMERCIAL, demo only. Output: P(live) for one face."""

    def __init__(self, directory=MODEL_DIR):
        manifest = json.loads((directory / "manifest.json").read_text())
        pre = manifest["preprocess"]
        if set(pre) != PREPROCESS_KEYS or pre["channels"] not in {"BGR", "RGB"}:
            raise ValueError("invalid AENet preprocess")
        self.size = int(pre["size"])
        self.scale = float(pre["crop_scale"])
        self.rgb = pre["channels"] == "RGB"
        self.divide = float(pre["divide"])
        self.mean = np.asarray(pre["mean"], np.float32).reshape(3, 1, 1)
        self.std = np.asarray(pre["std"], np.float32).reshape(3, 1, 1)
        self.live_index = int(pre["live_index"])
        self.version = str(manifest["model_version"])
        path = directory / manifest["onnx"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["onnx_sha256"]:
            raise ValueError("AENet checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        if self.session.get_inputs()[0].shape != [1, 3, self.size, self.size]:
            raise ValueError("invalid AENet model input")
        self.predict(np.zeros((self.size, self.size, 3), np.uint8))

    def tensor(self, patch):
        if self.rgb:
            patch = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
        value = patch.transpose(2, 0, 1).astype(np.float32) / self.divide
        return np.ascontiguousarray(((value - self.mean) / self.std)[None])

    def predict(self, patch) -> float:
        options = ort.RunOptions()
        timer = threading.Timer(
            RUN_TIMEOUT_S, lambda: setattr(options, "terminate", True)
        )
        timer.daemon = True
        timer.start()
        try:
            output = self.session.run(
                None, {self.session.get_inputs()[0].name: self.tensor(patch)}, options
            )[0]
        finally:
            timer.cancel()
        value = np.asarray(output, dtype=np.float64)
        if (
            value.shape != (1, 2)
            or not np.isfinite(value).all()
            or (value < 0).any()
            or (value > 1).any()
            or not np.allclose(value.sum(), 1, atol=1e-5)
        ):
            raise ValueError("invalid AENet softmax")
        return float(value[0, self.live_index])


def get_model():
    """Loaded once. A failure is remembered until restart, so a bad file is hashed once."""
    global _model, _failure
    if _model is None and _failure is None:
        with _lock:
            if _model is None and _failure is None:
                try:
                    _model = AENetPAD(MODEL_DIR)
                except FileNotFoundError:
                    _failure = "model_missing"
                except Exception as exc:
                    _failure = type(exc).__name__
    return _model


def score_scan(frames, detections, settle_ms, decode) -> dict:
    """AENet P(live) on 3-5 sharp, frontal settle frames. A record, never a verdict."""
    start = time.perf_counter()
    record = {
        "outcome": "inconclusive",
        "model": None,
        "median_live": None,
        "frames": [],
    }

    def finish(outcome, reason=None):
        record["outcome"] = outcome
        if reason is not None:
            record["reason"] = reason
        record["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        record["over_budget"] = record["latency_ms"] > BUDGET_MS
        return record

    model = get_model()
    if model is None:
        return finish("unavailable", _failure)
    record["model"] = model.version
    ts_ms = [f["ts_ms"] for f in frames]
    candidates = []
    for i, yaw in frontal_pool(detections, ts_ms, settle_ms):
        image = decode(frames[i]["jpeg_bytes"])
        if image is None:
            continue
        try:
            patch = crop(image, detections[i]["bbox"], model.scale, model.size)
        except ValueError:
            continue
        candidates.append((sharpness(patch), i, yaw, patch))
    chosen = sorted(candidates, key=lambda c: (-c[0], c[1]))[:MAX_FRAMES]
    if len(chosen) < MIN_FRAMES:
        return finish("inconclusive", "settle_frames")
    lives = []
    for sharp, i, yaw, patch in sorted(chosen, key=lambda c: c[1]):
        live = model.predict(patch)
        lives.append(live)
        record["frames"].append(
            {
                "index": i,
                "yaw": round(yaw, 4),
                "sharpness": round(sharp, 1),
                "live": round(live, 6),
            }
        )
    record["median_live"] = round(float(np.median(lives)), 6)
    return finish("scored")


def minifas_summary(pad) -> dict:
    """MiniFASNet's median P(live) over its scored frames, logged beside AENet's."""
    lives = [f["scores"][1] for f in pad.get("frames", ()) if "scores" in f]
    return {
        "outcome": pad.get("outcome"),
        "frames": len(lives),
        "median_live": round(float(np.median(lives)), 6) if lives else None,
    }
