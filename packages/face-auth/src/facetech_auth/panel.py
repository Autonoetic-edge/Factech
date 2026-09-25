"""Browser front door for the hardened gateway: the AmFatec face-check page.

Wraps the gateway ASGI app without changing it. The gateway stays deny-by-default
and keeps its own CSP; this layer adds only what a real browser needs:

* an explicit allowlist of static files (no directory serving, no fallback) with a
  page CSP and ``Permissions-Policy: camera=(self)``. The list includes the on-page
  face guide (``/shared``) and its pinned MediaPipe detector (``/vendor``); that
  detector is WebAssembly, which is the only reason the CSP carries
  ``'wasm-unsafe-eval'``. It allows compiling WebAssembly, not ``eval`` of script;
* ``subject_id`` on ``GET /auth/session`` so the page can address the signed-in
  account's own subject. It is an address, not an authority: every /v2 call is
  still authorized by the gateway against the session;
* after sign-in, a redirect to the page instead of the JSON session document;
* ``Origin`` on the same-origin challenge GET. Browsers omit Origin on same-origin
  GETs, so the gateway's CSRF origin check could never pass from a page. The
  browser-set, script-unforgeable ``Sec-Fetch-Site: same-origin`` is the same
  signal; the CSRF token is still required by the gateway;
* removal of reverse-proxy ``Forwarded``/``X-Forwarded-*`` headers, which nothing
  downstream may trust and the gateway rejects outright.
"""

import json
import re
from pathlib import Path

import sqlalchemy as sa

from . import schema as s
from .http import SOURCE_KEY
from .sessions import LOGIN_COOKIE

PAGE_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "permissions-policy": "camera=(self), microphone=(), geolocation=(), display-capture=()",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "cache-control": "no-cache",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".mjs": "text/javascript; charset=utf-8",
    ".wasm": "application/wasm",
    ".tflite": "application/octet-stream",
}
PAGE_FILES = (
    "index.html", "workspace.html", "verify.css", "logo.png", "main.js", "workspace.js",
    "bridge.js", "cues.js", "flow.js", "framing.js", "head-guide.js", "outcome.js", "view.js",
)  # fmt: skip
# The face guide, served from beside the page folder (apps/shared, apps/vendor), unchanged.
GUIDE_FILES = (
    "shared/face-guide.js", "shared/face-detector.js",
    "shared/messages.js",  # the engine's liveness message parser, used by outcome.js
    "vendor/mediapipe/vision_bundle.mjs", "vendor/mediapipe/blaze_face_short_range.tflite",
    "vendor/mediapipe/wasm/vision_wasm_internal.js", "vendor/mediapipe/wasm/vision_wasm_internal.wasm",
)  # fmt: skip
# The detector is 10 MB and pinned by hash (apps/vendor/fetch.sh): fetch it once, not per visit.
VENDOR_CACHE = "public, max-age=2592000, immutable"
CHALLENGE = re.compile(r"/v2/subjects/[A-Za-z0-9_-]{1,128}/challenge\Z")
DROPPED = (b"forwarded", b"x-forwarded-", b"x-real-ip")


def static_table(page_root, sdk_bundle):
    """URL path -> (bytes, content type). Read once at start; a missing file is a startup error."""
    root = Path(page_root)
    table = {"/" + name: root / name for name in PAGE_FILES}
    table.update({"/" + name: root.parent / name for name in GUIDE_FILES})
    table["/"] = table.pop("/index.html")
    table["/workspace"] = table.pop("/workspace.html")
    table["/sdk/index.js"] = Path(sdk_bundle)
    return {url: (path.read_bytes(), TYPES[path.suffix]) for url, path in table.items()}


