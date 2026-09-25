"""Reproduce official checkpoint -> CPU ONNX, then test upstream parity.

Run from E:/Factech with .venv-pad Python. No captures are used or downloaded.
"""

import collections.abc
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import onnx
import onnxruntime as ort
import torch

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "vendor/Silent-Face-Anti-Spoofing"
OUTPUT = ROOT / "engine/models/anti_spoof"
REVISION = "b6d5f04ad78778917853b25c778acef6d5626d15"
CHECKPOINTS = [
    (
        "2.7_80x80_MiniFASNetV2.pth",
        "a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0",
        2.7,
    ),
    (
        "4_0_0_80x80_MiniFASNetV1SE.pth",
        "84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990",
        4.0,
    ),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True
        ).strip()
        == REVISION
    )
    # The old upstream transforms refer to collections.Iterable on Python 3.12.
    collections.Iterable = collections.abc.Iterable
    sys.path[:0] = [str(UPSTREAM), str(ROOT / "engine")]
    from src.anti_spoof_predict import AntiSpoofPredict
    from src.data_io.transform import ToTensor
    from src.generate_patches import CropImage

    from app.anti_spoof import crop_bgr, tensor_of

    os.chdir(UPSTREAM)  # upstream detector uses relative paths
    torch.set_num_threads(1)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(UPSTREAM / "LICENSE", OUTPUT / "LICENSE")
    predictor = AntiSpoofPredict(0)
    predictor.device = torch.device("cpu")
    rng = np.random.default_rng(20260919)
    cases = [
        (rng.integers(0, 256, (320, 240, 3), dtype=np.uint8), box)
        for box in ([50, 60, 150, 220], [0, 0, 90, 130], [140, 150, 239, 319])
    ]
    for path in sorted((UPSTREAM / "images/sample").glob("*.jpg")):
        img = cv2.imread(str(path))
        x, y, w, h = predictor.get_bbox(img)
        cases.append((img, [x, y, x + w - 1, y + h - 1]))
    entries, reports = [], []
    for name, expected, scale in CHECKPOINTS:
        path = UPSTREAM / "resources/anti_spoof_models" / name
        assert sha(path) == expected
        predictor._load_model(str(path))
        model = torch.nn.Sequential(
            predictor.model.eval(), torch.nn.Softmax(dim=1)
        ).eval()
        output = OUTPUT / (path.stem + ".onnx")
        torch.onnx.export(
            model,
            torch.zeros((1, 3, 80, 80)),
            str(output),
            input_names=["bgr"],
            output_names=["scores"],
            opset_version=17,
            dynamo=False,
        )
        onnx.checker.check_model(onnx.load(output))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        session = ort.InferenceSession(
            str(output), sess_options=options, providers=["CPUExecutionProvider"]
        )
        errors = []
        for img, box in cases:
            x1, y1, x2, y2 = box
            bbox = [int(x1), int(y1), int(x2 - x1 + 1), int(y2 - y1 + 1)]
            upstream_patch = CropImage().crop(img, bbox, scale, 80, 80)
            local_patch = crop_bgr(img, box, scale)
            np.testing.assert_array_equal(local_patch, upstream_patch)
            np.testing.assert_array_equal(
                tensor_of(local_patch), ToTensor()(upstream_patch).unsqueeze(0).numpy()
            )
            reference = predictor.predict(upstream_patch, str(path))
            actual = session.run(None, {"bgr": tensor_of(local_patch)})[0]
            np.testing.assert_allclose(actual, reference, rtol=1e-4, atol=1e-5)
            assert actual.argmax() == reference.argmax()
            errors.append(float(np.max(np.abs(actual - reference))))
        entries.append(
            {
                "checkpoint": name,
                "checkpoint_sha256": expected,
                "onnx": output.name,
                "onnx_sha256": sha(output),
                "scale": scale,
            }
        )
        reports.append(
            {
                "model": name,
                "cases": len(cases),
                "max_abs_error": max(errors),
                "identical_crops_and_tensors": True,
                "same_argmax": True,
            }
        )
    manifest = {
        "repository": "https://github.com/minivision-ai/Silent-Face-Anti-Spoofing",
        "revision": REVISION,
        "license": "Apache-2.0 (repository license; no separate checkpoint license found)",
        "classes": {
            "0": "attack (subtype unspecified)",
            "1": "live",
            "2": "attack (subtype unspecified)",
        },
        "input": "BGR float32 NCHW 1x3x80x80, values 0..255, no normalization",
        "ensemble": "arithmetic mean of per-model softmax, live iff argmax is 1",
        "models": entries,
        "conversion": {
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "numpy": np.__version__,
            "opencv": cv2.__version__,
            "opset": 17,
        },
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    evidence = ROOT / "pad-evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "parity.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(json.dumps({"manifest": manifest, "parity": reports}, indent=2))


if __name__ == "__main__":
    main()
