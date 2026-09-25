import os
from pathlib import Path

import pytest

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
DET_MODEL = MODELS_DIR / "det_500m.onnx"
EMB_MODEL = MODELS_DIR / "w600k_r50.onnx"

STRICT = os.environ.get("FACETECH_REQUIRE_MODELS") == "1"

# Policy migration replaced 34 heuristic-only endpoint tests; 10 retained
# detector/recognizer tests plus 2 explicit real-PAD tests must run.
MIN_MODEL_TESTS = 12

_FIX = "run engine/scripts/download_models.py (or restore the CI models cache)"


def missing() -> list[str]:
    return [m.name for m in (DET_MODEL, EMB_MODEL) if not m.is_file()]


requires_models = pytest.mark.models(det=True, emb=True)
requires_det_model = pytest.mark.models(det=True, emb=False)


def resolve(marker: pytest.Mark) -> list[str]:
    need_det = marker.kwargs.get("det", True)
    need_emb = marker.kwargs.get("emb", True)
    absent = []
    if need_det and not DET_MODEL.is_file():
        absent.append(DET_MODEL.name)
    if need_emb and not EMB_MODEL.is_file():
        absent.append(EMB_MODEL.name)
    return absent


def unavailable_message(absent: list[str]) -> str:
    return f"model(s) not available: {', '.join(absent)} - {_FIX}"
