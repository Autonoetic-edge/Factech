import inspect

import pytest
from fastapi.testclient import TestClient

from app import auth, errors
from app.main import app
from helpers import AUTH, TEST_API_KEY

client = TestClient(app)

WRONG_KEYS = [
    pytest.param("wrong-key", id="wrong"),
    pytest.param("", id="empty"),
    pytest.param(TEST_API_KEY + "x", id="right-plus-suffix"),
    pytest.param(TEST_API_KEY[:-1], id="right-truncated"),
    pytest.param(TEST_API_KEY.upper(), id="wrong-case"),
    pytest.param(" " + TEST_API_KEY, id="leading-space"),
]


def _unauthorized(response) -> bool:
    return (
        response.status_code == 401
        and response.json()["error"]["code"] == errors.UNAUTHORIZED
    )


def test_missing_key_is_401():
    r = client.post("/v1/enroll", json={"user_id": "u", "facescan": "AAAA"})
    assert _unauthorized(r)
    assert r.status_code == errors.HTTP_STATUS[errors.UNAUTHORIZED]


@pytest.mark.parametrize("key", WRONG_KEYS)
def test_wrong_key_is_401(key):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "u", "facescan": "AAAA"},
        headers={"X-Engine-Key": key},
    )
    assert _unauthorized(r)


def test_right_key_reaches_the_endpoint():
    r = client.post(
        "/v1/enroll", json={"user_id": "u", "facescan": "AAAA"}, headers=AUTH
    )
    assert r.status_code != 401
    assert r.json()["error"]["code"] == errors.MALFORMED_SCAN


def test_header_name_is_case_insensitive():
    assert (
        client.get("/v1/info", headers={"x-ENGINE-key": TEST_API_KEY}).status_code
        == 200
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/info"),
        ("POST", "/v1/enroll"),
        ("POST", "/v1/verify"),
        ("DELETE", "/v1/templates/alice"),
        ("POST", "/v1/liveness"),
        ("POST", "/v1/debug/detect"),
        ("POST", "/v1/route/that/never/existed"),
    ],
)
def test_every_v1_route_needs_the_key(method, path):
    assert _unauthorized(client.request(method, path))


def test_health_needs_no_key():
    r = client.get("/health")
    assert r.status_code in (200, 503)
    assert r.json()["status"] in ("ok", "not_ready")


def test_v1_info_is_not_public():
    assert _unauthorized(client.get("/v1/info"))


def test_unconfigured_engine_authenticates_nobody(monkeypatch):
    monkeypatch.delenv(auth.ENV_VAR, raising=False)
    assert _unauthorized(client.get("/v1/info", headers=AUTH))
    assert _unauthorized(client.get("/v1/info"))
    assert client.get("/health").status_code in (200, 503)


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_key_counts_as_unset(monkeypatch, value):
    monkeypatch.setenv(auth.ENV_VAR, value)
    assert auth.configured_key() is None
    assert _unauthorized(client.get("/v1/info", headers={"X-Engine-Key": value}))
    with pytest.raises(auth.MissingAPIKey):
        auth.load_api_key()


def test_startup_fails_loudly_without_a_key(monkeypatch):
    monkeypatch.delenv(auth.ENV_VAR, raising=False)
    with pytest.raises(auth.MissingAPIKey, match=auth.ENV_VAR), TestClient(app):
        pass


def test_startup_succeeds_with_a_key():
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_secret_comparison_is_constant_time():
    body = inspect.getsource(auth.is_valid).split('"""')[-1]
    assert "compare_digest" in body
    assert "presented == expected" not in body


@pytest.mark.parametrize(
    "presented,expected,ok",
    [
        ("k", "k", True),
        ("k", "j", False),
        (None, "k", False),
        ("k", None, False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_is_valid_truth_table(presented, expected, ok):
    assert auth.is_valid(presented, expected) is ok


def test_non_ascii_configured_key_does_not_crash_the_gate(monkeypatch):
    monkeypatch.setenv(auth.ENV_VAR, "kéy-ünicode-✓")
    assert auth.is_valid("kéy-ünicode-✓") is True
    assert auth.is_valid("kéy-ünicode-x") is False
    assert _unauthorized(client.get("/v1/info", headers={"X-Engine-Key": "key"}))
