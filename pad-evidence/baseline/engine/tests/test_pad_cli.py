import json
import subprocess
import sys
from pathlib import Path

import pytest

import model_guard
from helpers import live_scan, replayed_scan, scan_to_msgpack

ENGINE = Path(__file__).resolve().parents[1]
PAD = ENGINE / "eval" / "pad.py"


def _dataset(root: Path) -> Path:
    data = root / "data"
    (data / "bona_fide").mkdir(parents=True)
    (data / "print").mkdir()
    for i in range(2):
        (data / "bona_fide" / f"live_{i}.msgpack").write_bytes(
            scan_to_msgpack(live_scan("face_synth.jpg", seed=i + 1))
        )
    (data / "print" / "replay_0.msgpack").write_bytes(
        scan_to_msgpack(replayed_scan("face_synth.jpg"))
    )
    return data


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(PAD),
            "--data",
            str(_dataset(tmp_path)),
            "--out",
            str(tmp_path / "out"),
            *extra,
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )


@model_guard.requires_det_model
def test_a_fresh_process_loads_the_pinned_detector_and_scores_the_corpus(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    report = json.loads((tmp_path / "out" / "pad.json").read_text(encoding="utf-8"))
    assert report["corpus"]["bona_fide"]["scans"] == 2
    assert report["corpus"]["print"]["scans"] == 1
    scored = [s for s in report["scans"]["bona_fide"] if "error" not in s]
    assert scored, "the detector found the synthetic face, so the pipeline really ran"
    assert (tmp_path / "out" / "pad_report.md").is_file()


@pytest.mark.parametrize("kind", ["missing", "garbage", "truncated"])
def test_an_absent_or_corrupt_detector_fails_clearly_and_writes_no_report(
    tmp_path, kind
):
    model = tmp_path / "det_500m.onnx"
    if kind == "garbage":
        model.write_bytes(b"not an onnx model" * 64)
    elif kind == "truncated":
        if not model_guard.DET_MODEL.is_file():
            pytest.skip("needs the pinned detector to truncate")
        model.write_bytes(model_guard.DET_MODEL.read_bytes()[:4096])
    r = _run(tmp_path, "--detector-model", str(model))
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    expected = (
        "detection model missing"
        if kind == "missing"
        else "does not match the pinned sha256"
    )
    assert expected in r.stderr
    assert not (tmp_path / "out").exists(), "no empty or misleading report is written"
