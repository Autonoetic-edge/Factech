import base64
import json
import logging
import logging.handlers

import pytest
from fastapi.testclient import TestClient

from app import store, trace
from app.challenge import MOVE_CLOSER
from app.liveness import LIVENESS_THRESHOLD
from app.main import PLACEHOLDER_THRESHOLD, app
from helpers import (
    AUTH,
    TEST_API_KEY,
    build_scan,
    live_scan,
    replayed_scan,
    scan_to_b64,
    scan_to_msgpack,
)
from model_guard import requires_models

client = TestClient(app, headers=AUTH)

USER = "trace-subject-7f3a"
SIGNALS = {"duplicates", "motion", "landmarks", "timing", "challenge", "depth", "moire"}
ADVISORY = {"depth", "moire"}


@pytest.fixture(autouse=True)
def _clean_state():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger=trace.LOGGER_NAME)

    def read() -> list[dict]:
        return [
            json.loads(r.getMessage())
            for r in caplog.records
            if r.name == trace.LOGGER_NAME
        ]

    read.text = lambda: "\n".join(
        r.getMessage() for r in caplog.records if r.name == trace.LOGGER_NAME
    )
    return read


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


def _walk(value, path="$"):
    yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def assert_no_biometrics(line: dict, text: str, scans: list[dict]) -> None:
    for path, value in _walk(line):
        if isinstance(value, list):
            assert len(value) <= len(SIGNALS), path
            assert all(isinstance(v, str) for v in value), path
        if isinstance(value, str):
            assert len(value) <= trace.MAX_REASON_CHARS, path
        assert not isinstance(value, (bytes, bytearray)), path
    assert USER not in text
    assert TEST_API_KEY not in text
    for scan in scans:
        body_b64 = scan_to_b64(scan)
        assert body_b64[:48] not in text
        for frame in scan["frames"]:
            jpeg = frame["jpeg_bytes"]
            assert base64.b64encode(jpeg).decode()[:32] not in text
            assert jpeg[:16].hex() not in text
    assert "\\xff\\xd8" not in text and "/9j/" not in text


def _only(lines, endpoint: str) -> dict:
    found = [ln for ln in lines() if ln["endpoint"] == endpoint]
    assert len(found) == 1, found
    return found[0]


@requires_models
def test_enroll_and_verify_lines_carry_every_field(lines, low_det_thresh, monkeypatch):
    monkeypatch.setenv(trace.BUILD_ID_ENV, "abc1234")
    enroll_scan = live_scan("face_like.jpg")
    r = client.post(
        "/v1/enroll", json={"user_id": USER, "facescan": scan_to_b64(enroll_scan)}
    )
    assert r.status_code == 200, r.text
    line = _only(lines, "enroll")

    assert line["request_id"] == r.headers[trace.REQUEST_ID_HEADER]
    assert line["build_id"] == "abc1234"
    assert line["event"] == "decision"
    assert line["http_status"] == 200
    assert line["outcome"] == "enrolled"
    assert line["error_code"] is None and line["reason"] is None
    assert line["transport"] == "json"
    assert line["enforced"] is True
    assert line["thresholds"] == {
        "match": PLACEHOLDER_THRESHOLD,
        "liveness": LIVENESS_THRESHOLD,
    }
    assert line["frames"]["count"] == 12
    assert 1 <= line["frames"]["with_face"] <= 12
    assert line["frames"]["embedded"] >= 1

    lv = line["liveness"]
    assert set(lv["signals"]) == SIGNALS
    for name, sig in lv["signals"].items():
        assert {"score", "ok", "advisory", "votes"} <= set(sig), name
        assert sig["advisory"] is (name in ADVISORY)
    voting = {n: s["score"] for n, s in lv["signals"].items() if s["votes"]}
    assert lv["min_signal"] == min(voting, key=voting.get)
    assert lv["score"] == pytest.approx(min(voting.values()), abs=1e-4)
    assert lv["live"] is True and lv["failed"] == []

    ch = line["challenge"]
    assert ch["ok"] is True and ch["consumed"] is True
    assert ch["action"] == MOVE_CLOSER
    assert ch["params"] == enroll_scan["challenge"]["params"]
    assert "nonce" not in json.dumps(ch)

    assert {"parse", "queue", "detect", "liveness", "embed", "total"} <= set(
        line["timing_ms"]
    )
    assert "match" not in line

    verify_scan = live_scan("face_like.jpg")
    r = client.post(
        "/v1/verify",
        content=scan_to_msgpack(verify_scan),
        headers={"Content-Type": "application/msgpack", "X-User-Id": USER},
    )
    assert r.status_code == 200, r.text
    v = _only(lines, "verify")
    assert v["request_id"] == r.headers[trace.REQUEST_ID_HEADER]
    assert v["request_id"] != line["request_id"]
    assert v["transport"] == "msgpack"
    assert v["outcome"] == "match"
    assert v["match"]["match"] is True
    assert v["match"]["similarity"] == pytest.approx(r.json()["score"], abs=1e-5)
    assert "match" in v["timing_ms"]

    text = lines.text()
    for ln in lines():
        assert_no_biometrics(ln, text, [enroll_scan, verify_scan])


