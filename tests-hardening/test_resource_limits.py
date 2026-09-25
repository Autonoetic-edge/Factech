"""M4 S01-S03: streaming body bounds, principal/source limits, admission control.

Isolated and synthetic. No live endpoint, no biometric payload, no real capture:
the bodies here are filler bytes and refused msgpack, and every assertion is about
the HTTP boundary, never about recognition or presentation-attack behavior.
"""

import asyncio
import tracemalloc

import pytest
from auth_fixtures import Harness, run, scan_request
from facetech_auth.contracts import Denied
from facetech_auth.http import SOURCE_KEY, create_gateway
from facetech_auth.limits import (
    BODY_LIMIT,
    BODY_SECONDS,
    Admission,
    Limits,
    Window,
)
from facetech_auth.sessions import COOKIE

SCAN_PATH = "/v2/subjects/subject-alice/enroll"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def h():
    """A harness on the shipping policy, not the relaxed default.

    Gateway and engine get separate limiters because they are separate processes
    in the deployed profile; sharing one object here would double-count every
    proxied scan and measure a limit that does not exist.
    """
    harness = Harness()
    harness.limits = Limits()
    harness.engine.limits = Limits()
    harness.gateway.limits = harness.limits
    return harness


def body_stream(total, chunk=64 * 1024, delay=0.0):
    """An ASGI receive() that never declares a length: chunked, like a real upload."""
    sent = 0

    async def receive():
        nonlocal sent
        if delay:
            await asyncio.sleep(delay)
        if sent >= total:
            return {"type": "http.request", "body": b"", "more_body": False}
        size = min(chunk, total - sent)
        sent += size
        return {"type": "http.request", "body": b"\0" * size, "more_body": True}

    return receive


