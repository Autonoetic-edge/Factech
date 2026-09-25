"""M4 follow-up: the rate-limit source behind a reverse proxy.

Isolated and synthetic. No live endpoint, no capture, no biometric payload.

The defect this closes: `Panel.source()` used to fall back to the transport peer
whenever no trusted proxy was configured or the configured address was wrong.
Behind a proxy that peer is the same address for every visitor, so every
per-source allowance collapsed into one bucket and one caller could spend the
whole team's. The source is now either an address the server trusts itself to
have seen, or a refusal.
"""

import asyncio

import httpx
import pytest
from auth_fixtures import Harness
from facetech_auth import serve
from facetech_auth.limits import Limits, Window
from facetech_auth.panel import GUIDE_FILES, PAGE_FILES, Panel

PROXY = "10.0.0.1"
ORIGIN = "https://gateway.test"


class Repo:
    """Panel needs `run` only for the /auth/session rewrite, unused in this file."""

    async def run(self, work):  # pragma: no cover - not reached by these tests
        raise AssertionError("no database call belongs in a source-resolution test")


def page(tmp_path):
    # The page folder, with the face guide's files beside it (as apps/shared and apps/vendor are).
    for root, names in ((tmp_path / "page", PAGE_FILES), (tmp_path, GUIDE_FILES)):
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"<" + name.encode() + b">")
    sdk = tmp_path / "sdk.js"
    sdk.write_text("export{}")
    return sdk


def build(gateway, tmp_path, trusted):
    return Panel(
        gateway,
        repository=Repo(),
        origin=ORIGIN,
        sdk_bundle=page(tmp_path),
        page_root=tmp_path / "page",
        trusted_proxies=trusted,
    )


@pytest.fixture
def rig(tmp_path):
    """A gateway on a tight per-source auth window, behind a configurable panel."""
    harness = Harness()
    harness.limits.auth_source = Window(2, 60)
    harness.limits.source = Window(50, 60)

    def configured(*trusted):
        panel = build(harness.gateway, tmp_path, trusted)

        def call(peer, forwarded_for=None):
            headers = (
                {} if forwarded_for is None else {"X-Forwarded-For": forwarded_for}
            )

            async def go():
                transport = httpx.ASGITransport(app=panel, client=(peer, 40000))
                async with httpx.AsyncClient(
                    transport=transport, base_url=ORIGIN
                ) as client:
                    return await client.get("/auth/login", headers=headers)

            return asyncio.run(go())

        return call

    return configured


def test_two_clients_behind_one_trusted_proxy_hold_separate_allowances(rig):
    """The whole point: one caller must not be able to lock the others out."""
    call = rig(PROXY)
    assert [call(PROXY, "198.51.100.7").status_code for _ in range(2)] == [302, 302]
    spent = call(PROXY, "198.51.100.7")
    assert spent.status_code == 429 and spent.json()["error"] == "RATE_LIMITED"
    # A different client address behind the same proxy is untouched by that.
    assert call(PROXY, "198.51.100.8").status_code == 302
    assert call(PROXY, "198.51.100.8").status_code == 302
    assert call(PROXY, "198.51.100.8").status_code == 429


def test_the_rightmost_hop_is_used_so_a_forged_entry_cannot_spend_another_bucket(rig):
    call = rig(PROXY)
    # Two entries forged by the client sit to the left; only the proxy's counts.
    for _ in range(2):
        assert call(PROXY, "198.51.100.8, 203.0.113.5, 198.51.100.7").status_code == 302
    assert call(PROXY, "198.51.100.7").status_code == 429
    assert call(PROXY, "198.51.100.8").status_code == 302


def test_a_trusted_peer_with_no_forwarded_header_is_refused(rig):
    """No usable hop means no source this server can vouch for. No peer fallback."""
    refused = rig(PROXY)(PROXY)
    assert refused.status_code == 400
    assert refused.json()["error"] == "UNTRUSTED_CONTEXT"
    empty = rig(PROXY)(PROXY, " , ")
    assert empty.status_code == 400 and empty.json()["error"] == "UNTRUSTED_CONTEXT"


