"""Separate ASGI gateway/engine factories. No legacy app/model/store imports.

All dispatch goes through the immutable explicit registry. There is no wildcard
proxy, static fallback, auto-HEAD, framework documentation or default repository.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qsl

import httpx
import msgpack
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .config import verified_tls
from .contracts import Access, Audit, Denied, Unavailable, UnconfiguredOperations
from .limits import BODY_LIMIT, BODY_SECONDS, Limits
from .operations import FrameBody
from .policy import Action, authorize
from .sessions import COOKIE, LOGIN_COOKIE, Sessions, digest


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    action: str
    kind: str = ""
    mutates: bool = False
    engine: bool = True


ROUTES = (
    Route("GET", "/health", "health"),
    Route("GET", "/v2/info", "info"),
    Route("GET", "/engine-status", "readiness", engine=False),
    Route("GET", "/auth/session", "session", engine=False),
    Route("GET", "/auth/login", "login", engine=False),
    Route("GET", "/auth/register", "register", engine=False),
    Route("GET", "/auth/options", "auth_options", engine=False),
    Route("GET", "/auth/callback", "callback", engine=False),
    Route("POST", "/auth/logout", "logout", mutates=True, engine=False),
    Route("POST", "/auth/backchannel-logout", "backchannel", engine=False),
    Route("GET", "/v2/subjects/{id}/challenge", "challenge", "subject", True),
    Route("POST", "/v2/subjects/{id}/enroll", Action.ENROLL, "subject", True),
    Route("POST", "/v2/subjects/{id}/templates", Action.ADD_TEMPLATE, "subject", True),
    Route("GET", "/v2/subjects/{id}/templates", Action.LIST_TEMPLATES, "subject"),
    Route(
        "DELETE",
        "/v2/subjects/{id}/templates",
        Action.REVOKE_TEMPLATES,
        "subject",
        True,
    ),
    Route("POST", "/v2/subjects/{id}/verify", Action.VERIFY, "subject", True),
    Route("POST", "/v2/subjects/{id}/liveness", Action.LIVENESS, "subject", True),
    Route(
        "POST",
        "/v2/subjects/{id}/consents/{scope}",
        Action.GRANT_CONSENT,
        "subject",
        True,
    ),
    Route(
        "DELETE",
        "/v2/subjects/{id}/consents/{scope}",
        Action.WITHDRAW_CONSENT,
        "subject",
        True,
    ),
    Route("GET", "/v2/receipts/{id}", Action.READ_RECEIPT, "receipt", engine=False),
    Route(
        "GET", "/v2/rounds/{id}/captures", Action.LIST_CAPTURES, "round", engine=False
    ),
    Route(
        "GET",
        "/v2/rounds/{id}/capture-status",
        Action.READ_AGGREGATES,
        "round",
        engine=False,
    ),
    Route("GET", "/v2/captures/{id}", Action.READ_CAPTURE, "capture", engine=False),
    Route(
        "GET",
        "/v2/captures/{id}/diagnostics",
        Action.READ_DIAGNOSTICS,
        "capture",
        engine=False,
    ),
    Route(
        "GET",
        "/v2/captures/{id}/frames/{index}",
        Action.READ_FRAME,
        "capture",
        engine=False,
    ),
    Route(
        "DELETE",
        "/v2/captures/{id}",
        Action.REQUEST_CAPTURE_DELETION,
        "capture",
        True,
        False,
    ),
)
PRECHECK_HEADER = "X-Facetech-Precheck"
PRECHECK_MAX = 256
SCAN_ACTIONS = {Action.ENROLL, Action.ADD_TEMPLATE, Action.VERIFY, Action.LIVENESS}
AUTH_ACTIONS = {"login", "register", "callback", "logout", "backchannel"}
SOURCE_KEY = "facetech.source"  # set by the trusted-proxy layer, never a header
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
FORBIDDEN = {
    "forwarded",
    "x-actor-id",
    "x-tenant-id",
    "x-role",
    "x-roles",
    "x-auth-context",
}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Denied("MALFORMED_REQUEST", 400)
        result[key] = value
    return result


def route_pattern(path):
    pattern = re.escape(path)
    for name in ("id", "scope", "index"):
        pattern = pattern.replace(
            re.escape("{" + name + "}"), f"(?P<{name}>[A-Za-z0-9_-]{{1,128}})"
        )
    return re.compile(pattern + r"\Z")


class HardenedApp:
    def __init__(
        self,
        sessions: Sessions,
        *,
        engine: bool,
        operations=None,
        engine_transport=None,
        engine_tls_context=None,
        limits=None,
    ):
        if sessions.repo is None:
            raise ValueError("Explicit shared repository required")
        self.sessions, self.engine = sessions, engine
        self.limits = limits if limits is not None else Limits()
        self.operations = (
            operations if operations is not None else UnconfiguredOperations()
        )
        self.engine_transport = engine_transport
        self.engine_tls = verified_tls(engine_tls_context)
        self.routes = tuple(r for r in ROUTES if not engine or r.engine)
        self._registry = tuple((r, route_pattern(r.path)) for r in self.routes)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        request_id = secrets.token_hex(16)
        context = {"actor": None, "action": "route.denied", "resource": None}
        try:
            response = await self.dispatch(request, request_id, context)
        except Denied as exc:
            try:
                await self.audit(context, request_id, "denied:" + exc.code)
            except Denied:
                exc = Unavailable()
            body = {"error": exc.code, "request_id": request_id}
            if exc.message:  # allowlisted frozen engine reason text only
                body["message"] = exc.message
            response = JSONResponse(body, status_code=exc.status)
            if exc.retry_after:
                response.headers["Retry-After"] = str(exc.retry_after)
        except (
            ClientDisconnect,
            ValueError,
            TypeError,
            UnicodeError,
            msgpack.UnpackException,
        ):
            try:
                await self.audit(context, request_id, "denied:MALFORMED_REQUEST")
                response = JSONResponse(
                    {"error": "MALFORMED_REQUEST", "request_id": request_id},
                    status_code=400,
                )
            except Denied:
                response = JSONResponse(
                    {"error": "DEPENDENCY_UNAVAILABLE", "request_id": request_id},
                    status_code=503,
                )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
                "X-Request-Id": request_id,
            }
        )
        await response(scope, receive, send)

    async def audit(self, context, request_id, outcome):
        await self.sessions.repo.audit(
            Audit(
                request_id,
                context["actor"],
                context["action"],
                context["resource"],
                outcome,
                int(self.sessions.clock()),
            )
        )

    def select(self, request):
        raw = request.scope.get("raw_path", request.url.path.encode()).split(b"?", 1)[0]
        if (
            not raw.isascii()
            or b"%" in raw
            or b"\\" in raw
            or b"//" in raw
            or any(p in {b".", b".."} for p in raw.split(b"/"))
            or raw.decode() != request.url.path
        ):
            raise Denied("NOT_FOUND", 404)
        for route, pattern in self._registry:
            match = pattern.fullmatch(request.url.path)
            if route.method == request.method and match:
                return route, match.groupdict()
        raise Denied("NOT_FOUND", 404)

    def headers(self, request):
        headers = unique_pairs((k.lower(), v) for k, v in request.scope["headers"])
        for raw in headers:
            name = raw.decode("ascii")
            if name in FORBIDDEN or name.startswith("x-forwarded-"):
                raise Denied("UNTRUSTED_CONTEXT", 400)
            if not self.engine and name in {
                "authorization",
                "x-engine-key",
                "x-facetech-session",
            }:
                raise Denied("UNTRUSTED_CONTEXT", 400)
        config = self.sessions.provider.config
        origin = config.engine_origin if self.engine else config.origin
        if (
            request.scope["scheme"] != "https"
            or f"https://{request.headers.get('host')}" != origin
        ):
            raise Denied("UNTRUSTED_ORIGIN", 400)
        # Reject ambiguous cookie parsing rather than letting first/last win.
        cookies = request.headers.get("cookie", "")
        if cookies:
            unique_pairs(
                tuple(part.strip().split("=", 1))
                for part in cookies.split(";")
                if "=" in part
            )

    def origin(self, request):
        if request.headers.get("origin") != self.sessions.provider.config.origin:
            raise Denied("CSRF_REQUIRED", 403)

    async def read_body(self, request, limit=BODY_LIMIT):
        """Stream with a hard byte cap and a whole-body deadline.

        The cap is applied to bytes actually received, so a false, absent or
        chunked Content-Length changes nothing; a declared length over the cap is
        merely refused one round trip earlier. Peak resident body bytes are the
        cap plus one transport chunk. A stalled or disconnected client releases
        the buffer on the way out.
        """
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise Denied("PAYLOAD_TOO_LARGE", 413)
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_SECONDS):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise Denied("PAYLOAD_TOO_LARGE", 413)
        except TimeoutError:
            body.clear()
            raise Denied("REQUEST_TIMEOUT", 408) from None
        return bytes(body)

    async def authenticate(self, request):
        if not self.engine:
            return await self.sessions.authenticate(cookie=request.cookies.get(COOKIE))
        key = request.headers.get("x-engine-key", "")
        # Rotation overlap: both keys are compared in full, no early exit.
        accepted = False
        for candidate in self.sessions.provider.config.engine_keys:
            accepted |= hmac.compare_digest(key, candidate)
        if not accepted:
            raise Denied()
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise Denied()
        return await self.sessions.authenticate(
            session_id=request.headers.get("x-facetech-session"),
            access_token=authorization[7:],
        )

    def source(self, request):
        """The server's own view of the caller: the transport peer, or the
        address a configured trusted proxy observed. Never a request header.

        An explicit ``None`` is the trusted-proxy layer saying it cannot name a
        source it trusts. Refuse: counting such a request would put it in a
        bucket the whole audience shares. ``/health`` never reaches here."""
        if request.scope.get(SOURCE_KEY, "") is None:
            raise Denied("UNTRUSTED_CONTEXT", 400)
        client = request.scope.get("client") or ("unknown",)
        return request.scope.get(SOURCE_KEY) or client[0]

    async def dispatch(self, request, request_id, context):
        self.headers(request)
        route, params = self.select(request)
        context["action"] = route.action
        # Per-source limits belong to the public edge only. The engine's single
        # caller is the gateway, so a source limit there would be a global cap;
        # the engine relies on the per-principal limits below. /health is exempt:
        # it reads no state and is the container and proxy liveness probe.
        if not self.engine and route.action != "health":
            source = self.source(request)
            self.limits.source.enforce(source)
            if route.action in AUTH_ACTIONS:
                self.limits.auth_source.enforce(source)
        query = unique_pairs(parse_qsl(request.url.query, keep_blank_values=True))
        if route.action == "auth_options":
            if query:
                raise Denied("MALFORMED_REQUEST", 400)
            return JSONResponse(
                {"registration": self.sessions.registration is not None}
            )
        if route.action == "health":
            return JSONResponse({"status": "ok"})
        if route.action in {"login", "register", "callback", "logout", "backchannel"}:
            return await self.auth_route(
                route.action, request, query, request_id, context
            )
        auth = await self.authenticate(request)
        context["actor"] = auth.principal.actor_id
        # Keyed on the durable actor id, so a new session, a new cookie or a
        # sibling tenant neither resets nor consumes this principal's allowance.
        self.limits.actor.enforce(auth.principal.actor_id)
        if route.action in SCAN_ACTIONS:
            self.limits.scan.enforce(auth.principal.actor_id)
        if not self.engine and route.mutates:
            self.origin(request)
            self.sessions.csrf(auth.session, request.headers.get("x-csrf-token"))
        if not route.kind:
            if query:
                raise Denied("MALFORMED_REQUEST", 400)
            await self.audit(context, request_id, "authorized")
            if route.action == "session":
                return JSONResponse(
                    {
                        "actor_id": auth.principal.actor_id,
                        "tenant_id": auth.principal.tenant_id,
                        "csrf_token": self.sessions.vault.open(auth.session)["csrf"],
                        "expires_at": auth.principal.session_expires_at,
                    }
                )
            if route.action == "readiness":
                raise Unavailable()
            return JSONResponse({"api_version": 2, "profile": "isolated-hardening"})
        action = route.action
        allowed_query = (
            {"operation"}
            if action == "challenge"
            else {"offset", "limit"}
            if action == Action.LIST_CAPTURES
            else set()
        )
        if set(query) - allowed_query:
            raise Denied("MALFORMED_REQUEST", 400)
        if action == "challenge":
            if query.get("operation") not in {"enroll", "verify", "liveness"}:
                raise Denied("MALFORMED_REQUEST", 400)
            action = "challenge." + query["operation"]
        if "scope" in params and params["scope"] not in {
            "template_authentication",
            "evaluation_recording",
        }:
            raise Denied("NOT_FOUND", 404)
        if "index" in params and (
            not params["index"].isdigit() or int(params["index"]) >= 12
        ):
            raise Denied("NOT_FOUND", 404)
        context["action"] = action

        async def resolve(current, *, own_deletion=False):
            target = await self.sessions.repo.target(
                current.principal.tenant_id, route.kind, params["id"]
            )
            if target is None or (not target.active and not own_deletion):
                raise Denied("NOT_FOUND", 404)
            grants = await self.sessions.repo.grants(current.principal.actor_id)
            decision = authorize(
                current.principal,
                action,
                target.resource,
                now=int(self.sessions.clock()),
                grants=grants,
            )
            if not decision.allowed:
                if decision.reason == "recent_login_required":
                    raise Denied("RECENT_LOGIN_REQUIRED", 403)
                raise Denied("NOT_FOUND", 404)
            return Access(current, action, target, grants, request_id)

        access = await resolve(auth)
        context["resource"] = access.target.id

        async def reauthorize(*, committed=False):
            current = await self.sessions.authenticate(
                session_id=auth.session.id, access_token=auth.access_token, touch=False
            )
            fresh = await resolve(
                current,
                own_deletion=committed and action == Action.REQUEST_CAPTURE_DELETION,
            )
            expected_generation = access.target.generation
            if committed and action in {
                Action.REVOKE_TEMPLATES,
                Action.REQUEST_CAPTURE_DELETION,
            }:
                expected_generation += 1
            if (
                fresh.target.generation != expected_generation
                or fresh.target.id != access.target.id
            ):
                raise Denied("RESOURCE_CHANGED", 409)
            return fresh

        payload = {"params": params, "query": query}
        for field in ("offset", "limit"):
            if field in query and (
                not query[field].isdigit()
                or int(query[field]) > (100 if field == "limit" else 100000)
            ):
                raise Denied("MALFORMED_REQUEST", 400)
        raw = await self.read_body(request)
        media = request.headers.get("content-type", "").split(";", 1)[0]
        if action in SCAN_ACTIONS:
            key = request.headers.get("idempotency-key", "")
            if not IDEMPOTENCY_KEY.fullmatch(key):
                raise Denied("IDEMPOTENCY_KEY_REQUIRED", 400)
            payload["idempotency_key"] = key
            payload["scan"], payload["scan_bytes"] = self.scan(
                raw, media, params["id"], request.headers.get("x-user-id")
            )
            payload["scan_digest"] = hashlib.sha256(payload["scan_bytes"]).hexdigest()
            payload["precheck"] = self.precheck(request)
        else:
            if request.headers.get("x-user-id") is not None:
                raise Denied("UNTRUSTED_CONTEXT", 400)
            if raw:
                if media != "application/json":
                    raise Denied("UNSUPPORTED_MEDIA_TYPE", 415)
                payload["body"] = json.loads(raw, object_pairs_hook=unique_pairs)
                if not isinstance(payload["body"], dict) or set(payload["body"]) - {
                    "text_version"
                }:
                    raise Denied("MALFORMED_REQUEST", 400)
        # No operation/inference/data read may start if audit persistence failed.
        await self.audit(context, request_id, "authorized")
        if not self.engine and route.engine:
            # Bounded concurrent scans, refused before any recording reservation
            # or engine call. The engine keeps its own worker/queue bound.
            async with (
                self.limits.scans.hold()
                if action in SCAN_ACTIONS
                else contextlib.nullcontext()
            ):
                return await self.proxy(
                    request, request_id, context, access, auth, payload, raw, media,
                    reauthorize,
                )  # fmt: skip
        result = await self.operations.execute(access, payload, reauthorize)
        # Also guard disclosure if a buggy adapter forgot its required check.
        changed = (
            isinstance(result, dict)
            and result.get("generation") == access.target.generation + 1
        )
        await reauthorize(committed=changed)
        await self.audit(context, request_id, "completed")
        if isinstance(result, FrameBody):
            return Response(result.content, media_type=result.media_type)
        return JSONResponse(result)

    async def proxy(
        self, request, request_id, context, access, auth, payload, raw, media,
        reauthorize,
    ):  # fmt: skip
        action = access.action
        recorder = (
            getattr(self.operations, "recording", None)
            if action in SCAN_ACTIONS
            else None
        )
        ticket = await recorder.begin(access, payload) if recorder else None
        response = await self.forward(request, auth, raw, media)
        if recorder:
            decision = json.loads(response.body)
            recording = await recorder.finish(
                access, payload, ticket, decision, reauthorize
            )
            response = JSONResponse(
                {**decision, "recording": recording},
                status_code=response.status_code,
            )
        changed = (
            response.status_code == 200
            and json.loads(response.body).get("generation")
            == access.target.generation + 1
        )
        await reauthorize(committed=changed)
        await self.audit(
            context,
            request_id,
            "completed" if response.status_code == 200 else "downstream_denied",
        )
        return response

    @staticmethod
    def precheck(request):
        """The page's pre-check reading: trace data only, passed on as bounded text."""
        value = request.headers.get(PRECHECK_HEADER)
        if value is None or len(value) > PRECHECK_MAX or not value.isprintable():
            return None
        return value

    @staticmethod
    def scan(raw, media, subject, selector):
        if selector is not None and selector != subject:
            raise Denied("NOT_FOUND", 404)
        if media == "application/json":
            body = json.loads(raw, object_pairs_hook=unique_pairs)
            if not isinstance(body, dict) or set(body) - {
                "user_id",
                "subject_id",
                "facescan",
            }:
                raise Denied("MALFORMED_REQUEST", 400)
            if any(body[k] != subject for k in ("user_id", "subject_id") if k in body):
                raise Denied("NOT_FOUND", 404)
            if not isinstance(body.get("facescan"), str):
                raise Denied("MALFORMED_REQUEST", 400)
            raw = base64.b64decode(body["facescan"], validate=True)
        elif media != "application/msgpack":
            raise Denied("UNSUPPORTED_MEDIA_TYPE", 415)
        # The engine's own limit: the frozen `app` package ships in the same image.
        from app.constants import MAX_SCAN_BYTES

        if len(raw) > MAX_SCAN_BYTES:
            raise Denied("PAYLOAD_TOO_LARGE", 413)
        scan = msgpack.unpackb(
            raw, raw=False, strict_map_key=True, object_pairs_hook=unique_pairs
        )
        if not isinstance(scan, dict):
            raise Denied("MALFORMED_REQUEST", 400)
        if any(scan[k] != subject for k in ("user_id", "subject_id") if k in scan):
            raise Denied("NOT_FOUND", 404)
        return scan, raw

    async def forward(self, request, auth, raw, media):
        config = self.sessions.provider.config
        headers = {
            "Authorization": "Bearer " + auth.access_token,
            "X-Engine-Key": config.engine_key,
            "X-Facetech-Session": auth.session.id,
        }
        if media:
            headers["Content-Type"] = media
        if request.headers.get("idempotency-key"):
            headers["Idempotency-Key"] = request.headers["idempotency-key"]
        if (precheck := self.precheck(request)) is not None:
            headers[PRECHECK_HEADER] = precheck
        try:
            async with httpx.AsyncClient(
                timeout=10,
                trust_env=False,
                verify=self.engine_tls,
                follow_redirects=False,
                transport=self.engine_transport,
            ) as client:
                response = await client.request(
                    request.method,
                    config.engine_origin + request.url.path,
                    params=list(request.query_params.multi_items()),
                    content=raw,
                    headers=headers,
                )
            if response.status_code not in {
                200,
                400,
                401,
                403,
                404,
                409,
                413,
                415,
                422,
                429,
                503,
            }:
                raise Unavailable()
            result = response.json()
            if not isinstance(result, dict):
                raise Unavailable()
            forwarded = JSONResponse(result, status_code=response.status_code)
            # Preserve the engine's own back-off advice; add nothing else.
            if response.status_code in {429, 503} and "retry-after" in response.headers:
                advice = response.headers["retry-after"]
                if advice.isdigit():
                    forwarded.headers["Retry-After"] = advice
            return forwarded
        except (httpx.HTTPError, ValueError) as exc:
            raise Unavailable() from exc

    async def auth_route(self, action, request, query, request_id, context):
        if action in {"login", "register"}:
            if query:
                raise Denied("MALFORMED_REQUEST", 400)
            await self.audit(context, request_id, "started")
            url, browser = await self.sessions.begin(register=action == "register")
            response = RedirectResponse(url, status_code=302)
            response.set_cookie(
                LOGIN_COOKIE,
                browser,
                max_age=300,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return response
        if action == "callback":
            if (
                set(query) - {"state", "code", "iss", "session_state"}
                or query.get("iss", self.sessions.provider.config.issuer)
                != self.sessions.provider.config.issuer
            ):
                raise Denied()
            await self.audit(context, request_id, "started")
            cookie, session = await self.sessions.callback(
                query.get("state"),
                request.cookies.get(LOGIN_COOKIE),
                query.get("code"),
                request.cookies.get(COOKIE),
            )
            context["actor"] = session.actor_id
            await self.audit(context, request_id, "completed")
            response = RedirectResponse("/auth/session", status_code=303)
            response.set_cookie(
                COOKIE,
                cookie,
                max_age=28800,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
            )
            response.delete_cookie(
                LOGIN_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
            )
            return response
        if query:
            raise Denied("MALFORMED_REQUEST", 400)
        if action == "logout":
            self.origin(request)
            # Logout can revoke an expired provider token: local cookie + CSRF.
            local = await self.sessions.repo.session_by_cookie(
                digest(request.cookies.get(COOKIE, ""))
            )
            if local is None:
                raise Denied()
            self.sessions.csrf(local, request.headers.get("x-csrf-token"))
            context["actor"] = local.actor_id
            await self.audit(context, request_id, "started")
            delivered = await self.sessions.logout(
                request.cookies.get(COOKIE), request.headers.get("x-csrf-token")
            )
            await self.audit(context, request_id, "completed")
            response = JSONResponse(
                {"logged_out": True, "provider_logout_pending": not delivered}
            )
            response.delete_cookie(
                COOKIE, secure=True, httponly=True, samesite="lax", path="/"
            )
            return response
        if (
            request.headers.get("content-type", "").split(";", 1)[0]
            != "application/x-www-form-urlencoded"
        ):
            raise Denied("UNSUPPORTED_MEDIA_TYPE", 415)
        body = unique_pairs(
            parse_qsl(
                (await self.read_body(request, 20000)).decode(), keep_blank_values=True
            )
        )
        if set(body) != {"logout_token"}:
            raise Denied("MALFORMED_REQUEST", 400)
        await self.audit(context, request_id, "started")
        await self.sessions.backchannel(body["logout_token"])
        await self.audit(context, request_id, "completed")
        return JSONResponse({"logged_out": True})


def create_gateway(
    sessions: Sessions,
    *,
    operations=None,
    engine_transport=None,
    engine_tls_context=None,
    limits=None,
):
    return HardenedApp(
        sessions,
        engine=False,
        operations=operations,
        engine_transport=engine_transport,
        engine_tls_context=engine_tls_context,
        limits=limits,
    )


def create_engine(sessions: Sessions, *, operations=None, limits=None):
    return HardenedApp(sessions, engine=True, operations=operations, limits=limits)
