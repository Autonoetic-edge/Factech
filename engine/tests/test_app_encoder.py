import base64
import json
import shutil
import subprocess
from pathlib import Path

import msgpack
import pytest

from app.facescan import parse_facescan
from helpers import make_jpeg

TESTS_DIR = Path(__file__).parent
HARNESS = TESTS_DIR / "app_encoder_run.js"
GOLDEN = TESTS_DIR / "fixtures" / "app_encoder_golden.txt"
NODE = shutil.which("node")

DEVICE = {
    "user_agent": "amfatec-app",
    "screen": {"w": 400, "h": 800},
    "tz_offset": -420,
}
CHALLENGE_ID = "ch-app-1"
SHADES = [120 + i for i in range(12)]
TS_MS = [1000 + i * 33 for i in range(12)]


def reference_scan() -> dict:
    return {
        "version": 1,
        "device": {
            "user_agent": DEVICE["user_agent"],
            "screen": {"w": DEVICE["screen"]["w"], "h": DEVICE["screen"]["h"]},
            "tz_offset": DEVICE["tz_offset"],
        },
        "frames": [
            {"jpeg_bytes": make_jpeg(64, shade), "ts_ms": ts, "pose": None}
            for shade, ts in zip(SHADES, TS_MS)
        ],
        "challenge": {"id": CHALLENGE_ID, "results": []},
    }


def harness_input() -> dict:
    return {
        "device": DEVICE,
        "challenge_id": CHALLENGE_ID,
        "frames": [
            {"jpeg_b64": base64.b64encode(make_jpeg(64, shade)).decode(), "ts_ms": ts}
            for shade, ts in zip(SHADES, TS_MS)
        ],
    }


def run_harness(tmp_path: Path) -> str:
    inp = tmp_path / "encoder_input.json"
    inp.write_text(json.dumps(harness_input()))
    proc = subprocess.run(
        [NODE, str(HARNESS), str(inp)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout.strip()


def test_golden_fixture_parses():
    scan = parse_facescan(GOLDEN.read_text().strip())
    assert scan["version"] == 1
    assert len(scan["frames"]) == 12
    assert scan["device"]["user_agent"] == "amfatec-app"
    assert scan["challenge"]["id"] == "ch-app-1"
    assert scan["frames"][0]["ts_ms"] == TS_MS[0]
    assert scan["frames"][0]["jpeg_bytes"].startswith(b"\xff\xd8")


def test_golden_fixture_matches_reference_bytes():
    raw = base64.b64decode(GOLDEN.read_text().strip())
    assert raw == msgpack.packb(reference_scan(), use_bin_type=True)
    assert len(raw) < 350 * 1024


@pytest.mark.skipif(NODE is None, reason="node not found on PATH")
def test_app_sdk_block_matches_python_msgpack(tmp_path):
    b64 = run_harness(tmp_path)
    assert base64.b64decode(b64) == msgpack.packb(reference_scan(), use_bin_type=True)
    scan = parse_facescan(b64)
    assert scan["version"] == 1 and len(scan["frames"]) == 12


@pytest.mark.skipif(NODE is None, reason="node not found on PATH")
def test_app_sdk_golden_is_current(tmp_path):
    assert run_harness(tmp_path) == GOLDEN.read_text().strip()
