from pathlib import Path

import numpy as np
import pytest

from app.detect import (
    align_largest_face,
    decode_jpeg,
    detect_largest_face,
)
from model_guard import requires_det_model

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_decode_jpeg_valid():
    img = decode_jpeg(fixture_bytes("face_like.jpg"))
    assert img is not None
    assert img.shape == (480, 640, 3)


def test_decode_jpeg_corrupt():
    assert decode_jpeg(fixture_bytes("corrupt.jpg")) is None
    assert decode_jpeg(b"") is None


def test_detect_corrupt_jpeg_returns_none():
    assert detect_largest_face(fixture_bytes("corrupt.jpg")) is None
    assert detect_largest_face(b"") is None


@requires_det_model
def test_detect_blank_returns_none():
    assert detect_largest_face(fixture_bytes("blank.jpg")) is None


@requires_det_model
def test_detect_fixture_returns_face(low_det_thresh):
    det = detect_largest_face(fixture_bytes("face_like.jpg"))
    assert det is not None
    assert len(det["bbox"]) == 4
    assert len(det["landmarks_5"]) == 5
    assert all(len(kp) == 2 for kp in det["landmarks_5"])
    assert 0.2 <= det["det_score"] <= 1.0

    x1, y1, x2, y2 = det["bbox"]
    assert 0 <= x1 < x2 <= 640 and 0 <= y1 < y2 <= 480


@requires_det_model
def test_align_output_is_112x112x3(low_det_thresh):
    aligned = align_largest_face(fixture_bytes("face_like.jpg"))
    assert aligned is not None
    assert aligned.shape == (112, 112, 3)
    assert aligned.dtype == np.uint8
