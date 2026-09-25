import json
import re
import time
from contextlib import asynccontextmanager

import anyio
import anyio.to_thread
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app import (
    aenet,
    anti_spoof,
    auth,
    challenge,
    enrolment,
    errors,
    flags,
    flash,
    geometry,
    liveness,
    store,
    trace,
)
from app.constants import MATCH_THRESHOLD
from app.detect import (
    align_largest_face,
    decode_jpeg,
    detect_faces,
    get_detector,
)
from app.embed import cosine_similarity, embed_aligned, get_embedder
from app.facescan import (
    MAX_B64_CHARS,
    FaceScanError,
    parse_facescan,
    parse_facescan_bytes,
)

MODEL_VERSION = "det_500m+w600k_r50 (buffalo_sc/buffalo_l)"
ENGINE_VERSION = "0.2.0"

PLACEHOLDER_THRESHOLD = MATCH_THRESHOLD

REFERENCE_FRAMES = 3

MSGPACK_CONTENT_TYPES = frozenset(
    {"application/msgpack", "application/x-msgpack", "application/vnd.msgpack"}
)
USER_ID_HEADER = "x-user-id"

MAX_BODY_BYTES = MAX_B64_CHARS + 4096

USER_ID_RE = re.compile(r"[a-z0-9._-]{1,64}")


def canonical_user_id(raw: str) -> str:
    return raw.strip().lower()


_INFERENCE_LIMITER = anyio.CapacityLimiter(1)
MAX_QUEUED_SCANS = 4
BUSY_RETRY_AFTER_S = 2

_admitted_scans = 0


def inflight_scans() -> int:
    return _admitted_scans


async def _off_loop(fn, *args) -> JSONResponse:
    global _admitted_scans
    t = trace.get()
    if _admitted_scans >= MAX_QUEUED_SCANS + 1:
        return _error(
            errors.BUSY,
            "engine is at capacity; retry shortly",
            headers={"Retry-After": str(BUSY_RETRY_AFTER_S)},
        )
    _admitted_scans += 1
    queued = time.perf_counter()

    def run():
        t.mark("queue", queued)
        return fn(*args)

    try:
        return await anyio.to_thread.run_sync(run, limiter=_INFERENCE_LIMITER)
    finally:
        _admitted_scans -= 1


def warm_models() -> dict:
    get_detector()
    get_embedder()
    anti_spoof.get_pad()
    return {"detector": True, "embedder": True, "pad": True}


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.load_api_key()
    warm_models()
    yield


app = FastAPI(title="facetech-engine", version=ENGINE_VERSION, lifespan=lifespan)


class LivenessRequest(BaseModel):
    facescan: str = Field(min_length=1)


class ScanRequest(LivenessRequest):
    user_id: str = Field(min_length=1)


