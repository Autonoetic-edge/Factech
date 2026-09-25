"""FIX_PLAN 2A.1: one engine constants module, and every listed copy reads from it."""

import re
from pathlib import Path

from app import (
    anti_spoof,
    challenge,
    constants,
    facescan,
    head_sequence,
    liveness,
    main,
)

ROOT = Path(__file__).resolve().parents[2]


def test_constants_restate_the_frozen_numbers():
    assert constants.FRAME_COUNT == 12
    assert constants.FRAME_INTERVAL_MS == 1400
    assert constants.CAPTURE_SPAN_MS == 15400
    assert constants.CAPTURE_SPAN_MS == (
        (constants.FRAME_COUNT - 1) * constants.FRAME_INTERVAL_MS
    )
    assert constants.MAX_SCAN_BYTES == 350 * 1024
    assert constants.MAX_B64_CHARS == 4 * ((350 * 1024 + 2) // 3)
    assert constants.MAX_GAP_MS == 3800
    assert constants.SINGLE_MAX_GAP_MS == 1400
    assert constants.MATCH_THRESHOLD == 0.55
    assert constants.LIVENESS_THRESHOLD == 0.50
    assert constants.CHALLENGE_EXPIRY_MS == 30_000


def test_engine_modules_share_the_constants():
    assert facescan.MAX_BYTES == constants.MAX_SCAN_BYTES
    assert facescan.MAX_B64_CHARS == constants.MAX_B64_CHARS
    assert facescan.FRAME_COUNT == constants.FRAME_COUNT
    assert anti_spoof.MAX_GAP_MS == constants.MAX_GAP_MS
    assert anti_spoof.SINGLE_MAX_GAP_MS == constants.SINGLE_MAX_GAP_MS
    assert challenge.EXPIRY_MS == constants.CHALLENGE_EXPIRY_MS
    assert main.PLACEHOLDER_THRESHOLD == constants.MATCH_THRESHOLD
    assert liveness.LIVENESS_THRESHOLD == constants.LIVENESS_THRESHOLD
    assert head_sequence.INTERVAL_MS == constants.FRAME_INTERVAL_MS


def _source(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_listed_literals_are_gone():
    """The places FIX_PLAN 2A.1 names read the constant instead of restating it."""
    assert not re.search(r"^MAX_BYTES = 350", _source("engine/app/facescan.py"), re.M)
    pad = _source("engine/app/anti_spoof.py")
    assert "len(frames) != 12" not in pad
    assert not re.search(r"^(SINGLE_)?MAX_GAP_MS = \d", pad, re.M)
    assert not re.search(r"^EXPIRY_MS = \d", _source("engine/app/challenge.py"), re.M)
    assert not re.search(
        r"^PLACEHOLDER_THRESHOLD = \d", _source("engine/app/main.py"), re.M
    )
    auth = "packages/face-auth/src/facetech_auth/"
    assert "350 * 1024" not in _source(auth + "http.py")
    assert "30000" not in _source(auth + "operations.py")
    assert "350 * 1024" not in _source("engine/tests/test_app_encoder.py")
