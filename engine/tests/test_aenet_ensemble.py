"""PAD_ENSEMBLE AENet beside MiniFASNet (docs/LIVENESS_UPGRADE_PLAN.md Phase 3).

AENet is NON-COMMERCIAL, demo only. It is log only: it writes a trace record and never
decides. The default `minifas` never loads or runs it and leaves the trace unchanged.
"""

import hashlib
import json

import numpy as np
import onnx
import pytest
from fastapi.testclient import TestClient
from onnx import TensorProto, helper

from app import aenet, challenge, main, store, trace
from helpers import AUTH
from test_nose_geometry import keypoints, verdict
from test_secure_pad import vector
from test_single_turn import look_scan, post_verify, wire

SIZE = 32


@pytest.fixture(autouse=True)
def fresh_model(monkeypatch):
    monkeypatch.setattr(aenet, "_model", None)
    monkeypatch.setattr(aenet, "_failure", None)


class FakeAENet:
    scale, size, version = 1.0, SIZE, "AENet-test"

    def __init__(self, live=0.9):
        self.live = live
        self.seen = []

    def predict(self, patch):
        assert patch.shape == (SIZE, SIZE, 3)
        self.seen.append(patch)
        return self.live


def face(yaw):
    det = keypoints(yaw)
    det["bbox"] = [200.0, 150.0, 440.0, 450.0]
    return det


def image_of(index):
    """Noise whose strength rises with the index: a higher index is a sharper frame."""
    rng = np.random.default_rng(index)
    base = np.full((480, 640, 3), 128, np.float64)
    return np.clip(base + rng.normal(0, 2 + 4 * index, base.shape), 0, 255).astype(
        np.uint8
    )


def scan(yaws):
    frames = [{"ts_ms": i * 500, "jpeg_bytes": i} for i in range(len(yaws))]
    return frames, [face(y) for y in yaws]


# Settle 0-1500 ms = frames 0-3, then a turn.
YAWS = [0.02, 0.0, 0.01, 0.03] + [0.1, 0.2, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3]


