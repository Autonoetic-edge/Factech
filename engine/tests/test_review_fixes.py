import asyncio
import json
import threading
import time

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import challenge, store
from app.facescan import FaceScanError, jpeg_dimensions, parse_facescan_bytes
from app.main import app
from helpers import (
    AUTH,
    CAPTURE_MS,
    FIXTURES,
    build_scan,
    challenge_map,
    issue_for,
    make_approach_jpegs,
    scan_to_b64,
    scan_to_msgpack,
    settle_frames,
)

client = TestClient(app, headers=AUTH)


@pytest.fixture(autouse=True)
def _clean_state():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


def _scan_with_params(name: str, params: dict, blank_first: int = 0) -> dict:
    issued = issue_for(challenge.MOVE_CLOSER, params)
    jpegs = make_approach_jpegs(
        (FIXTURES / name).read_bytes(), hold=settle_frames(params["settle_ms"])
    )
    for i in range(blank_first):
        jpegs[i] = b"\xff\xd8" + bytes([i + 1]) * 32
    return build_scan(
        jpegs=jpegs,
        ts_ms=[1000 + i * CAPTURE_MS for i in range(len(jpegs))],
        challenge={
            "id": "ch-1",
            "results": [],
            "nonce": issued["nonce"],
            "action": issued["action"],
            "params": dict(issued["params"]),
        },
    )


_VEC = {
    "alice": np.eye(512, dtype=np.float32)[0],
    "bob": np.eye(512, dtype=np.float32)[1],
}


def _face(who: str, cx: float, size: float, score: float) -> dict:
    half = size / 2
    return {
        "who": who,
        "bbox": [cx - half, 240 - half, cx + half, 240 + half],
        "landmarks_5": [
            [cx - 20, 220],
            [cx + 20, 220],
            [cx, 240],
            [cx - 15, 265],
            [cx + 15, 265],
        ],
        "det_score": score,
    }


def _alice_bob_detections() -> list[list[dict]]:
    frames = []
    for i in range(12):
        alice = _face("alice", 320, 160, 0.97 if i == 0 else 0.80)
        if i in (1, 2):
            frames.append([_face("bob", 560, 200, 0.96), alice])
        else:
            frames.append([alice])
    return frames


class _PassingLiveness:
    live = True
    score = 1.0
    failed: list = []
    retryable = False