def test_a_forwarded_header_from_an_untrusted_peer_is_refused(rig):
    """How a wrong AMFATEC_TRUSTED_PROXY shows itself, instead of failing silently
    into one bucket shared by the whole audience."""
    wrong_address = rig("10.9.9.9")(PROXY, "198.51.100.7")
    assert wrong_address.status_code == 400
    assert wrong_address.json()["error"] == "UNTRUSTED_CONTEXT"
    declared_direct = rig()(PROXY, "198.51.100.7")
    assert declared_direct.status_code == 400
    assert declared_direct.json()["error"] == "UNTRUSTED_CONTEXT"


def test_a_direct_peer_with_no_proxy_configured_is_counted_as_itself(rig):
    call = rig()
    assert [call("203.0.113.5").status_code for _ in range(2)] == [302, 302]
    assert call("203.0.113.5").status_code == 429
    assert call("203.0.113.6").status_code == 302


def test_health_stays_reachable_when_the_source_cannot_be_resolved(tmp_path):
    """The container and proxy liveness probe must not be refused by this gate."""
    panel = build(Harness().gateway, tmp_path, (PROXY,))

    async def go():
        transport = httpx.ASGITransport(app=panel, client=(PROXY, 40000))
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            return await client.get("/health")

    assert asyncio.run(go()).status_code == 200


def test_source_resolution_is_the_three_declared_cases(tmp_path):
    """The rule read straight off Panel.source; None means refuse."""
    panel = build(Harness().gateway, tmp_path, (PROXY,))

    def source(peer, *forwarded):
        headers = [(b"x-forwarded-for", v.encode()) for v in forwarded]
        return panel.source({"client": (peer, 1), "headers": headers})

    assert source("203.0.113.5") == "203.0.113.5"
    assert source(PROXY, "198.51.100.7") == "198.51.100.7"
    assert source(PROXY, "203.0.113.5, 198.51.100.7") == "198.51.100.7"
    assert source(PROXY, "203.0.113.5", "198.51.100.7") == "198.51.100.7"
    assert source(PROXY) is None
    assert source("203.0.113.5", "198.51.100.7") is None
    assert panel.source({"headers": []}) == "unknown"


# ------------------------------------------------ startup: required, no default


def test_gateway_startup_stops_when_the_trusted_proxy_is_not_declared(monkeypatch):
    monkeypatch.delenv("AMFATEC_TRUSTED_PROXY", raising=False)
    with pytest.raises(SystemExit) as stop:
        serve.application("gateway")
    assert "AMFATEC_TRUSTED_PROXY" in str(stop.value)


def test_gateway_startup_passes_the_gate_when_it_is_declared_direct(monkeypatch):
    """`direct` is an explicit declaration of no proxy, so startup moves on to the
    next required variable rather than stopping here."""
    monkeypatch.setenv("AMFATEC_TRUSTED_PROXY", "direct")
    with pytest.raises(SystemExit) as stop:
        serve.application("gateway")
    assert "AMFATEC_TRUSTED_PROXY" not in str(stop.value)


def test_the_engine_does_not_require_a_trusted_proxy(monkeypatch):
    monkeypatch.delenv("AMFATEC_TRUSTED_PROXY", raising=False)
    with pytest.raises(SystemExit) as stop:
        serve.application("engine")
    assert "AMFATEC_TRUSTED_PROXY" not in str(stop.value)


@pytest.mark.parametrize("value", ["", " ", ",", " , "])
def test_a_blank_or_empty_list_is_not_a_declaration(monkeypatch, value):
    monkeypatch.setenv("AMFATEC_TRUSTED_PROXY", value)
    with pytest.raises(SystemExit) as stop:
        serve.trusted_proxies()
    assert "AMFATEC_TRUSTED_PROXY" in str(stop.value)


def test_declared_proxies_are_parsed_as_a_list(monkeypatch):
    monkeypatch.setenv("AMFATEC_TRUSTED_PROXY", "direct")
    assert serve.trusted_proxies() == ()
    monkeypatch.setenv("AMFATEC_TRUSTED_PROXY", " 10.0.0.1 , 10.0.0.2 ")
    assert serve.trusted_proxies() == ("10.0.0.1", "10.0.0.2")


def test_limits_are_still_per_source_objects_not_one_global_counter():
    limits = Limits()
    limits.source.enforce("198.51.100.7")
    assert limits.source.retry_after("198.51.100.8") == 0
