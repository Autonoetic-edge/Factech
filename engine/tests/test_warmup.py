import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import detect, embed
from app.main import app, warm_models
from model_guard import requires_models

THIS_FILE = Path(__file__)


class _CountingModel:
    count = 0
    lock = threading.Lock()

    def __init__(self, model_file: str):
        with _CountingModel.lock:
            _CountingModel.count += 1
        time.sleep(0.05)

    def prepare(self, ctx_id):
        pass


@pytest.fixture(autouse=True)
def _reset_counter():
    _CountingModel.count = 0
    yield


def _hammer(fn, threads: int = 16) -> list:
    results: list = [None] * threads
    start = threading.Barrier(threads)

    def worker(i):
        start.wait()
        results[i] = fn()

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return results


def test_detector_constructed_once_under_concurrency(monkeypatch):
    monkeypatch.setattr(detect, "_detector", None)
    monkeypatch.setattr(detect, "DET_MODEL_FILE", THIS_FILE)
    monkeypatch.setattr(detect, "SCRFD", _CountingModel)

    results = _hammer(detect.get_detector)

    assert _CountingModel.count == 1, "lock failed: detector built more than once"
    assert all(r is results[0] for r in results)
    assert detect.is_loaded()


def test_embedder_constructed_once_under_concurrency(monkeypatch):
    monkeypatch.setattr(embed, "_embedder", None)
    monkeypatch.setattr(embed, "EMB_MODEL_FILE", THIS_FILE)
    monkeypatch.setattr(embed, "ArcFaceONNX", _CountingModel)

    results = _hammer(embed.get_embedder)

    assert _CountingModel.count == 1, "lock failed: embedder built more than once"
    assert all(r is results[0] for r in results)
    assert embed.is_loaded()


def test_missing_model_file_still_raises_under_concurrency(monkeypatch):
    monkeypatch.setattr(detect, "_detector", None)
    monkeypatch.setattr(detect, "DET_MODEL_FILE", THIS_FILE.parent / "nope.onnx")

    with pytest.raises(FileNotFoundError):
        detect.get_detector()
    assert not detect.is_loaded()


def test_secure_startup_refuses_missing_detector(monkeypatch):
    monkeypatch.setattr(detect, "_detector", None)
    monkeypatch.setattr(detect, "DET_MODEL_FILE", THIS_FILE.parent / "nope.onnx")
    with pytest.raises(FileNotFoundError):
        warm_models()
    with pytest.raises(FileNotFoundError), TestClient(app):
        pass


@requires_models
def test_warm_models_loads_both_when_present():
    assert warm_models() == {"detector": True, "embedder": True, "pad": True}
    assert detect.is_loaded() and embed.is_loaded()


@requires_models
def test_startup_hook_warms_both_singletons(monkeypatch):
    monkeypatch.setattr(detect, "_detector", None)
    monkeypatch.setattr(embed, "_embedder", None)
    assert not detect.is_loaded() and not embed.is_loaded()

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert detect.is_loaded(), "startup hook did not warm the detector"
        assert embed.is_loaded(), "startup hook did not warm the embedder"
