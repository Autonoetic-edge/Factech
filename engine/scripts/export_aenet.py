"""CelebA-Spoof AENet checkpoint -> CPU ONNX for PAD_ENSEMBLE (LIVENESS_UPGRADE_PLAN Phase 3).

NON-COMMERCIAL, DEMO ONLY. The AENet weights are licensed for non-commercial research
only. The output is for the demo engine; do not ship it in a product.

Offline only: PyTorch is needed here, never at runtime. Run from E:/Factech with
.venv-pad Python (PyTorch installed as for convert_pad.py):

    git clone https://github.com/Davidzhangyuanhan/CelebA-Spoof vendor/CelebA-Spoof
    .venv-pad\\Scripts\\python.exe engine\\scripts\\export_aenet.py ^
        --upstream vendor/CelebA-Spoof/intra_dataset_code ^
        --checkpoint vendor/CelebA-Spoof/intra_dataset_code/ckpt_iter.pth.tar

Writes engine/models/anti_spoof/aenet/{AENet.onnx,manifest.json}. The engine checks the
ONNX sha256 against that manifest at load. Only the 2-class live/spoof head is exported,
with softmax, so the model returns (1, 2) probabilities.

VERIFY BEFORE USE: the preprocessing defaults below (crop scale, channel order, scaling,
mean/std, live index) were written without the upstream source at hand. Check them
against upstream's tsn_predict.py / client.py at the pinned revision and pass the
matching flags; the engine reads them from the manifest, so no code change is needed.
"""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "engine/models/anti_spoof/aenet"
LICENSE = (
    "CelebA-Spoof AENet: non-commercial research only; demo only, not for a product"
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LiveHead(torch.nn.Module):
    """AENet returns several heads; keep the single (N, 2) live/spoof one, as softmax."""

    def __init__(self, net):
        super().__init__()
        self.net = net

    def forward(self, x):
        outputs = self.net(x)
        if not isinstance(outputs, (tuple, list)):
            outputs = (outputs,)
        heads = [o for o in outputs if o.dim() == 2 and o.shape[1] == 2]
        if len(heads) != 1:
            raise ValueError(f"expected one (N, 2) head, found {len(heads)}")
        return torch.softmax(heads[0], dim=1)


def load_net(upstream, checkpoint):
    spec = importlib.util.spec_from_file_location(
        "aenet_upstream", upstream / "models/AENet.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(upstream))
    spec.loader.exec_module(module)
    net = module.AENet(num_classes=2)
    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    state = state.get("state_dict", state)
    net.load_state_dict({k.removeprefix("module."): v for k, v in state.items()})
    return LiveHead(net.eval()).eval()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument("--crop-scale", type=float, default=1.0)
    parser.add_argument("--channels", choices=["BGR", "RGB"], default="BGR")
    parser.add_argument("--divide", type=float, default=255.0)
    parser.add_argument("--mean", type=float, nargs=3, default=[0.0, 0.0, 0.0])
    parser.add_argument("--std", type=float, nargs=3, default=[1.0, 1.0, 1.0])
    parser.add_argument("--live-index", type=int, choices=[0, 1], default=0)
    args = parser.parse_args()

    net = load_net(args.upstream, args.checkpoint)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    onnx_path = OUTPUT / "AENet.onnx"
    sample = torch.rand(1, 3, args.size, args.size)
    torch.onnx.export(
        net,
        sample,
        str(onnx_path),
        opset_version=17,
        input_names=["input"],
        output_names=["probabilities"],
        dynamo=False,
    )
    onnx.checker.check_model(str(onnx_path))

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(4):
        x = rng.random((1, 3, args.size, args.size), dtype=np.float32)
        with torch.no_grad():
            expected = net(torch.from_numpy(x)).numpy()
        worst = max(
            worst, float(np.abs(session.run(None, {"input": x})[0] - expected).max())
        )
    if worst > 1e-4:
        raise SystemExit(f"ONNX/PyTorch parity failed: max abs diff {worst}")
    x = rng.random((1, 3, args.size, args.size), dtype=np.float32)
    runs = []
    for _ in range(10):
        t0 = time.perf_counter()
        session.run(None, {"input": x})
        runs.append((time.perf_counter() - t0) * 1000)

    try:
        revision = subprocess.check_output(
            ["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    manifest = {
        "license": LICENSE,
        "repository": "https://github.com/Davidzhangyuanhan/CelebA-Spoof",
        "revision": revision,
        "checkpoint": args.checkpoint.name,
        "checkpoint_sha256": sha(args.checkpoint),
        "model_version": f"AENet-{(revision or 'unknown')[:12]}",
        "onnx": onnx_path.name,
        "onnx_sha256": sha(onnx_path),
        "output": "softmax over the 2-class live/spoof head, shape (1, 2)",
        "preprocess": {
            "size": args.size,
            "crop_scale": args.crop_scale,
            "channels": args.channels,
            "divide": args.divide,
            "mean": args.mean,
            "std": args.std,
            "live_index": args.live_index,
        },
        "parity_max_abs_diff": worst,
        "local_median_run_ms": round(float(np.median(runs)), 1),
        "conversion": {
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "numpy": np.__version__,
            "opset": 17,
        },
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    print(
        "NON-COMMERCIAL, DEMO ONLY. Measure latency on the VPS CPU before relying on it."
    )


if __name__ == "__main__":
    main()
