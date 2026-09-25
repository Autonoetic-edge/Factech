import base64
import binascii

import msgpack

from app import errors

MAX_BYTES = 350 * 1024

MAX_B64_CHARS = 4 * ((MAX_BYTES + 2) // 3)
FRAME_COUNT = 12
JPEG_SOI = b"\xff\xd8"

MAX_FRAME_LONG_EDGE = 1280
MAX_FRAME_PIXELS = 1_000_000
MAX_FRAME_ASPECT = 3

_STATUS = {
    errors.PAYLOAD_TOO_LARGE: errors.HTTP_STATUS[errors.PAYLOAD_TOO_LARGE],
    errors.MALFORMED_SCAN: errors.HTTP_STATUS[errors.MALFORMED_SCAN],
}


class FaceScanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = _STATUS[code]


def parse_facescan(facescan_b64: str) -> dict:
    if not isinstance(facescan_b64, str):
        raise FaceScanError(errors.MALFORMED_SCAN, "facescan must be a base64 string")
    if len(facescan_b64) > MAX_B64_CHARS:
        raise FaceScanError(
            errors.PAYLOAD_TOO_LARGE,
            f"base64 facescan is {len(facescan_b64)} chars, limit is "
            f"{MAX_B64_CHARS} (= {MAX_BYTES} bytes encoded)",
        )
    return parse_facescan_bytes(_decode_base64(facescan_b64))


def parse_facescan_bytes(raw: bytes) -> dict:
    if not isinstance(raw, (bytes, bytearray)):
        raise FaceScanError(errors.MALFORMED_SCAN, "facescan must be msgpack bytes")
    if len(raw) > MAX_BYTES:
        raise FaceScanError(
            errors.PAYLOAD_TOO_LARGE,
            f"msgpack payload is {len(raw)} bytes, limit is {MAX_BYTES}",
        )
    try:
        scan = msgpack.unpackb(
            raw, raw=False, strict_map_key=True, unicode_errors="surrogateescape"
        )
    except Exception as exc:
        raise FaceScanError(errors.MALFORMED_SCAN, f"not valid msgpack: {exc}") from exc
    _validate(scan)
    return scan


def _decode_base64(facescan_b64: str) -> bytes:
    if not isinstance(facescan_b64, str):
        raise FaceScanError(errors.MALFORMED_SCAN, "facescan must be a base64 string")
    try:
        return base64.b64decode(facescan_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FaceScanError(errors.MALFORMED_SCAN, f"invalid base64: {exc}") from exc


def _bad(message: str) -> FaceScanError:
    return FaceScanError(errors.MALFORMED_SCAN, message)


def _frame_bytes(jpeg):
    if isinstance(jpeg, (bytes, bytearray)):
        return bytes(jpeg)
    if isinstance(jpeg, str):
        try:
            return jpeg.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError:
            return None
    return None


_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

_STANDALONE_MARKERS = frozenset({0x01, *range(0xD0, 0xDA)})


def jpeg_dimensions(jpeg: bytes) -> tuple[int, int] | None:
    i, n = 2, len(jpeg)
    while i < n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        while i < n and jpeg[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        marker = jpeg[i]
        i += 1
        if marker in _STANDALONE_MARKERS or marker == 0x00:
            continue
        if i + 2 > n:
            return None
        length = (jpeg[i] << 8) | jpeg[i + 1]
        if marker in _SOF_MARKERS:
            if length < 7 or i + 7 > n:
                return None
            height = (jpeg[i + 3] << 8) | jpeg[i + 4]
            width = (jpeg[i + 5] << 8) | jpeg[i + 6]
            return width, height
        if marker == 0xDA:
            return None
        if length < 2:
            return None
        i += length
    return None


def _check_dimensions(index: int, jpeg: bytes) -> None:
    dims = jpeg_dimensions(jpeg)
    if dims is None:
        return
    width, height = dims
    long_edge, short_edge = max(width, height), min(width, height)
    if long_edge > MAX_FRAME_LONG_EDGE:
        raise _bad(
            f"frame {index}: {width}x{height} exceeds the "
            f"{MAX_FRAME_LONG_EDGE}px long-edge limit"
        )
    if width * height > MAX_FRAME_PIXELS:
        raise _bad(
            f"frame {index}: {width}x{height} exceeds the "
            f"{MAX_FRAME_PIXELS}-pixel limit"
        )
    if short_edge == 0 or long_edge > MAX_FRAME_ASPECT * short_edge:
        raise _bad(
            f"frame {index}: {width}x{height} exceeds the "
            f"{MAX_FRAME_ASPECT}:1 aspect-ratio limit"
        )


def _validate(scan) -> None:
    if not isinstance(scan, dict):
        raise _bad("FaceScan must be a msgpack map")

    version = scan.get("version")
    if type(version) is not int or version != 1:
        raise _bad(f"version must be 1, got {version!r}")

    if not isinstance(scan.get("device"), dict):
        raise _bad("device map missing or not a map")
    if not isinstance(scan.get("challenge"), dict):
        raise _bad("challenge map missing or not a map")

    frames = scan.get("frames")
    if not isinstance(frames, list) or len(frames) != FRAME_COUNT:
        n = len(frames) if isinstance(frames, list) else "not a list"
        raise _bad(f"frames must be a list of exactly {FRAME_COUNT} entries, got {n}")

    prev_ts: int | None = None
    for i, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise _bad(f"frame {i} is not a map")
        jpeg = _frame_bytes(frame.get("jpeg_bytes"))
        if jpeg is None or not jpeg.startswith(JPEG_SOI):
            raise _bad(f"frame {i}: jpeg_bytes must be non-empty JPEG bytes")
        frame["jpeg_bytes"] = jpeg
        _check_dimensions(i, jpeg)
        ts = frame.get("ts_ms")

        if isinstance(ts, bool) or not isinstance(ts, int):
            raise _bad(f"frame {i}: ts_ms must be an int")
        if prev_ts is not None and ts <= prev_ts:
            raise _bad("frames must be ordered by ascending ts_ms")
        prev_ts = ts
        if (
            "pose" in frame
            and frame["pose"] is not None
            and not isinstance(frame["pose"], dict)
        ):
            raise _bad(f"frame {i}: pose must be a map or null")