@pytest.fixture
def fake_pipeline(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_scan_detections", lambda scan: _alice_bob_detections())
    monkeypatch.setattr(
        main,
        "align_largest_face",
        lambda jpeg, detection=None: np.full(
            (2, 2, 3), ord(detection["who"][0]), np.uint8
        ),
    )
    monkeypatch.setattr(
        main,
        "embed_aligned",
        lambda aligned: _VEC["alice" if aligned[0, 0, 0] == ord("a") else "bob"],
    )
    monkeypatch.setattr(main, "_liveness_of", lambda *a, **k: _PassingLiveness())
    return main


def _nonced_scan() -> dict:
    return build_scan(challenge=challenge_map())


def test_f03_embedding_frames_follow_the_tracked_face(fake_pipeline):
    detections = _alice_bob_detections()
    selected = fake_pipeline._embedding_frames(build_scan(), detections)
    assert [det["who"] for _, _, det in selected] == ["alice"] * 3


def _jpeg(w: int, h: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 90, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.mark.parametrize(
    ("w", "h"),
    [
        (4096, 64),
        (1400, 1050),
        (1200, 1000),
        (900, 250),
        (250, 900),
    ],
)
def test_f20_oversized_frame_dimensions_rejected_before_decode(w, h):
    scan = build_scan(jpegs=[_jpeg(w, h)] + [_jpeg(64, 64)] * 11)
    with pytest.raises(FaceScanError) as exc:
        parse_facescan_bytes(scan_to_msgpack(scan))
    assert exc.value.code == "MALFORMED_SCAN"
    assert "frame 0" in exc.value.message


@pytest.mark.parametrize(("w", "h"), [(1280, 720), (640, 360), (480, 640), (288, 216)])
def test_f20_capture_sized_frames_still_parse(w, h):
    parse_facescan_bytes(scan_to_msgpack(build_scan(jpegs=[_jpeg(w, h)] * 12)))


def test_f20_header_is_read_without_decoding(monkeypatch):
    import cv2 as cv2_mod

    def boom(*a, **k):
        raise AssertionError("parser decoded a frame")

    scan = scan_to_msgpack(build_scan(jpegs=[_jpeg(4096, 64)] * 12))
    monkeypatch.setattr(cv2_mod, "imdecode", boom)
    with pytest.raises(FaceScanError):
        parse_facescan_bytes(scan)


def test_f20_undecodable_soi_frame_is_still_structural_only():
    parse_facescan_bytes(
        scan_to_msgpack(build_scan(jpegs=[b"\xff\xd8" + b"\x01" * 32] * 12))
    )


def test_f20_frame_dimensions_reader_handles_padding_and_progressive():
    ok, prog = cv2.imencode(
        ".jpg", np.zeros((300, 700, 3), np.uint8), [cv2.IMWRITE_JPEG_PROGRESSIVE, 1]
    )
    assert ok
    assert jpeg_dimensions(prog.tobytes()) == (700, 300)
    base = _jpeg(320, 200)
    padded = base[:2] + b"\xff\xff\xff" + base[2:]
    assert jpeg_dimensions(padded) == (320, 200)


def test_f19_oversized_json_body_is_413_before_json_parsing(monkeypatch):
    import types

    from app import main

    def no_parse(*a, **k):
        raise AssertionError("json.loads ran on an oversized body")

    monkeypatch.setattr(
        main, "json", types.SimpleNamespace(loads=no_parse, JSONDecodeError=ValueError)
    )
    body = json.dumps(
        {"user_id": "u", "facescan": scan_to_b64(build_scan()), "junk": "x" * 1_100_000}
    )
    r = client.post(
        "/v1/enroll", content=body, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_f19_oversized_chunked_body_is_413():
    def chunks():
        for _ in range(40):
            yield b"x" * 50_000

    r = client.post(
        "/v1/liveness",
        content=chunks(),
        headers={"Content-Type": "application/msgpack"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_f19_a_scan_at_the_limit_still_fits_under_the_body_cap():
    from app import facescan, main

    envelope = len('{"user_id":"","facescan":""}') + 64
    headroom = main.MAX_BODY_BYTES - facescan.MAX_B64_CHARS
    assert headroom > envelope


@pytest.fixture
def alice_only(fake_pipeline, monkeypatch):
    monkeypatch.setattr(
        fake_pipeline,
        "_scan_detections",
        lambda scan: [[_face("alice", 320, 160, 0.9)] for _ in range(12)],
    )
    return fake_pipeline


@pytest.mark.parametrize(
    "bad", ["a b", "alice@example.com", "ünïcode", "a" * 65, "x/y"]
)
def test_f40_non_canonical_user_id_is_a_shape_error(bad):
    r = client.post(
        "/v1/enroll", json={"user_id": bad, "facescan": scan_to_b64(build_scan())}
    )
    assert r.status_code == 422
    assert "detail" in r.json()
    assert store.user_count() == 0


def test_f40_user_id_of_64_chars_is_accepted_by_the_shape_check():
    r = client.post(
        "/v1/verify", json={"user_id": "a" * 64, "facescan": scan_to_b64(build_scan())}
    )
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_f40_delete_normalises_case():
    store.enroll("alice", _VEC["alice"])
    r = client.delete("/v1/templates/ALICE")
    assert r.status_code == 200
    assert r.json()["user_id"] == "alice"
    assert not store.has_user("alice")


@pytest.fixture
def no_inference(monkeypatch):
    from app import main

    def boom(scan):
        raise AssertionError("detection ran for a scan with a dead nonce")

    monkeypatch.setattr(main, "_scan_detections", boom)


@pytest.fixture
def no_face_detections(monkeypatch):
    from app import main

    calls = []

    def empty(scan):
        calls.append(1)
        return [[] for _ in range(12)]

    monkeypatch.setattr(main, "_scan_detections", empty)
    return calls


def _dead_nonce_scan(kind, monkeypatch):
    if kind == "unknown":
        return build_scan(
            challenge={
                "id": "x",
                "results": [],
                "nonce": "0" * 32,
                "action": challenge.MOVE_CLOSER,
                "params": {"settle_ms": 900, "target": 0.3},
            }
        )
    scan = build_scan(challenge=challenge_map())
    if kind == "reused":
        challenge.consume_from_scan(scan)
    else:
        at_ms = int(time.time() * 1000) + challenge.EXPIRY_MS + 1
        monkeypatch.setattr(challenge, "_now_ms", lambda: at_ms)
    return scan


@pytest.mark.parametrize("kind", ["unknown", "expired", "reused"])
@pytest.mark.parametrize("route", ["/v1/enroll", "/v1/verify"])
def test_f22_dead_nonce_is_refused_before_inference(
    no_inference, monkeypatch, kind, route
):
    store.enroll("alice", _VEC["alice"])
    scan = _dead_nonce_scan(kind, monkeypatch)
    r = client.post(route, json={"user_id": "alice", "facescan": scan_to_b64(scan)})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"


def _mismatched_scan(what):
    scan = build_scan(challenge=challenge_map())
    if what == "params":
        scan["challenge"]["params"] = {"settle_ms": 1, "target": 0.3}
    else:
        scan["challenge"]["action"] = challenge.LOOK_LEFT
    return scan


def _issued_shape(scan):
    record = challenge._issued[scan["challenge"]["nonce"]]
    return {"action": record["action"], "params": dict(record["params"])}


def test_f22_mismatch_with_a_face_is_challenge_fail_and_spent(
    monkeypatch, fake_pipeline
):
    monkeypatch.setattr(
        fake_pipeline,
        "_scan_detections",
        lambda scan: [[_face("alice", 320, 160, 0.9)] for _ in range(12)],
    )
    scan = _mismatched_scan("params")
    r = client.post(
        "/v1/enroll", json={"user_id": "alice", "facescan": scan_to_b64(scan)}
    )
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert challenge.inspect_from_scan(scan).reason == challenge.REUSED_NONCE


def test_f22_verify_still_says_user_not_found_first(no_inference):
    r = client.post(
        "/v1/verify", json={"user_id": "ghost", "facescan": scan_to_b64(build_scan())}
    )
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def _slow_detections(delay_s: float, gate: "threading.Event | None" = None):
    def run(scan):
        if gate is not None:
            assert gate.wait(10), "test gate never released"
        else:
            time.sleep(delay_s)
        return [[] for _ in range(12)]

    return run


async def _post_scan(ac, route="/v1/enroll"):
    issued = challenge.issue(
        route.rsplit("/", 1)[-1], "" if route.endswith("liveness") else "alice"
    )
    challenge._issued[issued["nonce"]]["issued_ms"] -= 8000
    scan = build_scan(challenge=issued)
    for i, frame in enumerate(scan["frames"]):
        frame["ts_ms"] = i * 700
    body = {"user_id": "alice", "facescan": scan_to_b64(scan)}
    return await ac.post(route, json=body)


def test_f21_health_answers_while_a_scan_is_running(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_scan_detections", _slow_detections(0.6))

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=AUTH
        ) as ac:
            t0 = time.perf_counter()
            scan = asyncio.create_task(_post_scan(ac))
            await asyncio.sleep(0.05)
            health = await ac.get("/health")
            health_s = time.perf_counter() - t0
            return (await scan), health, health_s

    scan, health, health_s = asyncio.run(scenario())
    assert scan.json()["error"]["code"] == "NO_FACE"
    assert health.status_code == 200
    assert health_s < 0.4, f"/health waited {health_s:.3f}s behind inference"


def test_f21_full_queue_is_503_busy_with_retry_after(monkeypatch):
    from app import main

    gate = threading.Event()
    monkeypatch.setattr(main, "_scan_detections", _slow_detections(0, gate))
    admitted = main.MAX_QUEUED_SCANS + 1

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=AUTH
        ) as ac:
            tasks = [asyncio.create_task(_post_scan(ac)) for _ in range(admitted)]
            await asyncio.sleep(0.2)
            extra = await _post_scan(ac, "/v1/liveness")
            gate.set()
            return extra, await asyncio.gather(*tasks)

    extra, results = asyncio.run(scenario())
    assert extra.status_code == 503
    assert extra.json()["error"]["code"] == "BUSY"
    assert int(extra.headers["Retry-After"]) >= 1
    assert all(r.json()["error"]["code"] == "NO_FACE" for r in results)
    assert main.inflight_scans() == 0


def test_f21_scans_run_one_at_a_time(monkeypatch):
    from app import main

    live, peak = [0], [0]
    lock = threading.Lock()

    def counted(scan):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return [[] for _ in range(12)]

    monkeypatch.setattr(main, "_scan_detections", counted)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=AUTH
        ) as ac:
            return await asyncio.gather(*[_post_scan(ac) for _ in range(3)])

    asyncio.run(scenario())
    assert peak[0] == 1
