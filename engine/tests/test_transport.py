import pytest
from fastapi.testclient import TestClient

from app import store
from app.facescan import MAX_BYTES
from app.main import app
from helpers import (
    AUTH,
    build_scan,
    live_scan,
    make_noise_jpeg,
    renonce,
    scan_to_b64,
    scan_to_legacy_msgpack,
    scan_to_msgpack,
)

client = TestClient(app, headers=AUTH)

MSGPACK = "application/msgpack"


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


def fixture_scan(name: str) -> dict:
    return live_scan(name)


def _with_fresh_challenge(scan: dict) -> dict:
    return renonce(scan) if scan.get("challenge", {}).get("nonce") else scan


def post_msgpack(path: str, scan: dict, user_id: str = "u", **kwargs):
    headers = {"Content-Type": MSGPACK, "X-User-Id": user_id}
    headers.update(kwargs.pop("headers", {}))
    return client.post(
        path,
        content=scan_to_msgpack(_with_fresh_challenge(scan)),
        headers=headers,
        **kwargs,
    )


def post_json(path: str, scan: dict, user_id: str = "u"):
    return client.post(
        path,
        json={
            "user_id": user_id,
            "facescan": scan_to_b64(_with_fresh_challenge(scan)),
        },
    )


def test_msgpack_enroll_reaches_the_parser_not_a_415():
    r = post_msgpack("/v1/enroll", build_scan())
    assert r.status_code in (422, 503)
    assert r.json()["error"]["code"] in ("CHALLENGE_FAIL", "MODEL_UNAVAILABLE")


def test_msgpack_verify_unknown_user_404():
    r = post_msgpack("/v1/verify", build_scan(), user_id="ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_msgpack_missing_user_id_header_422():
    r = client.post(
        "/v1/enroll",
        content=scan_to_msgpack(build_scan()),
        headers={"Content-Type": MSGPACK},
    )
    assert r.status_code == 422
    assert "detail" in r.json()


def test_msgpack_blank_user_id_header_422():
    r = post_msgpack("/v1/enroll", build_scan(), user_id="   ")
    assert r.status_code == 422
    assert "detail" in r.json()


def test_msgpack_user_id_header_is_case_insensitive():
    r = client.post(
        "/v1/verify",
        content=scan_to_msgpack(build_scan()),
        headers={"Content-Type": MSGPACK, "x-USER-id": "ghost"},
    )
    assert r.status_code == 404


def test_msgpack_content_type_with_parameters_is_accepted():
    r = client.post(
        "/v1/verify",
        content=scan_to_msgpack(build_scan()),
        headers={
            "Content-Type": "application/msgpack; charset=binary",
            "X-User-Id": "ghost",
        },
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "content_type",
    ["application/msgpack", "application/x-msgpack", "application/vnd.msgpack"],
)
def test_msgpack_content_type_aliases(content_type):
    r = client.post(
        "/v1/verify",
        content=scan_to_msgpack(build_scan()),
        headers={"Content-Type": content_type, "X-User-Id": "ghost"},
    )
    assert r.status_code == 404


def test_msgpack_garbage_body_422_malformed():
    r = client.post(
        "/v1/enroll",
        content=b"\xc1\xc1not msgpack",
        headers={"Content-Type": MSGPACK, "X-User-Id": "u"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"


def test_msgpack_oversized_body_413():
    big = build_scan(jpegs=[make_noise_jpeg(seed=i) for i in range(12)])
    raw = scan_to_msgpack(big)
    assert len(raw) > MAX_BYTES
    r = client.post(
        "/v1/enroll",
        content=raw,
        headers={"Content-Type": MSGPACK, "X-User-Id": "u"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_msgpack_eleven_frames_422():
    scan = build_scan()
    scan["frames"] = scan["frames"][:11]
    r = post_msgpack("/v1/enroll", scan)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"


def test_json_transport_still_default_without_content_type_games():
    r = post_json("/v1/verify", build_scan(), user_id="ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_json_body_that_is_not_json_422():
    r = client.post(
        "/v1/enroll", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422
    assert "detail" in r.json()


def test_both_transports_agree_on_errors():
    scan = build_scan()
    scan["frames"] = scan["frames"][:11]
    a = post_json("/v1/enroll", scan)
    b = post_msgpack("/v1/enroll", scan)
    assert a.status_code == b.status_code == 422
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "MALFORMED_SCAN"


def post_legacy_msgpack(path: str, scan: dict, user_id: str = "u"):
    return client.post(
        path,
        content=scan_to_legacy_msgpack(_with_fresh_challenge(scan)),
        headers={"Content-Type": MSGPACK, "X-User-Id": user_id},
    )


def test_legacy_packed_scan_over_msgpack_transport_reaches_the_engine():
    r = post_legacy_msgpack("/v1/verify", build_scan(), user_id="ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


def test_legacy_packed_malformed_scan_still_rejected():
    scan = build_scan()
    scan["frames"] = scan["frames"][:11]
    r = post_legacy_msgpack("/v1/enroll", scan)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MALFORMED_SCAN"
