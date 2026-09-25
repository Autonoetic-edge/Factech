import asyncio
import contextlib
import logging
import os
import uuid
import warnings
from pathlib import Path

import httpx
from anyio import to_thread
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import capture_store
from evaluation import install_review

log = logging.getLogger("facetech.gateway")

APPS_DIR = Path(__file__).resolve().parent.parent / "apps"
ENGINE_URL = os.environ.get("FACETECH_ENGINE_URL", "http://127.0.0.1:8000")

ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
ENGINE_KEY_HEADER = "X-Engine-Key"
USER_ID_HEADER = "X-User-Id"

REQUEST_ID_HEADER = "X-Request-Id"
ENGINE_BUILD_HEADER = "X-Engine-Build"

PASSTHROUGH_RESPONSE_HEADERS = ("retry-after", "x-request-id")

if not ENGINE_API_KEY.strip():
    warnings.warn(
        "ENGINE_API_KEY is unset: /v1/* proxying will be rejected by the "
        "engine with 401 UNAUTHORIZED (contract §2.3). Set the same value "
        "the engine has.",
        RuntimeWarning,
        stacklevel=2,
    )

ENGINE_UNREACHABLE = "ENGINE_UNREACHABLE"

GATEWAY_CODES = frozenset({ENGINE_UNREACHABLE})
GATEWAY_HTTP_STATUS = {ENGINE_UNREACHABLE: 502}

CAPTURES, CAPTURE_LABEL = capture_store.open_from_env()

if CAPTURES is not None:
    warnings.warn(
        f"capture store ON: raw scans (face images) are being written to "
        f"{CAPTURES.path} with fallback label={CAPTURE_LABEL or 'none'}. This "
        f"file is biometric data — gitignored, never committed. Only scans "
        f"whose page sent consent {capture_store.CONSENT_VERSION!r} are kept, "
        f"for {CAPTURES.retention_days} days. "
        f"Unset FACETECH_CAPTURE_DB to disable.",
        RuntimeWarning,
        stacklevel=2,
    )

PURGE_TASK_S = 3600.0


async def _retention_loop(store: capture_store.CaptureStore) -> None:
    while True:
        await asyncio.sleep(PURGE_TASK_S)
        await to_thread.run_sync(store.purge_expired)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    store = CAPTURES
    task = asyncio.create_task(_retention_loop(store)) if store is not None else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="facetech-mock-gateway", version="0.1.0", lifespan=_lifespan)

@app.middleware("http")
async def evaluation_cache_control(request: Request, call_next):
    response = await call_next(request)
    # Avoid mixing cached capture code with a newer challenge policy.
    content_type = response.headers.get("content-type", "")
    if ("text/html" in content_type or "javascript" in content_type
            or request.url.path in ("/capture-status", "/engine-status", "/health")):
        response.headers["Cache-Control"] = "no-store"
    return response


http_client = httpx.AsyncClient(base_url=ENGINE_URL, timeout=60.0)


def _error(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=GATEWAY_HTTP_STATUS[code],
        content={"error": {"code": code, "message": message}},
    )


def _headers(request: Request, request_id: str) -> dict:
    headers = {
        "Content-Type": request.headers.get("content-type", ""),
        REQUEST_ID_HEADER: request_id,
    }

    user_id = request.headers.get(USER_ID_HEADER)
    if user_id is not None:
        headers[USER_ID_HEADER] = user_id
    operation = request.headers.get("X-Facetech-Operation")
    if operation is not None:
        headers["X-Facetech-Operation"] = operation
    if ENGINE_API_KEY:
        headers[ENGINE_KEY_HEADER] = ENGINE_API_KEY
    return headers


@app.get("/engine-status")
async def engine_status() -> dict:
    try:
        r = await http_client.get("/health")
        return {"engine": r.json()}
    except httpx.HTTPError:
        return {"engine": None}


@app.get("/capture-status")
async def capture_status() -> dict:
    if CAPTURES is None:
        return {"enabled": False}
    counts = await to_thread.run_sync(CAPTURES.counts)
    return {"enabled": True, "label": CAPTURE_LABEL, **counts}


