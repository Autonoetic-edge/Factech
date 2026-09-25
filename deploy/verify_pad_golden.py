import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort

root = Path("/app/models/anti_spoof")
manifest = json.loads((root / "manifest.json").read_text())
golden = json.loads(Path("/tmp/upstream-golden.json").read_text())
patch = (np.arange(80*80*3).reshape(80, 80, 3) % 256).astype(np.uint8)
tensor = np.ascontiguousarray(patch.transpose(2, 0, 1)[None], dtype=np.float32)
for model in manifest["models"]:
    path = root / model["onnx"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == model["onnx_sha256"]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    actual = session.run(None, {"bgr": tensor})[0]
    reference = np.asarray(golden[path.name])
    np.testing.assert_allclose(actual, reference, rtol=1e-4, atol=1e-5)
    assert actual.argmax() == reference.argmax()
    print(path.name, "checksum verified; upstream CPU parity max error", float(np.abs(actual-reference).max()))