async def raw_call(app, receive, *, headers=(), path=SCAN_PATH, method="POST"):
    """Drive the ASGI app directly so the body can be shaped byte by byte."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "client": ("203.0.113.9", 51000),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    start, chunks = {}, []

    async def send(message):
        if message["type"] == "http.response.start":
            start.update(message)
        else:
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return start["status"], b"".join(chunks)


def browser_headers(h, browser, extra=()):
    return (
        ("host", "gateway.test"),
        ("cookie", COOKIE + "=" + browser.cookie),
        ("origin", h.config.origin),
        ("x-csrf-token", browser.csrf),
        ("content-type", "application/msgpack"),
        ("idempotency-key", "0123456789abcdef0123456789abcdef"),
        *extra,
    )


# ---------------------------------------------------------------- S01 bodies


@pytest.mark.parametrize("engine", [False, True])
def test_s01_body_over_the_cap_is_refused_without_a_declared_length(h, engine):
    browser = run(h.login())
    app = h.engine if engine else h.gateway
    headers = (
        (
            ("host", "engine.test"),
            ("authorization", "Bearer " + browser.token),
            ("x-engine-key", h.config.engine_key),
            ("x-facetech-session", browser.session_id),
            ("content-type", "application/msgpack"),
            ("idempotency-key", "0123456789abcdef0123456789abcdef"),
        )
        if engine
        else browser_headers(h, browser)
    )
    status, body = run(raw_call(app, body_stream(BODY_LIMIT * 8), headers=headers))
    assert status == 413, body
    assert b"PAYLOAD_TOO_LARGE" in body
    assert not h.engine_ops.calls and not h.gateway_ops.calls


def test_s01_a_lying_content_length_does_not_raise_the_cap(h):
    browser = run(h.login())
    status, body = run(
        raw_call(
            h.gateway,
            body_stream(BODY_LIMIT * 4),
            headers=browser_headers(h, browser, (("content-length", "10"),)),
        )
    )
    assert status == 413 and b"PAYLOAD_TOO_LARGE" in body


def test_s01_a_declared_length_over_the_cap_is_refused_before_the_body(h):
    browser = run(h.login())
    read = {"chunks": 0}

    async def receive():
        read["chunks"] += 1
        return {"type": "http.request", "body": b"\0" * 1024, "more_body": True}

    status, body = run(
        raw_call(
            h.gateway,
            receive,
            headers=browser_headers(
                h, browser, (("content-length", str(BODY_LIMIT + 1)),)
            ),
        )
    )
    assert status == 413 and b"PAYLOAD_TOO_LARGE" in body
    assert read["chunks"] == 0, "the body was read despite an over-cap declaration"


def test_s01_peak_memory_stays_near_the_cap_for_a_large_upload(h):
    """The recorded S01 memory number: peak bytes held while a 15 MB upload
    is refused. Bound is the cap plus transport chunks, not the upload size."""
    browser = run(h.login())
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        status, _ = run(
            raw_call(
                h.gateway,
                body_stream(15 * 1024 * 1024),
                headers=browser_headers(h, browser),
            )
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert status == 413
    assert peak < 3 * BODY_LIMIT, f"peak {peak} bytes held for a 15 MB upload"


def test_s01_a_slow_body_is_released_on_the_deadline(h, monkeypatch):
    monkeypatch.setattr("facetech_auth.http.BODY_SECONDS", 0.2)
    browser = run(h.login())
    status, body = run(
        raw_call(
            h.gateway,
            body_stream(BODY_LIMIT, chunk=256, delay=0.05),
            headers=browser_headers(h, browser),
        )
    )
    assert status == 408 and b"REQUEST_TIMEOUT" in body
    assert not h.gateway_ops.calls


def test_s01_a_client_disconnect_ends_the_request_without_operations(h):
    browser = run(h.login())

    async def receive():
        return {"type": "http.disconnect"}

    status, body = run(
        raw_call(h.gateway, receive, headers=browser_headers(h, browser))
    )
    assert status == 400 and b"MALFORMED_REQUEST" in body
    assert not h.gateway_ops.calls and not h.engine_ops.calls


def test_s01_the_deadline_is_a_positive_bound():
    assert 0 < BODY_SECONDS <= 60 and BODY_LIMIT == 481_964


# ------------------------------------------------- S02 principal and source


def test_s02_scan_limit_is_per_principal_and_survives_new_sessions(h):
    browser = run(h.login())
    seen = []
    for _ in range(h.limits.scan.limit):
        seen.append(run(h.request("POST", SCAN_PATH, browser, **scan_request())))
    assert all(r.status_code != 429 for r in seen), [r.status_code for r in seen]
    refused = run(h.request("POST", SCAN_PATH, browser, **scan_request()))
    assert refused.status_code == 429
    assert refused.json()["error"] == "RATE_LIMITED"
    assert int(refused.headers["Retry-After"]) >= 1
    # A brand new session for the same account does not reset the counter.
    again = run(h.login())
    assert again.cookie != browser.cookie
    churned = run(h.request("POST", SCAN_PATH, again, **scan_request()))
    assert churned.status_code == 429


def test_s02_a_peer_tenant_keeps_its_own_allowance(h):
    alice = run(h.login())
    for _ in range(h.limits.scan.limit):
        run(h.request("POST", SCAN_PATH, alice, **scan_request()))
    assert run(h.request("POST", SCAN_PATH, alice, **scan_request())).status_code == 429
    carol = run(h.login("carol"))  # team-b
    response = run(
        h.request(
            "POST",
            "/v2/subjects/subject-carol/verify",
            carol,
            **scan_request(subject="subject-carol"),
        )
    )
    assert response.status_code != 429


def test_s02_spoofed_forwarded_headers_cannot_pick_the_source(h):
    browser = run(h.login())
    for header in ("x-forwarded-for", "forwarded", "x-forwarded-host"):
        response = run(
            h.request(
                "GET",
                "/v2/info",
                browser,
                headers={header: "198.51.100.7"},
            )
        )
        assert response.status_code == 400
        assert response.json()["error"] == "UNTRUSTED_CONTEXT"


def test_s02_the_source_key_is_the_transport_peer_by_default(h):
    """With nothing in the scope the limiter counts the peer address."""
    browser = run(h.login())
    h.limits.source = Window(2, 60)
    headers = (
        ("host", "gateway.test"),
        ("cookie", COOKIE + "=" + browser.cookie),
    )

    async def empty():
        return {"type": "http.request", "body": b"", "more_body": False}

    codes = [
        run(raw_call(h.gateway, empty, headers=headers, path="/v2/info", method="GET"))[
            0
        ]
        for _ in range(4)
    ]
    assert codes[-1] == 429 and 200 in codes


def test_s02_unauthenticated_scans_never_reach_inference(h):
    for engine in (False, True):
        response = run(
            h.request("POST", SCAN_PATH, None, engine=engine, **scan_request())
        )
        assert response.status_code == 401
    assert not h.engine_ops.calls and not h.gateway_ops.calls


def test_s02_auth_routes_have_their_own_unauthenticated_source_bound(h):
    h.limits.auth_source = Window(3, 60)

    async def empty():
        return {"type": "http.request", "body": b"", "more_body": False}

    codes = [
        run(
            raw_call(
                h.gateway,
                empty,
                headers=(("host", "gateway.test"),),
                path="/auth/login",
                method="GET",
            )
        )[0]
        for _ in range(5)
    ]
    assert codes[:3] == [302, 302, 302] and codes[3:] == [429, 429]


def test_s02_health_stays_reachable_under_a_flood(h):
    h.limits.source = Window(1, 60)

    async def empty():
        return {"type": "http.request", "body": b"", "more_body": False}

    for _ in range(5):
        status, _ = run(
            raw_call(
                h.gateway,
                empty,
                headers=(("host", "gateway.test"),),
                path="/health",
                method="GET",
            )
        )
        assert status == 200


# --------------------------------------------------------- S03 admission


def test_s03_window_refuses_and_then_recovers_on_its_own_clock():
    clock = Clock()
    window = Window(2, 60, clock=clock)
    assert window.retry_after("a") == 0 and window.retry_after("a") == 0
    assert window.retry_after("a") == 60
    assert window.retry_after("b") == 0, "a second key has its own allowance"
    clock.now += 61
    assert window.retry_after("a") == 0


def test_s03_window_key_table_is_bounded():
    window = Window(5, 60, keys=8)
    for index in range(500):
        window.retry_after(f"source-{index}")
    assert len(window._hits) <= 8


def test_s03_admission_refuses_with_busy_and_retry_after():
    gate = Admission(1)

    async def scenario():
        async with gate.hold():
            with pytest.raises(Denied) as refused:
                async with gate.hold():
                    pass
        return refused.value

    error = run(scenario())
    assert (error.code, error.status) == ("BUSY", 503)
    assert error.retry_after >= 1
    assert gate.active == 0, "the gate did not release"


def test_s03_admission_releases_when_the_held_work_fails():
    gate = Admission(1)

    async def scenario():
        with pytest.raises(ValueError):
            async with gate.hold():
                raise ValueError("synthetic failure inside the held work")
        async with gate.hold():
            return gate.active

    assert run(scenario()) == 1
    assert gate.active == 0


def test_s03_concurrent_scans_over_the_gateway_bound_get_busy(h):
    h.limits.scans = Admission(1)
    started = asyncio.Event()
    release = asyncio.Event()

    class Slow:
        recording = None
        calls = []

        async def execute(self, access, payload, reauthorize):
            started.set()
            await release.wait()
            await reauthorize()
            return {"synthetic_authorized": True}

    h.engine.operations = Slow()

    async def scenario():
        browser = await h.login()
        first = asyncio.create_task(
            h.request("POST", SCAN_PATH, browser, **scan_request())
        )
        await asyncio.wait_for(started.wait(), 5)
        second = await h.request("POST", SCAN_PATH, browser, **scan_request())
        release.set()
        return second, await first

    refused, accepted = run(scenario())
    assert refused.status_code == 503 and refused.json()["error"] == "BUSY"
    assert int(refused.headers["Retry-After"]) >= 1
    assert accepted.status_code == 200
    assert h.limits.scans.active == 0


def test_s03_the_engines_busy_retry_after_reaches_the_client(h):
    """A 503 BUSY raised downstream keeps its back-off advice through the proxy."""

    class Busy:
        recording = None
        calls = []

        async def execute(self, access, payload, reauthorize):
            raise Denied("BUSY", 503, "engine is at capacity; retry shortly", 2)

    h.engine.operations = Busy()
    browser = run(h.login())
    response = run(h.request("POST", SCAN_PATH, browser, **scan_request()))
    assert response.status_code == 503 and response.json()["error"] == "BUSY"
    assert response.headers["Retry-After"] == "2"


def test_s03_a_refused_request_is_refused_before_any_recording_or_engine_call(h):
    browser = run(h.login())
    h.limits.scan = Window(0, 60)
    response = run(h.request("POST", SCAN_PATH, browser, **scan_request()))
    assert response.status_code == 429
    assert not h.gateway_ops.calls and not h.engine_ops.calls


def test_s03_limits_are_shared_by_every_request_of_one_app_instance():
    """Two apps built separately must not share a limiter by accident."""
    first, second = Limits(), Limits()
    first.actor.enforce("actor")
    assert second.actor.retry_after("actor") == 0
    assert first.actor is not second.actor


class _FakeRequest:
    def __init__(self, scope):
        self.scope = scope


def test_s03_the_source_scope_key_is_not_settable_by_a_header(h):
    browser = run(h.login())
    # A header of that name is just an unknown header; it never becomes the key.
    response = run(
        h.request("GET", "/v2/info", browser, headers={SOURCE_KEY: "1.2.3.4"})
    )
    assert response.status_code == 200
    peer_only = _FakeRequest({"client": ("198.51.100.1", 1)})
    assert h.gateway.source(peer_only) == "198.51.100.1"
    proxied = _FakeRequest({"client": ("10.0.0.1", 1), SOURCE_KEY: "198.51.100.2"})
    assert h.gateway.source(proxied) == "198.51.100.2"


def test_s03_limits_can_be_injected_and_are_not_a_module_global():
    harness = Harness()
    tight = Limits()
    tight.actor.limit = 1
    gateway = create_gateway(harness.gateway_sessions, limits=tight)
    assert gateway.limits is tight