@app.api_route("/health", methods=["GET"])
@app.api_route("/v1/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy(request: Request, path: str = "") -> Response:
    url = request.url.path
    body = await request.body()
    request_id = uuid.uuid4().hex
    try:
        upstream = await http_client.request(
            request.method,
            url,
            content=body or None,
            params=request.query_params,
            headers=_headers(request, request_id),
        )
    except httpx.HTTPError as exc:
        failed = _error(
            ENGINE_UNREACHABLE,
            f"engine at {ENGINE_URL} unreachable: {exc.__class__.__name__}",
        )

        failed.headers[REQUEST_ID_HEADER] = request_id
        await _bank(url, request, body, failed.status_code, failed.body, request_id)
        return failed

    request_id = upstream.headers.get(REQUEST_ID_HEADER, request_id)
    await _bank(
        url,
        request,
        body,
        upstream.status_code,
        upstream.content,
        request_id,
        upstream.headers.get(ENGINE_BUILD_HEADER),
        upstream.headers.get("X-Facetech-Decision"),
    )
    return Response(
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        content=upstream.content,
        headers={
            name: upstream.headers[name]
            for name in PASSTHROUGH_RESPONSE_HEADERS
            if name in upstream.headers
        },
    )


async def _bank(
    url: str,
    request: Request,
    body: bytes,
    status_code: int,
    response: bytes,
    request_id: str,
    build_id: str | None = None,
    decision: str | None = None,
) -> None:
    if CAPTURES is None or url not in capture_store.CAPTURED_ENDPOINTS:
        return
    try:
        await _bank_scan(
            CAPTURES,
            url,
            request,
            body,
            status_code,
            response,
            request_id,
            build_id,
            decision,
        )
    except Exception as exc:
        CAPTURES.bank_failed()
        log.warning(
            "capture banking failed; the response was sent unchanged "
            "request_id=%s error=%s",
            request_id if capture_store.REQUEST_ID_RE.fullmatch(request_id) else "-",
            type(exc).__name__,
        )


async def _bank_scan(
    store: capture_store.CaptureStore,
    url: str,
    request: Request,
    body: bytes,
    status_code: int,
    response: bytes,
    request_id: str,
    build_id: str | None,
    decision: str | None = None,
) -> None:
    meta = capture_store.parse_meta(request.headers.get(capture_store.META_HEADER))
    if meta.get("consent") != capture_store.CONSENT_VERSION:
        store.skip("no_consent")
        return
    content_type = request.headers.get("content-type", "")
    scan = capture_store.scan_bytes_of(content_type, body)
    if scan is None:
        store.skip("not_a_scan")
        return
    await to_thread.run_sync(
        lambda: store.record(
            endpoint=url,
            content_type=content_type,
            body=scan,
            status_code=status_code,
            response=response,
            label=CAPTURE_LABEL,
            request_id=request_id,
            build_id=build_id,
            user_agent=request.headers.get("user-agent"),
            meta=meta,
            decision=decision,
        )
    )


install_review(app, lambda: CAPTURES)

app.mount(
    "/console", StaticFiles(directory=APPS_DIR / "console", html=True), name="console"
)

SDK_DIST = Path(__file__).resolve().parent.parent / "packages" / "face-sdk" / "dist"


def mount_if_present(
    target: FastAPI, url_path: str, directory: Path, name: str, *, html: bool = False
) -> bool:
    if not directory.is_dir():
        return False
    target.mount(url_path, StaticFiles(directory=directory, html=html), name=name)
    return True


mount_if_present(
    app, "/integration", APPS_DIR / "integration-demo", "integration", html=True
)
mount_if_present(app, "/sdk", SDK_DIST, "sdk")

mount_if_present(app, "/shared", APPS_DIR / "shared", "shared")

MEDIAPIPE_DIR = APPS_DIR / "vendor" / "mediapipe"
mount_if_present(app, "/vendor/mediapipe", MEDIAPIPE_DIR, "mediapipe")

app.mount(
    "/", StaticFiles(directory=APPS_DIR / "integration-demo", html=True), name="home"
)
