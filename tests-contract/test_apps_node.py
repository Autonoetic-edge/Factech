import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_node_suite_passes():
    result = subprocess.run(
        [NODE, "--test", "apps/tests/*.test.js"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    tail = (result.stdout + result.stderr)[-4000:]
    assert result.returncode == 0, tail
    assert "# fail 0" in result.stdout or "ℹ fail 0" in result.stdout, tail