def _error(
    code: str,
    message: str,
    headers: dict | None = None,
    trace_reason: str | None = None,
) -> JSONResponse:
    trace.get().error(code, message if trace_reason is None else trace_reason)
    return JSONResponse(
        status_code=errors.HTTP_STATUS[code],
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


@app.middleware("http")
async def require_engine_key(request: Request, call_next):
    if auth.requires_key(request.url.path) and not auth.is_valid(
        request.headers.get(auth.HEADER)
    ):
        return _error(
            errors.UNAUTHORIZED,
            f"missing or invalid {auth.HEADER} header",
        )
    return await call_next(request)


@app.middleware("http")
async def decision_trace(request: Request, call_next):
    request_id = trace.request_id_from(request.headers.get(trace.REQUEST_ID_HEADER))
    endpoint = (
        trace.TRACED_PATHS.get(request.url.path) if request.method == "POST" else None
    )
    if endpoint is None:
        response = await call_next(request)
        _tag(response, request_id)
        return response
    t, token = trace.start(endpoint, request_id)
    t.set("engine_version", ENGINE_VERSION)
    t.set("enforced", True)
    t.fields["thresholds"] = {
        "match": PLACEHOLDER_THRESHOLD,
        "liveness": liveness.LIVENESS_THRESHOLD,
    }
    t.precheck(request.headers.get(trace.PRECHECK_HEADER))
    try:
        response = await call_next(request)
    except Exception:
        t.emit(500)
        raise
    finally:
        trace.reset(token)
    _tag(response, request_id)
    evaluation = flags.choice("FACETECH_DIAGNOSTIC_HEADER", ("0", "1"), "0") == "1"
    decision = (
        t.line(response.status_code) if evaluation else t.emit(response.status_code)
    )
    if evaluation and auth.is_valid(request.headers.get(auth.HEADER)):
        response.headers["X-Facetech-Decision"] = json.dumps(
            decision, separators=(",", ":"), ensure_ascii=True
        )
    return response


def _tag(response, request_id: str) -> None:
    response.headers[trace.REQUEST_ID_HEADER] = request_id
    response.headers[trace.BUILD_HEADER] = trace.build_id()


@app.exception_handler(FaceScanError)
async def facescan_error_handler(request: Request, exc: FaceScanError) -> JSONResponse:
    return _error(exc.code, exc.message)


class _ShapeError(Exception):
    def __init__(self, loc: list, message: str, err_type: str = "value_error"):
        super().__init__(message)
        self.loc = loc
        self.message = message
        self.err_type = err_type

    def response(self) -> JSONResponse:
        trace.get().shape_error(self.loc, self.err_type)
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": self.loc, "msg": self.message, "type": self.err_type}
                ]
            },
        )


async def _read_scan(
    request: Request, *, require_user_id: bool = True
) -> tuple[str | None, dict]:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    msgpack = media_type in MSGPACK_CONTENT_TYPES
    t = trace.get()
    t.transport("msgpack" if msgpack else "json")
    with t.step("parse"):
        return await _parse_scan(request, msgpack, require_user_id)


