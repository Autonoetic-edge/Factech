import base64
from pathlib import Path

import cv2
import msgpack
import numpy as np

from app import challenge as challenge_mod

FRAME_COUNT = 12
FIXTURES = Path(__file__).parent / "fixtures"

CAPTURE_MS = 500

TEST_API_KEY = "test-engine-key-not-a-secret"

AUTH = {"X-Engine-Key": TEST_API_KEY}


def make_jpeg(size: int = 64, shade: int = 128) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((size, size, 3), shade, np.uint8))
    assert ok
    return buf.tobytes()


def make_noise_jpeg(size: int = 256, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


LIVE_JITTER_DEG = 1.5
LIVE_JITTER_SCALE = 0.015
LIVE_JITTER_PX = 6.0
LIVE_JITTER_NOISE = 6


def make_live_jpegs(
    source: bytes,
    count: int = FRAME_COUNT,
    seed: int = 7,
    quality: int = 70,
) -> list[bytes]:
    rng = np.random.default_rng(seed)
    img = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, "source is not a decodable JPEG"
    h, w = img.shape[:2]
    frames = []
    for _ in range(count):
        matrix = cv2.getRotationMatrix2D(
            (w / 2, h / 2),
            rng.uniform(-LIVE_JITTER_DEG, LIVE_JITTER_DEG),
            1.0 + rng.uniform(-LIVE_JITTER_SCALE, LIVE_JITTER_SCALE),
        )
        matrix[0, 2] += rng.uniform(-LIVE_JITTER_PX, LIVE_JITTER_PX)
        matrix[1, 2] += rng.uniform(-LIVE_JITTER_PX, LIVE_JITTER_PX)
        moved = cv2.warpAffine(img, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
        noise = rng.integers(
            -LIVE_JITTER_NOISE, LIVE_JITTER_NOISE + 1, moved.shape, dtype=np.int16
        )
        noisy = np.clip(moved.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, quality])
        assert ok
        frames.append(buf.tobytes())
    return frames


TURN_AMPLITUDE = 1.0


def _yaw_remap(img: np.ndarray, amount: float) -> np.ndarray:
    h, w = img.shape[:2]
    u = np.arange(w, dtype=np.float32) / (w - 1)
    warped = 0.5 + (u - 0.5) * (1.0 + amount * (u - 0.5))
    map_x = np.tile(np.clip(warped, 0.0, 1.0) * (w - 1), (h, 1))
    map_y = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
    return cv2.remap(
        img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def make_turned_jpegs(
    source: bytes,
    action: str = challenge_mod.LOOK_LEFT,
    count: int = FRAME_COUNT,
    seed: int = 7,
    quality: int = 70,
    amplitude: float = TURN_AMPLITUDE,
) -> list[bytes]:
    assert action in (challenge_mod.LOOK_LEFT, challenge_mod.LOOK_RIGHT), action
    signed = amplitude if action == challenge_mod.LOOK_LEFT else -amplitude
    img = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, "source is not a decodable JPEG"
    turned = []
    for i in range(count):
        fraction = min(1.0, max(0.0, (i - 1) / (count - 4)))
        ok, buf = cv2.imencode(".jpg", _yaw_remap(img, signed * fraction))
        assert ok
        turned.append(buf.tobytes())

    return [
        make_live_jpegs(frame, count=1, seed=seed * 1000 + i, quality=quality)[0]
        for i, frame in enumerate(turned)
    ]


APPROACH_RATIO = 1.5


def _ramp_fraction(i: int, count: int, hold: int = 1) -> float:
    span = max(1, count - hold - 2)
    return min(1.0, max(0.0, (i - hold) / span))


def settle_frames(settle_ms: int, cadence_ms: int = CAPTURE_MS) -> int:
    return settle_ms // cadence_ms + 1


def make_approach_jpegs(
    source: bytes,
    count: int = FRAME_COUNT,
    seed: int = 7,
    quality: int = 70,
    ratio: float = APPROACH_RATIO,
    hold: int = 1,
) -> list[bytes]:
    img = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, "source is not a decodable JPEG"
    h, w = img.shape[:2]
    approached = []
    for i in range(count):
        scale = 1.0 / ratio + (1.0 - 1.0 / ratio) * _ramp_fraction(i, count, hold)
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 0.0, scale)
        zoomed = cv2.warpAffine(img, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
        ok, buf = cv2.imencode(".jpg", zoomed)
        assert ok
        approached.append(buf.tobytes())
    return [
        make_live_jpegs(frame, count=1, seed=seed * 1000 + i, quality=quality)[0]
        for i, frame in enumerate(approached)
    ]


def make_action_jpegs(
    source: bytes,
    action: str,
    seed: int = 7,
    hold: int = 1,
    ratio: float = APPROACH_RATIO,
) -> list[bytes]:
    if action == challenge_mod.MOVE_CLOSER:
        return make_approach_jpegs(source, seed=seed, hold=hold, ratio=ratio)
    return make_turned_jpegs(source, action=action, seed=seed)


FIXTURE_SETTLE_MS = 1500
FIXTURE_TARGET = 0.25
FIXTURE_PARAMS = {"settle_ms": FIXTURE_SETTLE_MS, "target": FIXTURE_TARGET}


def issue_for(action: str, params: dict | None = None) -> dict:
    assert action in challenge_mod.ACTIONS, action
    previous = challenge_mod.ISSUABLE_ACTIONS
    challenge_mod.ISSUABLE_ACTIONS = (action,)
    try:
        issued = challenge_mod.issue()
    finally:
        challenge_mod.ISSUABLE_ACTIONS = previous
    if params is not None:
        with challenge_mod._lock:
            challenge_mod._issued[issued["nonce"]]["params"] = dict(params)
        issued["params"] = dict(params)
    return issued


def challenge_map(
    action: str = challenge_mod.MOVE_CLOSER,
    challenge_id: str = "ch-1",
    params: dict | None = FIXTURE_PARAMS,
) -> dict:
    issued = issue_for(action, params)
    return {
        "id": challenge_id,
        "results": [],
        "nonce": issued["nonce"],
        "action": issued["action"],
        "params": dict(issued["params"]),
    }


def renonce(scan: dict, action: str | None = None) -> dict:
    action = action or scan["challenge"]["action"]
    scan["challenge"] = challenge_map(action, scan["challenge"].get("id", "ch-1"))
    return scan


def live_scan(
    name: str, seed: int = 7, action: str = challenge_mod.MOVE_CLOSER
) -> dict:
    challenge = challenge_map(action)
    jpegs = make_action_jpegs(
        (FIXTURES / name).read_bytes(),
        action,
        seed=seed,
        hold=settle_frames(challenge["params"]["settle_ms"]),
    )
    return build_scan(
        jpegs=jpegs,
        ts_ms=[1000 + i * CAPTURE_MS for i in range(len(jpegs))],
        challenge=challenge,
    )


def flat_scan(
    name: str, seed: int = 7, action: str = challenge_mod.MOVE_CLOSER
) -> dict:
    jpegs = make_live_jpegs((FIXTURES / name).read_bytes(), seed=seed)
    return build_scan(
        jpegs=jpegs,
        ts_ms=[1000 + i * CAPTURE_MS for i in range(len(jpegs))],
        challenge=challenge_map(action),
    )


def replayed_scan(name: str) -> dict:
    jpeg = (FIXTURES / name).read_bytes()
    return build_scan(
        jpegs=[jpeg] * FRAME_COUNT,
        ts_ms=[1000 + i * CAPTURE_MS for i in range(FRAME_COUNT)],
        challenge=challenge_map(),
    )


def make_png(size: int = 64) -> bytes:
    ok, buf = cv2.imencode(".png", np.full((size, size, 3), 128, np.uint8))
    assert ok
    return buf.tobytes()


def build_scan(
    jpegs: list[bytes] | None = None,
    version: int = 1,
    ts_ms: list[int] | None = None,
    with_device: bool = True,
    with_challenge: bool = True,
    challenge: dict | None = None,
) -> dict:
    if jpegs is None:
        jpegs = [make_jpeg() for _ in range(FRAME_COUNT)]
    if ts_ms is None:
        ts_ms = [1000 + i * 33 for i in range(len(jpegs))]
    scan = {
        "version": version,
        "frames": [
            {"jpeg_bytes": j, "ts_ms": t, "pose": None} for j, t in zip(jpegs, ts_ms)
        ],
    }
    if with_device:
        scan["device"] = {
            "user_agent": "pytest",
            "screen": {"w": 400, "h": 800},
            "tz_offset": -420,
        }
    if with_challenge:
        scan["challenge"] = challenge or {"id": "ch-1", "results": []}
    return scan


def scan_to_b64(scan: dict) -> str:
    return base64.b64encode(scan_to_msgpack(scan)).decode()


def scan_to_msgpack(scan: dict) -> bytes:
    return msgpack.packb(scan, use_bin_type=True)


def scan_to_legacy_msgpack(scan: dict) -> bytes:
    return msgpack.packb(scan, use_bin_type=False)