@requires_models
def test_liveness_fail_names_the_failing_and_minimum_signal(lines, low_det_thresh):
    scan = replayed_scan("face_like.jpg")
    r = client.post("/v1/enroll", json={"user_id": USER, "facescan": scan_to_b64(scan)})
    assert r.status_code == 422
    line = _only(lines, "enroll")
    assert line["outcome"] == "rejected"
    assert line["error_code"] == r.json()["error"]["code"] == "LIVENESS_FAIL"
    assert line["reason"].startswith("liveness check failed")
    lv = line["liveness"]
    assert lv["live"] is False
    assert lv["failed"] and lv["min_signal"] == lv["failed"][0]
    assert lv["signals"]["duplicates"]["ok"] is False
    assert "embed" not in line["timing_ms"]
    assert_no_biometrics(line, lines.text(), [scan])


@requires_models
def test_liveness_endpoint_inspects_and_does_not_spend(lines, low_det_thresh):
    scan = live_scan("face_like.jpg")
    r = client.post("/v1/liveness", json={"facescan": scan_to_b64(scan)})
    assert r.status_code == 200
    line = _only(lines, "liveness")
    assert line["outcome"] == ("live" if r.json()["live"] else "not_live")
    assert line["challenge"]["consumed"] is False
    assert set(line["liveness"]["signals"]) == SIGNALS
    assert_no_biometrics(line, lines.text(), [scan])


def test_unknown_user_is_traced_without_the_user_id(lines):
    scan = build_scan()
    r = client.post("/v1/verify", json={"user_id": USER, "facescan": scan_to_b64(scan)})
    assert r.status_code == 404
    assert USER in r.json()["error"]["message"]
    line = _only(lines, "verify")
    assert line["error_code"] == "USER_NOT_FOUND"
    assert line["reason"] == "user has no enrolled template"
    assert_no_biometrics(line, lines.text(), [scan])


def test_caller_request_id_is_kept_and_echoed(lines):
    rid = "gw-0123456789abcdef"
    r = client.post(
        "/v1/verify",
        json={"user_id": USER, "facescan": scan_to_b64(build_scan())},
        headers={trace.REQUEST_ID_HEADER: rid},
    )
    assert r.headers[trace.REQUEST_ID_HEADER] == rid
    assert _only(lines, "verify")["request_id"] == rid


@pytest.mark.parametrize(
    "bad",
    ["short", "has space in it!", "x" * 65, "semi;colon-0123456", '"quote"-01234'],
)
def test_malformed_request_id_is_replaced(lines, bad):
    r = client.post(
        "/v1/verify",
        json={"user_id": USER, "facescan": scan_to_b64(build_scan())},
        headers={trace.REQUEST_ID_HEADER: bad},
    )
    rid = r.headers[trace.REQUEST_ID_HEADER]
    assert rid != bad and len(rid) == 32
    assert _only(lines, "verify")["request_id"] == rid


def test_unauthorized_is_traced_and_the_key_is_not(lines):
    wrong = "wrong-key-value-that-must-not-be-logged"
    r = TestClient(app).post(
        "/v1/enroll",
        json={"user_id": USER, "facescan": "AAAA"},
        headers={"X-Engine-Key": wrong},
    )
    assert r.status_code == 401
    assert r.headers[trace.REQUEST_ID_HEADER]
    line = _only(lines, "enroll")
    assert line["error_code"] == "UNAUTHORIZED"
    assert wrong not in lines.text()


def test_shape_error_logs_location_not_input(lines):
    secretish = "Zm9vYmFyYmF6cXV4cXV1eGZvb2JhcmJhenF1eA=="
    r = client.post("/v1/enroll", json={"facescan": secretish})
    assert r.status_code == 422
    line = _only(lines, "enroll")
    assert line["error_code"] is None
    assert line["reason"] == "request shape: missing at body.user_id"
    assert secretish not in lines.text()