async def _parse_scan(
    request: Request, msgpack: bool, require_user_id: bool
) -> tuple[str | None, dict]:
    body = await _read_body_capped(request)

    if msgpack:
        user_id = request.headers.get(USER_ID_HEADER, "").strip()
        if not user_id and require_user_id:
            raise _ShapeError(
                ["header", USER_ID_HEADER],
                f"{USER_ID_HEADER} header is required on the msgpack transport",
                "missing",
            )
        if require_user_id:
            user_id = _checked_user_id(user_id, ["header", USER_ID_HEADER])
        return (user_id or None), parse_facescan_bytes(body)

    model = ScanRequest if require_user_id else LivenessRequest
    try:
        req = model.model_validate(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _ShapeError(["body"], f"body is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        first = exc.errors()[0]
        raise _ShapeError(
            ["body", *(str(p) for p in first.get("loc", ()))],
            first.get("msg", "invalid request body"),
            first.get("type", "value_error"),
        ) from exc
    user_id = getattr(req, "user_id", None)
    if user_id is not None:
        user_id = _checked_user_id(user_id, ["body", "user_id"])
    return user_id, parse_facescan(req.facescan)


async def _read_body_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise FaceScanError(
            errors.PAYLOAD_TOO_LARGE,
            f"request body is {declared} bytes, limit is {MAX_BODY_BYTES}",
        )
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise FaceScanError(
                errors.PAYLOAD_TOO_LARGE,
                f"request body exceeds the {MAX_BODY_BYTES}-byte limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _checked_user_id(raw: str, loc: list) -> str:
    user_id = canonical_user_id(raw)
    if not USER_ID_RE.fullmatch(user_id):
        raise _ShapeError(
            loc,
            "user_id must be 1-64 characters of [a-z0-9._-] (case-insensitive)",
            "string_pattern_mismatch",
        )
    return user_id


def _scan_detections(scan: dict) -> list[list[dict]]:
    t = trace.get()
    with t.step("detect"):
        detections = [detect_faces(frame["jpeg_bytes"]) for frame in scan["frames"]]
    t.frames(len(detections), sum(1 for dets in detections if dets))
    return detections


def _bbox_centre(det: dict) -> tuple[float, float]:
    x1, y1, x2, y2 = det["bbox"]
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _tracked_faces(detections: list[list[dict]], reference: dict) -> list[dict | None]:
    ref_x, ref_y = _bbox_centre(reference)
    rx1, ry1, rx2, ry2 = reference["bbox"]
    limit = float(np.hypot(rx2 - rx1, ry2 - ry1))
    tracked = []
    for dets in detections:
        if not dets:
            tracked.append(None)
            continue
        nearest = min(
            dets,
            key=lambda d: np.hypot(
                _bbox_centre(d)[0] - ref_x, _bbox_centre(d)[1] - ref_y
            ),
        )
        nx, ny = _bbox_centre(nearest)
        distance = float(np.hypot(nx - ref_x, ny - ref_y))
        tracked.append(nearest if distance <= limit else None)
    return tracked


def _ranked_frames(scan: dict, detections: list[list[dict]]) -> list[tuple]:
    scored = [(frame, dets) for frame, dets in zip(scan["frames"], detections) if dets]
    return sorted(scored, key=lambda pair: pair[1][0]["det_score"], reverse=True)


def _embedding_frames(
    scan: dict, detections: list[list[dict]], reference: dict | None = None
) -> list[tuple]:
    if reference is None:
        reference = _ranked_frames(scan, detections)[0][1][0]
    tracked = _tracked_faces(detections, reference)
    candidates = [
        (frame, dets, det)
        for frame, dets, det in zip(scan["frames"], detections, tracked)
        if det is not None
    ]
    candidates.sort(key=lambda item: item[2]["det_score"], reverse=True)
    return candidates[:REFERENCE_FRAMES]


def _reference_embedding(selected: list[tuple]):
    t = trace.get()
    with t.step("embed"):
        embedding, used = _embed_selected(selected)
    t.embedded(used)
    return embedding, used


def _embed_selected(selected: list[tuple]):
    vectors = []
    for frame, _dets, det in selected:
        aligned = align_largest_face(frame["jpeg_bytes"], detection=det)
        if aligned is None:
            continue
        vectors.append(embed_aligned(aligned))
    if not vectors:
        return None, 0
    mean = np.mean(vectors, axis=0)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        return vectors[0], 1
    return (mean / norm).astype(np.float32), len(vectors)


def _liveness_of(
    scan: dict, detections: list[list[dict]], reference: dict, challenge_verdict
):
    t = trace.get()
    with t.step("liveness"):
        result = liveness.check_liveness(
            scan["frames"], _tracked_faces(detections, reference), challenge_verdict
        )
    t.challenge(challenge_verdict)
    t.liveness(result, liveness.LIVENESS_THRESHOLD)
    return result


def _challenge_failure(verdict) -> JSONResponse | None:
    if verdict.ok:
        return None
    return _error(
        errors.CHALLENGE_FAIL,
        f"challenge check failed: {verdict.reason}",
    )


def _early_challenge_failure(scan: dict) -> JSONResponse | None:
    if not liveness.enforcement_enabled():
        return None
    if challenge.inspect_from_scan(scan).reason not in challenge.EARLY_REFUSAL_REASONS:
        return None
    verdict = challenge.consume_from_scan(scan)
    trace.get().challenge(verdict)
    return _challenge_failure(verdict)


def _liveness_failure(result) -> JSONResponse | None:
    if result.live:
        return None
    if result.retryable:
        return _error(
            errors.LOW_QUALITY,
            "not enough usable frames to check liveness: "
            + ", ".join(result.failed)
            + " — capture again",
        )
    return _error(
        errors.LIVENESS_FAIL,
        "liveness check failed: " + ", ".join(result.failed),
    )


def _liveness_summary(result) -> dict:
    return {
        "live": result.live,
        "score": round(result.score, 4),
        "enforced": True,
        "failed_signals": result.failed,
    }


@app.get("/health")
def health() -> dict:
    if anti_spoof._model is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "buildId": trace.build_id()},
        )
    return {
        "status": "ok",
        "engineVersion": ENGINE_VERSION,
        "buildId": trace.build_id(),
    }


@app.get("/v1/info")
def info() -> dict:
    return {
        "engineVersion": ENGINE_VERSION,
        "modelVersion": MODEL_VERSION,
        "thresholds": {"match": None, "liveness": None},
        "faceScanVersions": [1],
    }


@app.get("/v1/challenge")
def issue_challenge(request: Request):
    operation = request.headers.get("x-facetech-operation", "liveness")
    user_id = request.headers.get(USER_ID_HEADER, "")
    user_id = canonical_user_id(user_id)
    if operation not in {"enroll", "verify", "liveness"} or (
        operation != "liveness" and not USER_ID_RE.fullmatch(user_id)
    ):
        return _error(errors.CHALLENGE_FAIL, "valid operation and user_id required")
    return challenge.issue(operation, user_id if operation != "liveness" else "")


@app.post("/v1/enroll")
async def enroll(request: Request) -> JSONResponse:
    try:
        user_id, scan = await _read_scan(request)
    except _ShapeError as exc:
        return exc.response()

    if (failure := _early_challenge_failure(scan)) is not None:
        return failure
    return await _off_loop(_enroll_scan, user_id, scan)


def _secure_analysis(scan: dict, operation: str, user_id: str):
    verdict = challenge.consume_from_scan(scan, (operation, user_id))
    trace.get().challenge(verdict)
    if (failure := _challenge_failure(verdict)) is not None:
        return failure, None, None
    if verdict.action not in challenge.issuable_actions():
        return (
            _error(
                errors.CHALLENGE_FAIL,
                "current head sequence required"
                if challenge.selected_policy() == challenge.POLICY_HEAD_SEQUENCE
                else "current single turn required",
            ),
            None,
            None,
        )
    try:
        detections = _scan_detections(scan)

        def embedding(frame, det):
            aligned = align_largest_face(frame["jpeg_bytes"], detection=det)
            if aligned is None:
                raise ValueError("alignment failed")
            return embed_aligned(aligned)

        policy = anti_spoof.policy_for(verdict.action)
        with trace.get().step("pad_and_continuity"):
            pad, vector = anti_spoof.evaluate(
                scan["frames"], detections, embedding, decode_jpeg, policy
            )
        trace.get().pad(pad)
        trace.get().embedded(pad["usable"])
        tracked = [d[0] if len(d) == 1 else None for d in detections]
        with trace.get().step("liveness"):
            live = liveness.check_liveness(scan["frames"], tracked, verdict, policy)
        trace.get().liveness(live, liveness.LIVENESS_THRESHOLD)
        trace.get().sequence(live.signals["challenge"].detail)
        geom = _log_geometry(scan["frames"], tracked, verdict)
        _log_ensemble(scan["frames"], tracked, verdict, pad)
        glow = _log_flash(scan["frames"], tracked, verdict)
        if pad["outcome"] != "live":
            if pad["reason"] == "multiple_faces":
                return (
                    _error(
                        errors.MULTI_FACE,
                        "exactly one face required throughout capture",
                    ),
                    None,
                    live,
                )
            if pad["outcome"] == "insufficient_evidence" and not any(detections):
                return _error(errors.NO_FACE, "no usable face in capture"), None, live
            code = (
                errors.MODEL_UNAVAILABLE
                if pad["outcome"] == "inference_error"
                else errors.LOW_QUALITY
                if pad["outcome"] == "insufficient_evidence"
                else errors.LIVENESS_FAIL
            )
            return (
                _error(code, "PAD " + pad["outcome"] + ": " + str(pad["reason"])),
                None,
                live,
            )
        if (failure := _liveness_failure(live)) is not None:
            return failure, None, live
        if glow is not None and glow["enforced"] and glow["outcome"] == "fail":
            return (
                _error(errors.LIVENESS_FAIL, "liveness check failed: pad_flash"),
                None,
                live,
            )
        if operation == "enroll":
            failure, vector = _enrolment(
                scan["frames"], tracked, verdict, vector, geom, glow
            )
            if failure is not None:
                return failure, None, live
        return None, vector, live
    except Exception as exc:
        return (
            _error(
                errors.MODEL_UNAVAILABLE,
                "required inference failed",
                trace_reason=type(exc).__name__,
            ),
            None,
            None,
        )


def _log_geometry(frames: list[dict], tracked: list, verdict) -> dict | None:
    """PAD_GEOMETRY log mode: the nose residual goes to the trace, never to a decision
    (only the enrolment bar reads it back, see _enrolment)."""
    if geometry.mode() == "off":
        return None
    settle = (verdict.params or {}).get("settle_ms")
    if not isinstance(settle, int) or isinstance(settle, bool):
        settle = None
    t = trace.get()
    with t.step("geometry"):
        try:
            record = geometry.nose_residual(
                tracked, [f["ts_ms"] for f in frames], settle
            )
        except Exception as exc:
            record = {"outcome": "error", "reason": type(exc).__name__}
    t.geometry({"mode": geometry.mode(), **record})
    return record


def _log_ensemble(frames: list[dict], tracked: list, verdict, pad: dict) -> None:
    """PAD_ENSEMBLE log mode: AENet beside MiniFASNet goes to the trace, never to a decision.

    AENet is NON-COMMERCIAL, demo only (models/anti_spoof/POLICY.md). Under the default
    `minifas` it is never loaded or run and the trace is unchanged.
    """
    mode = aenet.mode()
    if mode == aenet.MINIFAS:
        return
    settle = (verdict.params or {}).get("settle_ms")
    if not isinstance(settle, int) or isinstance(settle, bool):
        settle = None
    t = trace.get()
    with t.step("aenet"):
        try:
            record = aenet.score_scan(frames, tracked, settle, decode_jpeg)
        except Exception as exc:
            record = {"outcome": "error", "reason": type(exc).__name__}
    t.ensemble(
        {
            "mode": mode,
            "enforced": False,
            "license": aenet.LICENSE,
            "minifas": aenet.minifas_summary(pad),
            "aenet": record,
        }
    )


def _log_flash(frames: list[dict], tracked: list, verdict) -> dict | None:
    """FLASH_CHECK: the glow record goes to the trace. Only `on` enforces, and only a
    `fail`; `inconclusive` (too bright, unmeasurable) never refuses. Off = not run."""
    mode = flash.mode()
    if mode == flash.OFF:
        return None
    t = trace.get()
    with t.step("flash"):
        try:
            record = flash.check(frames, tracked, verdict.glow, decode_jpeg)
        except Exception as exc:
            record = {"outcome": "error", "reason": type(exc).__name__}
    record = {"mode": mode, "enforced": mode == flash.ON, **record}
    t.flash(record)
    return record


def _enrolment(frames, tracked, verdict, vector, geometry_record, glow):
    """Phase 5, enrolment only: the enrolment photo (single-turn policies) and the stricter
    bar (enforced per check only when its flag is `on`). All flags off: nothing runs, nothing
    is written, the template is today's mean."""
    wanted = enrolment.photo_wanted()
    decision = enrolment.bar(geometry_record, glow)
    if not wanted and decision is None:
        return None, vector
    t = trace.get()
    record = {}
    if wanted:
        settle = (verdict.params or {}).get("settle_ms")
        if not isinstance(settle, int) or isinstance(settle, bool):
            settle = None
        with t.step("photo"):
            try:
                photo = enrolment.photo(frames, tracked, settle, decode_jpeg)
                if photo["index"] is not None:
                    i = photo["index"]
                    chosen, _ = _embed_selected([(frames[i], None, tracked[i])])
                    if chosen is None:
                        photo = {**photo, "index": None, "reason": "alignment"}
                    else:
                        vector = chosen
            except Exception as exc:
                photo = {"index": None, "reason": type(exc).__name__}
        # No usable photo: the template stays the mean of the PAD frames, as today.
        record["photo"] = photo
    if decision is not None:
        record["bar"] = decision
    t.enrolment(record)
    if decision is not None and decision["refuse"]:
        return (
            _error(
                errors.LIVENESS_FAIL,
                "liveness check failed: " + ", ".join(decision["refuse"]),
            ),
            None,
        )
    return None, vector


def _pad_policy(scan: dict) -> str:
    presented = scan.get("challenge")
    action = presented.get("action") if isinstance(presented, dict) else None
    return anti_spoof.policy_for(action)


def _enroll_scan(user_id: str, scan: dict) -> JSONResponse:
    failure, embedding, live = _secure_analysis(scan, "enroll", user_id)
    if failure is not None:
        return failure
    template_id = store.enroll(user_id, embedding)
    trace.get().set("outcome", "enrolled")
    return JSONResponse(
        status_code=200,
        content={
            "template_id": template_id,
            "quality": {
                "score": trace.get().fields["pad"]["minimum_detection_score"]
                if trace.current()
                else None,
                "frames_embedded": trace.get().fields["pad"]["usable"]
                if trace.current()
                else 0,
            },
            "liveness": _liveness_summary(live),
            "pad": {"outcome": "live", "policy": _pad_policy(scan)},
        },
    )


@app.post("/v1/verify")
async def verify(request: Request) -> JSONResponse:
    try:
        user_id, scan = await _read_scan(request)
    except _ShapeError as exc:
        return exc.response()

    templates = store.get_templates(user_id)
    if not templates:
        return _error(
            errors.USER_NOT_FOUND,
            f"user_id {user_id!r} has no enrolled template",
            trace_reason="user has no enrolled template",
        )
    if (failure := _early_challenge_failure(scan)) is not None:
        return failure
    return await _off_loop(_verify_scan, templates, scan, user_id)


def _verify_scan(templates: list[dict], scan: dict, user_id: str = "") -> JSONResponse:
    failure, embedding, live = _secure_analysis(scan, "verify", user_id)
    if failure is not None:
        return failure
    with trace.get().step("match"):
        score = max(cosine_similarity(embedding, t["embedding"]) for t in templates)
    matched = bool(score >= PLACEHOLDER_THRESHOLD)
    trace.get().match(score, matched, PLACEHOLDER_THRESHOLD)
    trace.get().set("outcome", "match" if matched else "no_match")

    return JSONResponse(
        status_code=200,
        content={
            "pad": {"outcome": "live", "policy": _pad_policy(scan)},
            "match": matched,
            "score": score,
            "threshold": PLACEHOLDER_THRESHOLD,
            "liveness": _liveness_summary(live),
        },
    )


@app.post("/v1/liveness")
async def liveness_check(request: Request) -> JSONResponse:
    try:
        _, scan = await _read_scan(request, require_user_id=False)
    except _ShapeError as exc:
        return exc.response()
    return await _off_loop(_liveness_scan, scan)


def _liveness_scan(scan: dict) -> JSONResponse:
    failure, _, live = _secure_analysis(scan, "liveness", "")
    if failure is not None:
        return failure
    trace.get().set("outcome", "live")
    return JSONResponse(
        status_code=200,
        content={
            **live.as_dict(),
            "enforced": True,
            "pad": {"outcome": "live", "policy": _pad_policy(scan)},
        },
    )


@app.delete("/v1/templates/{user_id}")
async def delete_templates(user_id: str) -> JSONResponse:
    user_id = canonical_user_id(user_id)
    removed = store.delete_user(user_id)
    if not removed:
        return _error(
            errors.USER_NOT_FOUND, f"user_id {user_id!r} has no enrolled template"
        )
    return JSONResponse(
        status_code=200, content={"user_id": user_id, "deleted": removed}
    )