class Panel:
    def __init__(
        self,
        gateway,
        *,
        repository,
        origin,
        page_root,
        sdk_bundle,
        trusted_proxies=(),
    ):
        if not origin.startswith("https://"):
            raise ValueError("HTTPS origin required")
        self.gateway, self.repo, self.origin = gateway, repository, origin
        self.static = static_table(page_root, sdk_bundle)
        self.trusted_proxies = frozenset(trusted_proxies)

    def source(self, scope):
        """Who the rate limiter counts against, or None to refuse the request.

        Three cases, and nothing falls through to a default:

        * an untrusted peer sending no ``X-Forwarded-For`` — the peer itself;
        * a trusted proxy sending a non-empty ``X-Forwarded-For`` — its
          rightmost entry, the address that proxy itself observed. A client's
          own forged entries stay to its left and are never used;
        * anything else — refused. That is a trusted proxy that sent no usable
          hop, and an ``X-Forwarded-For`` from a peer not configured as trusted,
          which is how a wrong ``AMFATEC_TRUSTED_PROXY`` shows itself.

        There is deliberately no peer fallback: behind a proxy the peer is the
        same address for every visitor, so counting it would put the whole
        audience in one bucket and let one caller lock everyone out.
        The header is dropped from the forwarded request in every case.
        """
        peer = (scope.get("client") or ("unknown",))[0]
        hops = [
            hop.strip()
            for name, value in scope["headers"]
            if name.lower() == b"x-forwarded-for"
            for hop in value.decode("latin1").split(",")
            if hop.strip()
        ]
        if peer in self.trusted_proxies:
            return hops[-1] if hops else None
        return None if hops else peer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.gateway(scope, receive, send)
        source = self.source(scope)
        headers = [
            (k, v) for k, v in scope["headers"] if not k.lower().startswith(DROPPED)
        ]
        names = {k.lower(): v for k, v in headers}
        path, method = scope["path"], scope["method"]

        if method == "GET" and path in self.static:
            trusted = (
                scope.get("scheme") == "https"
                and b"https://" + names.get(b"host", b"") == self.origin.encode()
            )
            cache = VENDOR_CACHE if path.startswith("/vendor/") else None
            return await self.file(
                send, *(self.static[path] if trusted else (None, None)), cache
            )

        if (
            method == "GET"
            and CHALLENGE.fullmatch(path)
            and b"origin" not in names
            and names.get(b"sec-fetch-site") == b"same-origin"
        ):
            headers.append((b"origin", self.origin.encode()))
        scope = {**scope, "headers": headers, SOURCE_KEY: source}

        if method == "GET" and path in {"/auth/session", "/auth/callback"}:
            return await self.rewritten(scope, receive, send, path)
        return await self.gateway(scope, receive, send)

    async def file(self, send, content=None, media=None, cache=None):
        status = 200 if content is not None else 400
        body = content if content is not None else b""
        head = dict(PAGE_HEADERS)
        if media:
            head["content-type"] = media
        if cache and content is not None:
            head["cache-control"] = cache
        head["content-length"] = str(len(body))
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(k.encode(), v.encode()) for k, v in head.items()],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def rewritten(self, scope, receive, send, path):
        start, chunks = {}, []

        async def capture(message):
            if message["type"] == "http.response.start":
                start.update(message)
            else:
                chunks.append(message.get("body", b""))

        await self.gateway(scope, receive, capture)
        body, headers = b"".join(chunks), list(start.get("headers", []))
        if path == "/auth/callback" and start.get("status") == 303:
            headers = [
                (k, b"/" if k.lower() == b"location" and v == b"/auth/session" else v)
                for k, v in headers
            ]
        elif (
            path == "/auth/callback"
            and start.get("status") == 401
            and LOGIN_COOKIE.encode() + b"=" not in dict(scope["headers"]).get(b"cookie", b"")
        ):
            # The 5-minute sign-in window ran out (a slow sign-up form) and the browser
            # dropped the login cookie. The provider session is still live, so start
            # again. The fresh cookie makes a second failure show instead of looping.
            start["status"], body = 302, b""
            headers = [(b"location", b"/auth/login"), (b"content-length", b"0")]
        elif path == "/auth/session" and start.get("status") == 200:
            document = json.loads(body)
            document["subject_id"] = await self.subject(
                document["tenant_id"], document["actor_id"]
            )
            body = json.dumps(document).encode()
            headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def subject(self, tenant, actor):
        """The actor's own active subject, or None (a reviewer/admin has none)."""

        def find(connection):
            return connection.execute(
                sa.select(s.resources.c.id).where(
                    s.resources.c.tenant == tenant,
                    s.resources.c.kind == "subject",
                    s.resources.c.owner_actor_id == actor,
                    s.resources.c.active.is_(True),
                )
            ).scalar_one_or_none()

        return await self.repo.run(find)
