"""FIX_PLAN 2A.2: the SDK's constants.ts and the engine's constants.py agree."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SDK_CONSTANTS = REPO / "packages" / "face-sdk" / "src" / "constants.ts"
NODE = shutil.which("node")

sys.path.insert(0, str(REPO / "engine"))
from app import constants as engine  # noqa: E402

# SDK name -> engine name, for every value both sides must hold.
SHARED = {
    "FRAME_COUNT": "FRAME_COUNT",
    "HEAD_SEQUENCE_INTERVAL_MS": "FRAME_INTERVAL_MS",
    "HEAD_SEQUENCE_SPAN_MS": "CAPTURE_SPAN_MS",
    "ENGINE_MAX_BYTES": "MAX_SCAN_BYTES",
    "DEFAULT_CHALLENGE_LIFE_MS": "CHALLENGE_EXPIRY_MS",
}


@pytest.fixture(scope="module")
def sdk():
    if NODE is None:
        pytest.skip("node not found on PATH")
    script = (
        f"const m = await import({json.dumps(SDK_CONSTANTS.as_uri())});"
        "console.log(JSON.stringify(m));"
    )
    out = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("sdk_name,engine_name", sorted(SHARED.items()))
def test_sdk_and_engine_share_the_value(sdk, sdk_name, engine_name):
    assert sdk_name in sdk, f"constants.ts does not export {sdk_name}"
    assert sdk[sdk_name] == getattr(engine, engine_name), sdk_name


def test_page_limits_live_in_the_sdk(sdk):
    assert sdk["LUMA_MIN"] == 55
    assert sdk["SHARP_MIN"] == 12
    assert sdk["CAMERA_CONSTRAINTS"] == {
        "video": {"width": {"ideal": 640}, "height": {"ideal": 480}, "facingMode": "user"},
        "audio": False,
    }
