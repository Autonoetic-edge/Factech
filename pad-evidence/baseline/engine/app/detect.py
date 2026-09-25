import threading
from pathlib import Path

import cv2
import numpy as np

from app.vendor import face_align
from app.vendor.scrfd import SCRFD

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
DET_MODEL_FILE = MODEL_DIR / "det_500m.onnx"
DET_INPUT_SIZE = (640, 640)
ALIGN_SIZE = 112

_detector = None
_detector_lock = threading.Lock()


def get_detector() -> SCRFD:
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                if not DET_MODEL_FILE.is_file():
                    raise FileNotFoundError(
                        f"detection model missing: {DET_MODEL_FILE} "
                        "(run engine/scripts/download_models.py)"
                    )
                detector = SCRFD(model_file=str(DET_MODEL_FILE))
                detector.prepare(-1)
                _detector = detector
    return _detector


def is_loaded() -> bool:
    return _detector is not None


def decode_jpeg(jpeg_bytes: bytes):
    buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    if buf.size == 0:
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return None
    return img


def _pad_square(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    if h == w:
        return img
    if h < w:
        pad = np.zeros((w - h, w, 3), dtype=np.uint8)
        return np.vstack((img, pad))
    pad = np.zeros((h, h - w, 3), dtype=np.uint8)
    return np.hstack((img, pad))


def _detect_all(img: np.ndarray) -> list[dict]:
    detector = get_detector()
    bboxes, kpss = detector.detect(
        _pad_square(img), input_size=DET_INPUT_SIZE, max_num=0
    )
    if bboxes is None or bboxes.shape[0] == 0 or kpss is None:
        return []
    areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
    order = np.argsort(areas)[::-1]
    return [
        {
            "bbox": [float(v) for v in bboxes[i, :4]],
            "landmarks_5": [[float(x), float(y)] for x, y in kpss[i]],
            "det_score": float(bboxes[i, 4]),
        }
        for i in order
    ]


def _detect_largest(img: np.ndarray):
    dets = _detect_all(img)
    return dets[0] if dets else None


def detect_faces(jpeg_bytes: bytes) -> list[dict]:
    img = decode_jpeg(jpeg_bytes)
    if img is None:
        return []
    return _detect_all(img)


def detect_largest_face(jpeg_bytes: bytes):
    img = decode_jpeg(jpeg_bytes)
    if img is None:
        return None
    return _detect_largest(img)


def align_largest_face(jpeg_bytes: bytes, detection: dict | None = None):
    img = decode_jpeg(jpeg_bytes)
    if img is None:
        return None
    if detection is None:
        detection = _detect_largest(img)
    if detection is None:
        return None
    landmark = np.asarray(detection["landmarks_5"], dtype=np.float32)
    return face_align.norm_crop(img, landmark, image_size=ALIGN_SIZE)
