"""Mandatory, replaceable RGB PAD candidate. See models/anti_spoof/POLICY.md.

Crop arithmetic adapted from Minivision CropImage (Apache-2.0; bundled LICENSE).
No recognition alignment, normalization, RGB conversion or score calibration.
"""

import hashlib
import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "anti_spoof"
POLICY_VERSION = "pad-sequence-v2-slow"
MODEL_VERSION = "MiniFASNet-b6d5f04ad787"
MIN_USABLE = 9
MAX_GAP_MS = 3800
# single-turn-v1 (CHALLENGE_POLICY): same 12 frames and rules at 500 ms cadence. The gap
# limit keeps v2-slow's meaning, one missing sample allowed and two refused.
SINGLE_POLICY_VERSION = "pad-single-v1"
SINGLE_MAX_GAP_MS = 1400
SCAN_TIMEOUT_S = 15.0
RUN_TIMEOUT_S = 2.0
_model = None
_lock = threading.Lock()


def crop_bgr(image, bbox, scale):
    """SCRFD xyxy -> upstream inclusive xywh, expanded/clamped, 80x80 BGR."""
    x1, y1, x2, y2 = map(float, bbox)
    if not np.isfinite([x1, y1, x2, y2]).all() or x2 <= x1 or y2 <= y1:
        raise ValueError("invalid face box")
    x, y, bw, bh = int(x1), int(y1), int(x2 - x1 + 1), int(y2 - y1 + 1)
    h, w = image.shape[:2]
    if bw < 16 or bh < 16 or x < 0 or y < 0 or x2 >= w or y2 >= h:
        raise ValueError("face box outside frame or too small")
    scale = min((h - 1) / bh, (w - 1) / bw, scale)
    nw, nh = bw * scale, bh * scale
    cx, cy = bw / 2 + x, bh / 2 + y
    left, top, right, bottom = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > w - 1:
        left -= right - w + 1
        right = w - 1
    if bottom > h - 1:
        top -= bottom - h + 1
        bottom = h - 1
    patch = image[int(top) : int(bottom) + 1, int(left) : int(right) + 1]
    return cv2.resize(patch, (80, 80), interpolation=cv2.INTER_LINEAR)


def tensor_of(patch):
    return np.ascontiguousarray(patch.transpose(2, 0, 1)[None], dtype=np.float32)


def checked_scores(scores):
    value = np.asarray(scores, dtype=np.float64)
    if value.shape != (1, 3) or not np.isfinite(value).all():
        raise ValueError("invalid PAD output")
    if (
        (value < 0).any()
        or (value > 1).any()
        or not np.allclose(value.sum(), 1, atol=1e-5)
    ):
        raise ValueError("invalid PAD softmax")
    return value[0]


