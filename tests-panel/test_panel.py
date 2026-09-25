"""The browser front door: what it serves, what it adds, and what it must not change."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from facetech_auth.panel import GUIDE_FILES, PAGE_FILES, Panel

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://amfatec.test"
ACTOR, SUBJECT = "a" * 32, "b" * 32


class Repo:
    """Answers the one query the panel makes: this actor's own subject."""

    def __init__(self, owned):
        self.owned, self.asked = owned, []

    async def run(self, operation):
        class Result:
            def __init__(self, value):
                self.value = value

            def scalar_one_or_none(self):
                return self.value

        class Connection:
            def execute(inner, statement):
                params = statement.compile().params
                self.asked.append(params)
                key = (params["tenant_1"], params["owner_actor_id_1"])
                return Result(self.owned.get(key))

        return operation(Connection())


class Gateway:
    """Stands in for HardenedApp; records exactly what reached it."""

    def __init__(self):
        self.seen = []

    async def __call__(self, scope, receive, send):
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        self.seen.append((scope["method"], scope["path"], headers))
        if scope["path"] == "/auth/session":
            status, body, extra = (
                200,
                {"actor_id": ACTOR, "tenant_id": "t1", "csrf_token": "c"},
                [],
            )
            if "cookie" not in headers:
                status, body = 401, {"error": "AUTHENTICATION_REQUIRED"}
        elif scope["path"] == "/auth/callback":
            status, body, extra = (
                303,
                {},
                [(b"location", b"/auth/session"), (b"set-cookie", b"s=1")],
            )
            if b"state=late" in scope["query_string"]:
                status, body, extra = 401, {"error": "AUTHENTICATION_REQUIRED"}, []
        else:
            status, body, extra = 200, {"ok": True}, []
        raw = json.dumps(body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode()),
                    (b"content-security-policy", b"default-src 'none'"),
                    *extra,
                ],
            }
        )
        await send({"type": "http.response.body", "body": raw})


@pytest.fixture
def rig(tmp_path):
    page = tmp_path / "page"  # the face guide sits beside the page folder, as apps/shared does
    for root, names in ((page, PAGE_FILES), (tmp_path, GUIDE_FILES)):
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"<" + name.encode() + b">")
    (page / "secret.env").write_text("nope")
    sdk = tmp_path / "sdk.js"
    sdk.write_text("export{}")
    gateway, repo = Gateway(), Repo({("t1", ACTOR): SUBJECT})
    panel = Panel(
        gateway, repository=repo, origin=ORIGIN, page_root=page, sdk_bundle=sdk
    )

    def call(method, path, **headers):
        async def go():
            transport = httpx.ASGITransport(app=panel)
            async with httpx.AsyncClient(
                transport=transport, base_url=ORIGIN
            ) as client:
                return await client.request(method, path, headers=headers)

        return asyncio.run(go())

    return call, gateway, repo


def test_page_is_served_with_camera_policy_and_a_strict_csp(rig):
    call, gateway, _ = rig
    for path in (
        "/",
        "/workspace",
        "/main.js",
        "/verify.css",
        "/logo.png",
        "/sdk/index.js",
    ):
        r = call("GET", path)
        assert r.status_code == 200, path
        assert r.headers["permissions-policy"].startswith("camera=(self)")
        csp = r.headers["content-security-policy"]
        assert (
            "script-src 'self'" in csp
            and "'unsafe-inline'" not in csp
            and "'unsafe-eval'" not in csp
            and "frame-ancestors 'none'" in csp
        )
        assert r.headers["x-content-type-options"] == "nosniff"
    assert call("GET", "/main.js").headers["content-type"].startswith("text/javascript")
    assert gateway.seen == []  # static files never touch the gateway


def test_the_face_guide_is_served_and_only_the_pinned_detector_is_cached(rig):
    call, gateway, _ = rig
    for name in GUIDE_FILES:
        r = call("GET", "/" + name)
        assert r.status_code == 200 and r.content == b"<" + name.encode() + b">", name
        assert ("immutable" in r.headers["cache-control"]) == name.startswith("vendor/")
    wasm = call("GET", "/vendor/mediapipe/wasm/vision_wasm_internal.wasm")
    assert wasm.headers["content-type"] == "application/wasm"  # browsers refuse to compile anything else
    assert "'wasm-unsafe-eval'" in call("GET", "/").headers["content-security-policy"]
    assert call("GET", "/main.js").headers["cache-control"] == "no-cache"
    assert gateway.seen == []


