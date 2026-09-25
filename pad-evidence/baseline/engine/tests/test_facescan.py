import base64

import msgpack
import pytest

from app.facescan import (
    MAX_B64_CHARS,
    MAX_BYTES,
    FaceScanError,
    parse_facescan,
    parse_facescan_bytes,
)
from helpers import (
    build_scan,
    make_noise_jpeg,
    make_png,
    scan_to_b64,
    scan_to_legacy_msgpack,
    scan_to_msgpack,
)


def expect_error(b64: str, code: str) -> FaceScanError:
    with pytest.raises(FaceScanError) as excinfo:
        parse_facescan(b64)
    assert excinfo.value.code == code
    return excinfo.value


def test_valid_scan_parses():
    scan = parse_facescan(scan_to_b64(build_scan()))
    assert scan["version"] == 1
    assert isinstance(scan["device"], dict)
    assert isinstance(scan["challenge"], dict)
    assert len(scan["frames"]) == 12
    first = scan["frames"][0]
    assert first["jpeg_bytes"].startswith(b"\xff\xd8")
    assert first["ts_ms"] == 1000
    assert first["pose"] is None


def test_payload_over_350kb_rejected():
    big = build_scan(jpegs=[make_noise_jpeg(seed=i) for i in range(12)])
    raw = msgpack.packb(big, use_bin_type=True)
    assert len(raw) > MAX_BYTES
    err = expect_error(base64.b64encode(raw).decode(), "PAYLOAD_TOO_LARGE")
    assert err.status_code == 413


def test_payload_under_350kb_accepted():
    scan = build_scan()
    raw = msgpack.packb(scan, use_bin_type=True)
    assert len(raw) < MAX_BYTES
    assert parse_facescan(base64.b64encode(raw).decode())["version"] == 1


def test_bad_base64_rejected():
    expect_error("!!! not base64 !!!", "MALFORMED_SCAN")


def test_not_msgpack_rejected():
    expect_error(base64.b64encode(b"hello world").decode(), "MALFORMED_SCAN")


def test_top_level_not_a_map_rejected():
    packed = msgpack.packb([1, 2, 3], use_bin_type=True)
    expect_error(base64.b64encode(packed).decode(), "MALFORMED_SCAN")


def test_wrong_version_rejected():
    expect_error(scan_to_b64(build_scan(version=2)), "MALFORMED_SCAN")


def test_bool_version_rejected():
    expect_error(scan_to_b64(build_scan(version=True)), "MALFORMED_SCAN")


def test_eleven_frames_rejected():
    scan = build_scan()
    scan["frames"] = scan["frames"][:11]
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_thirteen_frames_rejected():
    scan = build_scan()
    scan["frames"] = scan["frames"] + [dict(scan["frames"][-1], ts_ms=99999)]
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_non_jpeg_frame_rejected():
    scan = build_scan()
    scan["frames"][3]["jpeg_bytes"] = make_png()
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_empty_frame_bytes_rejected():
    scan = build_scan()
    scan["frames"][0]["jpeg_bytes"] = b""
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_descending_ts_rejected():
    scan = build_scan(ts_ms=[1000 + i * 33 for i in range(12)][::-1])
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_duplicate_ts_rejected():
    scan = build_scan(ts_ms=[1000] * 12)
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_missing_device_map_rejected():
    expect_error(scan_to_b64(build_scan(with_device=False)), "MALFORMED_SCAN")


def test_missing_challenge_map_rejected():
    expect_error(scan_to_b64(build_scan(with_challenge=False)), "MALFORMED_SCAN")


def test_bad_pose_type_rejected():
    scan = build_scan()
    scan["frames"][0]["pose"] = "tilted"
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_missing_ts_ms_rejected():
    scan = build_scan()
    del scan["frames"][5]["ts_ms"]
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_oversized_base64_rejected_before_decoding():
    err = expect_error("!" * (MAX_B64_CHARS + 4), "PAYLOAD_TOO_LARGE")
    assert err.status_code == 413


def test_b64_char_limit_admits_a_maximum_size_payload():
    assert len(base64.b64encode(b"x" * MAX_BYTES)) <= MAX_B64_CHARS


def test_oversized_encoded_length_still_reported_as_too_large():
    big = build_scan(jpegs=[make_noise_jpeg(seed=i) for i in range(12)])
    err = expect_error(scan_to_b64(big), "PAYLOAD_TOO_LARGE")
    assert err.status_code == 413


def test_parse_facescan_bytes_accepts_raw_msgpack():
    scan = parse_facescan_bytes(scan_to_msgpack(build_scan()))
    assert scan["version"] == 1
    assert len(scan["frames"]) == 12


def test_parse_facescan_bytes_rejects_oversized():
    big = scan_to_msgpack(
        build_scan(jpegs=[make_noise_jpeg(seed=i) for i in range(12)])
    )
    assert len(big) > MAX_BYTES
    with pytest.raises(FaceScanError) as excinfo:
        parse_facescan_bytes(big)
    assert excinfo.value.code == "PAYLOAD_TOO_LARGE"


def test_parse_facescan_bytes_rejects_garbage():
    with pytest.raises(FaceScanError) as excinfo:
        parse_facescan_bytes(b"\xc1\xc1 not msgpack")
    assert excinfo.value.code == "MALFORMED_SCAN"


def test_both_entry_points_agree():
    scan = build_scan()
    assert parse_facescan(scan_to_b64(scan)) == parse_facescan_bytes(
        scan_to_msgpack(scan)
    )


def test_legacy_str_packed_frames_accepted():
    original = build_scan()
    scan = parse_facescan_bytes(scan_to_legacy_msgpack(original))
    assert len(scan["frames"]) == 12
    for parsed, source in zip(scan["frames"], original["frames"]):
        assert isinstance(parsed["jpeg_bytes"], bytes)
        assert parsed["jpeg_bytes"] == source["jpeg_bytes"]


def test_legacy_str_packed_non_jpeg_still_rejected():
    scan = build_scan()
    scan["frames"][2]["jpeg_bytes"] = make_png()
    with pytest.raises(FaceScanError) as excinfo:
        parse_facescan_bytes(scan_to_legacy_msgpack(scan))
    assert excinfo.value.code == "MALFORMED_SCAN"


def test_str_frame_that_is_not_binary_rejected():
    scan = build_scan()
    scan["frames"][0]["jpeg_bytes"] = "definitely not a jpeg"
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


@pytest.mark.parametrize(
    "timestamps",
    [
        list(range(12)),
        [1000 + i * 33 for i in range(12)],
        [1_700_000_000_000 + i for i in range(12)],
        [-100 + i for i in range(12)],
    ],
    ids=["uint8", "uint16", "uint64", "negative"],
)
def test_any_integer_width_accepted_for_ts_ms(timestamps):
    scan = parse_facescan(scan_to_b64(build_scan(ts_ms=timestamps)))
    assert [f["ts_ms"] for f in scan["frames"]] == timestamps


def test_bool_ts_ms_rejected():
    scan = build_scan()
    scan["frames"][0]["ts_ms"] = True
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_float_ts_ms_rejected():
    scan = build_scan()
    scan["frames"][3]["ts_ms"] = 1099.0
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")


def test_string_ts_ms_rejected():
    scan = build_scan()
    scan["frames"][3]["ts_ms"] = "1099"
    expect_error(scan_to_b64(scan), "MALFORMED_SCAN")