class MiniFASNetPAD:
    def __init__(self, directory=MODEL_DIR):
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["revision"] != "b6d5f04ad78778917853b25c778acef6d5626d15":
            raise ValueError("unrecognized PAD revision")
        self.sessions = []
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        for entry in manifest["models"]:
            path = directory / entry["onnx"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["onnx_sha256"]:
                raise ValueError("PAD checksum mismatch")
            session = ort.InferenceSession(
                str(path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            if session.get_inputs()[0].shape != [1, 3, 80, 80]:
                raise ValueError("invalid PAD model input")
            self.sessions.append((session, float(entry["scale"])))
        if len(self.sessions) != 2 or [s for _, s in self.sessions] != [2.7, 4.0]:
            raise ValueError("incomplete PAD ensemble")
        self.predict(np.zeros((160, 120, 3), np.uint8), [30, 40, 90, 120])

    def predict(self, image, bbox):
        results = []
        for session, scale in self.sessions:
            options = ort.RunOptions()
            timer = threading.Timer(
                RUN_TIMEOUT_S,
                lambda options=options: setattr(options, "terminate", True),
            )
            timer.daemon = True
            timer.start()
            try:
                output = session.run(
                    None,
                    {
                        session.get_inputs()[0].name: tensor_of(
                            crop_bgr(image, bbox, scale)
                        )
                    },
                    options,
                )[0]
                results.append(checked_scores(output))
            finally:
                timer.cancel()
        ensemble = np.mean(results, axis=0)
        return results, ensemble


def policy_for(action):
    from app import challenge  # noqa: PLC0415

    if action in challenge.SINGLE_TURN_ACTIONS:
        return SINGLE_POLICY_VERSION
    return POLICY_VERSION


def get_pad():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _model = MiniFASNetPAD()
    return _model


def evaluate(frames, detections, embed_face, decode, policy=POLICY_VERSION):
    """Use every single-face frame; same boxes for PAD and identity continuity.

    Returns private diagnostics and the mean recognition embedding. The caller
    must reject every outcome other than live, regardless of other scores.
    """
    start = time.perf_counter()
    result = {
        "model": MODEL_VERSION,
        "policy": policy,
        "outcome": "inference_error",
        "reason": None,
        "frames": [],
        "usable": 0,
        "coverage": [],
        "aggregate": None,
    }
    vectors, indices, scores = [], [], []
    max_gap = SINGLE_MAX_GAP_MS if policy == SINGLE_POLICY_VERSION else MAX_GAP_MS

    def finish(outcome, reason, embedding=None):
        result.update(
            outcome=outcome,
            reason=reason,
            usable=len(indices),
            coverage=[sum(i // 4 == third for i in indices) for third in range(3)],
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return result, embedding

    try:
        model = get_pad()
        if len(frames) != 12 or len(detections) != 12:
            return finish("insufficient_evidence", "frame_count")
        for i, (frame, faces) in enumerate(zip(frames, detections)):
            if time.perf_counter() - start > SCAN_TIMEOUT_S:
                return finish("inference_error", "deadline")
            if len(faces) > 1:
                return finish("insufficient_evidence", "multiple_faces")
            if not faces:
                result["frames"].append({"index": i, "outcome": "no_face"})
                continue
            image = decode(frame["jpeg_bytes"])
            if image is None:
                return finish("inference_error", "decode")
            try:
                members, ensemble = model.predict(image, faces[0]["bbox"])
            except ValueError as exc:
                if "face box" in str(exc):
                    result["frames"].append({"index": i, "outcome": "invalid_crop"})
                    continue
                raise
            # Also validate interchangeable implementations, not only ORT.
            ensemble = checked_scores(np.asarray(ensemble)[None])
            vector = np.asarray(embed_face(frame, faces[0]), dtype=np.float32)
            norm = np.linalg.norm(vector)
            if vector.shape != (512,) or not np.isfinite(vector).all() or norm < 1e-6:
                raise ValueError("invalid continuity embedding")
            vector /= norm
            similarities = [float(np.dot(vector, other)) for other in vectors]
            continuity = min(similarities, default=1.0)
            vectors.append(vector)
            indices.append(i)
            scores.append(ensemble)
            result["frames"].append(
                {
                    "index": i,
                    "outcome": "live" if int(np.argmax(ensemble)) == 1 else "spoof",
                    "scores": ensemble.round(6).tolist(),
                    "members": [np.asarray(v).round(6).tolist() for v in members],
                    "continuity_min": round(continuity, 5),
                }
            )
        if time.perf_counter() - start > SCAN_TIMEOUT_S:
            return finish("inference_error", "deadline")
        if scores:
            result["minimum_detection_score"] = min(
                float(detections[i][0]["det_score"]) for i in indices
            )
            result["aggregate"] = {
                "mean_scores": np.mean(scores, axis=0).round(6).tolist(),
                "minimum_live_score": round(float(min(s[1] for s in scores)), 6),
                "live_frames": sum(int(np.argmax(s)) == 1 for s in scores),
            }
        if any(f.get("continuity_min", 1) < 0.55 for f in result["frames"]):
            return finish("insufficient_evidence", "face_discontinuity")
        if any(int(np.argmax(s)) != 1 for s in scores):
            return finish("spoof", "non_live_frame")
        coverage = [sum(i // 4 == third for i in indices) for third in range(3)]
        if (
            len(indices) < MIN_USABLE
            or min(coverage) < 2
            or indices[0] > 1
            or indices[-1] < 10
        ):
            return finish("insufficient_evidence", "temporal_coverage")
        if any(
            frames[b]["ts_ms"] - frames[a]["ts_ms"] > max_gap
            for a, b in zip(indices, indices[1:])
        ):
            return finish("insufficient_evidence", "coverage_gap")
        mean = np.mean(vectors, axis=0)
        return finish("live", None, mean / np.linalg.norm(mean))
    except Exception as exc:
        return finish("inference_error", type(exc).__name__)