@pytest.mark.parametrize(
    "value, mode",
    [
        (None, aenet.MINIFAS),
        ("", aenet.MINIFAS),
        ("minifas", aenet.MINIFAS),
        ("aenet", aenet.MINIFAS),
        ("minifas+aenet:log", aenet.LOG),
        (" MINIFAS+AENET ", aenet.ON),
    ],
)
def test_mode_defaults_to_minifas(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv(aenet.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(aenet.MODE_ENV, value)
    assert aenet.mode() == mode


def test_frontal_pool_keeps_settle_frames_most_frontal_first():
    frames, dets = scan(YAWS)
    ts = [f["ts_ms"] for f in frames]
    pool = aenet.frontal_pool(dets, ts, 1500)
    assert [i for i, _ in pool] == [1, 2, 0, 3]
    dets[1] = None
    assert [i for i, _ in aenet.frontal_pool(dets, ts, 1500)] == [2, 0, 3]
    assert [i for i, _ in aenet.frontal_pool(dets, ts, None)] == [2, 0, 3]
    assert aenet.frontal_pool(dets, ts[:3], 1500) == []


def test_scores_three_to_five_sharp_frontal_settle_frames(monkeypatch):
    model = FakeAENet(live=0.25)
    monkeypatch.setattr(aenet, "_model", model)
    yaws = [0.0] * 8 + [0.3] * 4
    frames = [{"ts_ms": i * 250, "jpeg_bytes": i} for i in range(12)]
    dets = [face(y) for y in yaws]
    record = aenet.score_scan(frames, dets, 1750, image_of)
    assert record["outcome"] == "scored" and record["model"] == "AENet-test"
    # Settle = frames 0-7 (all frontal); the 5 sharpest are the last five.
    assert [f["index"] for f in record["frames"]] == [3, 4, 5, 6, 7]
    assert record["median_live"] == 0.25
    assert record["latency_ms"] >= 0 and record["over_budget"] in (True, False)
    json.dumps(record)


def test_too_few_frames_is_inconclusive_not_a_failure(monkeypatch):
    monkeypatch.setattr(aenet, "_model", FakeAENet())
    frames, dets = scan(YAWS)
    dets[0] = dets[1] = None
    record = aenet.score_scan(frames, dets, 1500, image_of)
    assert record["outcome"] == "inconclusive" and record["reason"] == "settle_frames"
    record = aenet.score_scan(frames, scan(YAWS)[1], 1500, lambda b: None)
    assert record["outcome"] == "inconclusive"


def test_missing_model_is_unavailable_and_remembered(monkeypatch, tmp_path):
    monkeypatch.setattr(aenet, "MODEL_DIR", tmp_path)
    frames, dets = scan(YAWS)
    record = aenet.score_scan(frames, dets, 1500, image_of)
    assert record["outcome"] == "unavailable" and record["reason"] == "model_missing"
    assert record["median_live"] is None
    write_model(tmp_path)
    assert aenet.get_model() is None  # remembered until restart


def write_model(directory, *, sha=None, live_index=0):
    """Tiny stand-in: mean colour -> 2-class softmax (a real AENet is ResNet-sized)."""
    graph = helper.make_graph(
        [
            helper.make_node("GlobalAveragePool", ["input"], ["pooled"]),
            helper.make_node("Flatten", ["pooled"], ["flat"]),
            helper.make_node("MatMul", ["flat", "w"], ["logits"]),
            helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
        ],
        "aenet_stub",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, SIZE, SIZE])],
        [helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [1, 2])],
        [helper.make_tensor("w", TensorProto.FLOAT, [3, 2], [4, -4, 0, 0, 0, 0])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    path = directory / "AENet.onnx"
    onnx.save(model, str(path))
    manifest = {
        "license": aenet.LICENSE,
        "model_version": "AENet-stub",
        "onnx": path.name,
        "onnx_sha256": sha or hashlib.sha256(path.read_bytes()).hexdigest(),
        "preprocess": {
            "size": SIZE,
            "crop_scale": 1.2,
            "channels": "RGB",
            "divide": 255.0,
            "mean": [0.5, 0.5, 0.5],
            "std": [0.5, 0.5, 0.5],
            "live_index": live_index,
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))


def test_onnx_model_loads_checks_its_hash_and_scores(tmp_path):
    write_model(tmp_path)
    model = aenet.AENetPAD(tmp_path)
    red = np.zeros((SIZE, SIZE, 3), np.uint8)
    red[..., 2] = 255  # BGR red; RGB channel 0 drives the live logit
    blue = np.zeros((SIZE, SIZE, 3), np.uint8)
    blue[..., 0] = 255
    assert model.predict(red) > 0.99 and model.predict(blue) < 0.01
    write_model(tmp_path, live_index=1)
    assert aenet.AENetPAD(tmp_path).predict(red) < 0.01
    write_model(tmp_path, sha="0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        aenet.AENetPAD(tmp_path)


def test_crop_stays_inside_the_frame():
    image = image_of(1)
    assert aenet.crop(image, [0, 0, 100, 120], 2.0, SIZE).shape == (SIZE, SIZE, 3)
    assert aenet.crop(image, [500, 350, 638, 478], 3.0, SIZE).shape == (SIZE, SIZE, 3)
    with pytest.raises(ValueError, match="face box"):
        aenet.crop(image, [600, 400, 700, 500], 1.0, SIZE)


def test_minifas_median_is_logged_beside_aenet():
    pad = {
        "outcome": "live",
        "frames": [
            {"index": 0, "scores": [0.1, 0.8, 0.1]},
            {"index": 1, "outcome": "no_face"},
            {"index": 2, "scores": [0.0, 0.9, 0.1]},
            {"index": 3, "scores": [0.3, 0.6, 0.1]},
        ],
    }
    assert aenet.minifas_summary(pad) == {
        "outcome": "live",
        "frames": 3,
        "median_live": 0.8,
    }
    assert aenet.minifas_summary({"outcome": "inference_error"})["median_live"] is None


def test_log_ensemble_writes_the_trace_only_when_on(monkeypatch):
    frames, dets = scan(YAWS)
    monkeypatch.setattr(main, "decode_jpeg", image_of)
    pad = {"outcome": "live", "frames": [{"index": 0, "scores": [0.1, 0.8, 0.1]}]}
    t, token = trace.start("verify", "req-aenet-1")
    try:
        monkeypatch.delenv(aenet.MODE_ENV, raising=False)
        monkeypatch.setattr(aenet, "get_model", lambda: pytest.fail("loaded while off"))
        main._log_ensemble(frames, dets, verdict(), pad)
        assert "ensemble" not in t.line(200) and "aenet" not in t.timing_ms

        monkeypatch.setattr(aenet, "get_model", lambda: FakeAENet(live=0.1))
        for mode in (aenet.LOG, aenet.ON):
            monkeypatch.setenv(aenet.MODE_ENV, mode)
            main._log_ensemble(frames, dets, verdict(), pad)
            line = t.line(200)
            assert line["ensemble"]["mode"] == mode
            assert line["ensemble"]["enforced"] is False
            assert "non-commercial" in line["ensemble"]["license"]
            assert line["ensemble"]["minifas"]["median_live"] == 0.8
            assert line["ensemble"]["aenet"]["outcome"] == "scored"
            assert line["ensemble"]["aenet"]["median_live"] == 0.1
            assert "aenet" in line["timing_ms"]
            json.dumps(line)

        monkeypatch.setattr(aenet, "score_scan", lambda *a: 1 / 0)
        main._log_ensemble(frames, dets, verdict(), pad)
        assert t.line(200)["ensemble"]["aenet"] == {
            "outcome": "error",
            "reason": "ZeroDivisionError",
        }
    finally:
        trace.reset(token)


def test_a_spoof_score_from_aenet_never_changes_the_response(monkeypatch):
    """Flag off, log and `on` give the same response; AENet saying 'spoof' rejects nobody."""
    store.reset()
    store.enroll("test", vector())
    wire(monkeypatch, [])
    monkeypatch.setenv(challenge.POLICY_ENV, challenge.POLICY_SINGLE_TURN_V1)
    model = FakeAENet(live=0.0)
    monkeypatch.setattr(aenet, "_model", model)
    lines = []
    monkeypatch.setattr(
        trace.Trace, "emit", lambda self, status: lines.append(self.line(status))
    )
    bodies = []
    with TestClient(main.app, headers=AUTH) as client:
        for mode in (None, aenet.LOG, aenet.ON):
            if mode is None:
                monkeypatch.delenv(aenet.MODE_ENV, raising=False)
            else:
                monkeypatch.setenv(aenet.MODE_ENV, mode)
            response = post_verify(client, look_scan(challenge.LOOK_LEFT))
            assert response.status_code == 200, response.text
            bodies.append(
                {k: v for k, v in response.json().items() if k != "request_id"}
            )
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["match"] is True
    assert "ensemble" not in lines[0]
    assert [line["ensemble"]["mode"] for line in lines[1:]] == [aenet.LOG, aenet.ON]
