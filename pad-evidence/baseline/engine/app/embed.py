import threading
from pathlib import Path

import numpy as np

from app.vendor.arcface_onnx import ArcFaceONNX

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
EMB_MODEL_FILE = MODEL_DIR / "w600k_r50.onnx"
EMBED_DIM = 512

_embedder = None
_embedder_lock = threading.Lock()


def get_embedder() -> ArcFaceONNX:
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                if not EMB_MODEL_FILE.is_file():
                    raise FileNotFoundError(
                        f"embedding model missing: {EMB_MODEL_FILE} "
                        "(run engine/scripts/download_models.py)"
                    )
                embedder = ArcFaceONNX(model_file=str(EMB_MODEL_FILE))
                embedder.prepare(-1)
                _embedder = embedder
    return _embedder


def embed_aligned(aligned: np.ndarray) -> np.ndarray:
    embedder = get_embedder()
    feat = embedder.get_feat(aligned).flatten().astype(np.float32)
    norm = float(np.linalg.norm(feat))
    if norm == 0.0:
        raise ValueError("embedding model produced a zero vector")
    return feat / norm


def is_loaded() -> bool:
    return _embedder is not None


def cosine_similarity(a, b) -> float:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return max(-1.0, min(1.0, float(np.dot(a, b) / denom)))