def test_only_listed_files_exist(rig):
    call, gateway, _ = rig
    for path in (
        "/secret.env",
        "/../secret.env",
        "/index.html",
        "/sdk/internal.js",
        "/package.json",
        "/shared/framing.js",
        "/vendor/fetch.sh",
    ):
        call("GET", path)
    assert [p for _, p, _ in gateway.seen] == [
        "/secret.env",
        "/secret.env",
        "/index.html",
        "/sdk/internal.js",
        "/package.json",
        "/shared/framing.js",
        "/vendor/fetch.sh",
    ]
    # ...where the real gateway answers NOT_FOUND; nothing was read from disk by the panel.


def test_page_is_refused_on_the_wrong_host(rig):
    call, _, _ = rig
    assert call("GET", "/", host="evil.test").status_code == 400


def test_session_gains_only_the_callers_own_subject(rig):
    call, _, repo = rig
    body = call("GET", "/auth/session", cookie="s=1").json()
    assert body["subject_id"] == SUBJECT and body["csrf_token"] == "c"
    assert repo.asked == [
        {"tenant_1": "t1", "kind_1": "subject", "owner_actor_id_1": ACTOR}
    ]


def test_signed_out_session_is_passed_through_untouched(rig):
    call, _, repo = rig
    r = call("GET", "/auth/session")
    assert r.status_code == 401 and "subject_id" not in r.json() and repo.asked == []


def test_account_without_a_subject_gets_null_not_someone_elses(rig):
    call, _, repo = rig
    repo.owned.clear()
    assert call("GET", "/auth/session", cookie="s=1").json()["subject_id"] is None


def test_sign_in_lands_on_the_page_and_keeps_the_session_cookie(rig):
    call, _, _ = rig
    r = call("GET", "/auth/callback?state=x&code=y")
    assert (
        r.status_code == 303
        and r.headers["location"] == "/"
        and r.headers["set-cookie"] == "s=1"
    )


def test_an_expired_sign_in_window_restarts_sign_in_once(rig):
    call, _, _ = rig
    late = "/auth/callback?state=late&code=y"
    r = call("GET", late)  # login cookie expired in the browser
    assert r.status_code == 302 and r.headers["location"] == "/auth/login"
    r = call("GET", late, cookie="__Host-facetech-login=b")  # fresh cookie: no loop
    assert r.status_code == 401 and r.json() == {"error": "AUTHENTICATION_REQUIRED"}


def test_origin_is_added_only_for_a_same_origin_challenge_get(rig):
    call, gateway, _ = rig
    path = f"/v2/subjects/{SUBJECT}/challenge"
    call("GET", path, **{"sec-fetch-site": "same-origin"})
    call("GET", path, **{"sec-fetch-site": "cross-site"})
    call("GET", path)
    call("GET", path, origin="https://evil.test", **{"sec-fetch-site": "same-origin"})
    call("POST", f"/v2/subjects/{SUBJECT}/verify", **{"sec-fetch-site": "same-origin"})
    origins = [h.get("origin") for _, _, h in gateway.seen]
    assert origins == [ORIGIN, None, None, "https://evil.test", None]


def test_proxy_headers_never_reach_the_gateway(rig):
    call, gateway, _ = rig
    call(
        "GET",
        "/v2/info",
        **{
            "x-forwarded-for": "1.2.3.4",
            "x-forwarded-proto": "https",
            "forwarded": "for=1.2.3.4",
            "x-real-ip": "1.2.3.4",
        },
    )
    seen = gateway.seen[0][2]
    assert not [
        k for k in seen if k.startswith(("x-forwarded", "forwarded", "x-real-ip"))
    ]


def test_gateway_responses_keep_the_gateways_own_csp(rig):
    call, _, _ = rig
    assert (
        call("GET", "/v2/info").headers["content-security-policy"]
        == "default-src 'none'"
    )


def test_real_page_files_all_exist_and_carry_no_inline_code():
    page = ROOT / "apps" / "verify"
    for name in PAGE_FILES:
        assert (page / name).is_file(), name
    for name in GUIDE_FILES:
        assert (page.parent / name).is_file(), name
    for name in ("index.html", "workspace.html"):
        html = (page / name).read_text(encoding="utf-8")
        assert (
            " style=" not in html and "onclick" not in html and "<script>" not in html
        )
        assert "javascript:" not in html