def test_malformed_scan_does_not_echo_the_body(lines):
    garbage = base64.b64encode(b"\xff\xd8\xff not a facescan at all").decode()
    r = client.post("/v1/enroll", json={"user_id": USER, "facescan": garbage})
    assert r.status_code == 422
    line = _only(lines, "enroll")
    assert line["error_code"] == "MALFORMED_SCAN"
    assert garbage not in lines.text()
    assert USER not in lines.text()


def test_dead_nonce_refusal_records_the_challenge(lines):
    scan = build_scan(
        challenge={
            "id": "c",
            "results": [],
            "nonce": "0" * 32,
            "action": MOVE_CLOSER,
            "params": {"settle_ms": 1500, "target": 0.25},
        }
    )
    r = client.post("/v1/enroll", json={"user_id": USER, "facescan": scan_to_b64(scan)})
    assert r.status_code == 422
    line = _only(lines, "enroll")
    assert line["error_code"] == "CHALLENGE_FAIL"
    assert line["challenge"]["reason"] == "unknown_nonce"
    assert line["challenge"]["consumed"] is False
    assert "0" * 32 not in lines.text()


def test_non_decisions_are_not_traced_but_are_tagged(lines):
    r = client.delete(f"/v1/templates/{USER}")
    assert r.headers[trace.REQUEST_ID_HEADER]
    r = client.get("/v1/challenge")
    assert r.headers[trace.REQUEST_ID_HEADER]
    assert lines() == []


def test_health_reports_the_build_id(monkeypatch):
    monkeypatch.setenv(trace.BUILD_ID_ENV, "abc1234")
    body = TestClient(app).get("/health").json()
    assert body == {
        "status": "ok",
        "engineVersion": body["engineVersion"],
        "buildId": "abc1234",
    }
    monkeypatch.delenv(trace.BUILD_ID_ENV)
    assert TestClient(app).get("/health").json()["buildId"] == "dev"


def test_flat_keeps_scalars_only():
    out = trace._flat(
        {
            "n": 1,
            "f": 0.123456789,
            "s": "x" * 500,
            "b": b"\xff\xd8",
            "emb": [0.1] * 512,
            "nested": {"ok": 1, "deep": {"x": 1}, "arr": [1, 2]},
        }
    )
    assert out == {
        "n": 1,
        "f": 0.12346,
        "s": "x" * trace.MAX_STRING_CHARS,
        "nested": {"ok": 1},
    }


class _FakeSignal:
    def __init__(self, name, score=0.1, ok=False):
        self.name = name
        self.score = score
        self.ok = ok
        self.advisory = False
        self.votes = True
        self.detail = {}


class _FakeResult:
    def __init__(self, failed):
        self.live = False
        self.score = 0.0
        self.retryable = False
        self.failed = failed
        self.signals = {n: _FakeSignal(n) for n in ("motion", "timing", "challenge")}


def _failed_in_line(failed):
    t = trace.Trace("liveness", "req-00000001")
    t.liveness(_FakeResult(failed), LIVENESS_THRESHOLD)
    return t.line(200)["liveness"]["failed"]


def test_failed_keeps_signal_names_and_drops_anything_else():
    assert _failed_in_line(["motion", "timing"]) == ["motion", "timing"]
    assert _failed_in_line(
        [
            "motion",
            "not-a-signal",
            "user alice failed: " + "x" * 400,
            None,
            42,
            {"motion": 1},
            ["timing"],
        ]
    ) == ["motion"]


def test_failed_is_capped_in_length():
    over = ["motion"] * (trace.MAX_FAILED_SIGNALS + 20)
    assert len(_failed_in_line(over)) == trace.MAX_FAILED_SIGNALS


def test_a_decision_line_is_logged_exactly_once_under_uvicorn():
    assert trace._logger.propagate is False

    assert any(type(h) is logging.StreamHandler for h in trace._logger.handlers), (
        "the module's own handler is gone; nothing would emit the line"
    )

    root_saw = logging.handlers.BufferingHandler(capacity=100)
    ours = logging.handlers.BufferingHandler(capacity=100)
    root = logging.getLogger()
    root.addHandler(root_saw)
    trace._logger.addHandler(ours)
    try:
        trace.Trace("verify", "req-00000002").emit(200)
    finally:
        root.removeHandler(root_saw)
        trace._logger.removeHandler(ours)

    assert len(ours.buffer) == 1, "the trace logger did not emit its line"
    assert root_saw.buffer == [], "the line also reached the root logger"
