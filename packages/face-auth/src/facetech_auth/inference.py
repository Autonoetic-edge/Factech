"""The frozen engine pipeline behind the Analyzer port; no rule is re-implemented.

`engine/app/main._secure_analysis` is called unchanged for every scan, so PAD,
head-sequence, liveness, embedding and match arithmetic are byte-for-byte the
live engine's. The durable challenge store is the only authority for nonce
single-use, expiry and binding; the already-claimed challenge's frozen action,
parameters and age are handed to the engine's own verdict path through a
per-call record. Templates are the engine's normalized float32 vectors.
"""

import asyncio
import contextlib
import copy
import hashlib
import importlib.util
import json
import secrets
import sys
import threading
from pathlib import Path

import numpy as np

from .contracts import Denied, Unavailable
from .operations import Analysis

TEMPLATE_FORMAT = "arcface-512-float32-le"


class FrozenAnalyzer:
    def __init__(self, engine_root, *, warm=True):
        root = Path(engine_root).resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from app import anti_spoof, challenge, embed, facescan, main, trace

        self.main, self.pad, self.challenge = main, anti_spoof, challenge
        self.facescan, self.trace, self.embed = facescan, trace, embed
        self.format_id = TEMPLATE_FORMAT + "/" + self.fingerprint(root)
        self._admission, self._queued = threading.Lock(), 0
        self._worker = threading.Lock()  # one inference at a time, as the engine
        if warm:
            main.warm_models()

    def challenge_parameters(self):
        """CHALLENGE_POLICY single-turn-v1 / single-turn-glow-v1: the action, params and glow
        schedule the engine itself would issue, drawn by its own challenge.py / flash.py (no
        numbers copied here). Nothing is registered in the engine's in-memory nonce table:
        the durable store is the authority. Any other policy: None, and the gateway issues
        today's HEAD_SEQUENCE challenge unchanged."""
        ch = self.challenge
        if ch.selected_policy() not in ch.SINGLE_TURN_POLICIES:
            return None
        action = secrets.choice(ch.issuable_actions())
        parameters = {"action": action, "params": ch._draw_params(action)}
        glow = ch._draw_glow()
        if glow is not None:
            parameters["glow"] = glow
        return parameters

    def fingerprint(self, root):
        """Hash of the verified model bytes plus frozen version/policy labels."""
        spec = importlib.util.spec_from_file_location(
            "facetech_download_models", root / "scripts/download_models.py"
        )
        pins = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pins)
        expected = {m["file"]: m["sha256"] for m in pins.MODELS}
        manifest = json.loads(
            (root / "models/anti_spoof/manifest.json").read_text("utf-8")
        )
        for model in manifest["models"]:
            expected["anti_spoof/" + model["onnx"]] = model["onnx_sha256"]
        digest = hashlib.sha256()
        for name in sorted(expected):
            path = root / "models" / name
            if not path.is_file():
                raise Unavailable()
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected[name]:
                raise Unavailable()
            digest.update(f"{name}:{actual}\n".encode())
        digest.update(
            "|".join(
                (
                    self.main.MODEL_VERSION,
                    self.pad.MODEL_VERSION,
                    self.pad.POLICY_VERSION,
                    str(self.embed.EMBED_DIM),
                    str(self.main.PLACEHOLDER_THRESHOLD),
                )
            ).encode()
        )
        return digest.hexdigest()[:24]

    @staticmethod
    def encode(vector):
        array = np.asarray(vector, dtype="<f4").ravel()
        FrozenAnalyzer.check(array)
        return array.tobytes()

    @staticmethod
    def decode(raw):
        array = np.frombuffer(raw, dtype="<f4")
        FrozenAnalyzer.check(array)
        return array

    @staticmethod
    def check(array):
        if (
            array.shape != (512,)
            or not np.isfinite(array).all()
            or abs(float(np.linalg.norm(array)) - 1.0) > 1e-3
        ):
            raise Unavailable()

    @contextlib.asynccontextmanager
    async def admit(self):
        """Engine admission bound (one worker, four queued) before any nonce spend."""
        with self._admission:
            if self._queued >= self.main.MAX_QUEUED_SCANS + 1:
                raise Denied(
                    "BUSY",
                    503,
                    "engine is at capacity; retry shortly",
                    retry_after=self.main.BUSY_RETRY_AFTER_S,
                )
            self._queued += 1
        try:
            yield
        finally:
            with self._admission:
                self._queued -= 1

    async def analyze(
        self, operation, scan, templates, *, request_id, challenge, precheck=None
    ):
        return await asyncio.to_thread(
            self._run, operation, scan, templates, request_id, challenge, precheck
        )

    def _run(self, operation, raw, templates, request_id, challenge, precheck=None):
        main, trace = self.main, self.trace
        t, token = trace.start(operation, trace.request_id_from(request_id))
        t.set("engine_version", main.ENGINE_VERSION)
        t.set("enforced", True)
        t.set("model_fingerprint", self.format_id)
        t.fields["thresholds"] = {
            "match": main.PLACEHOLDER_THRESHOLD,
            "liveness": main.liveness.LIVENESS_THRESHOLD,
        }
        t.transport("msgpack")
        t.precheck(precheck)
        try:
            with self._worker:
                return self._decide(t, operation, raw, templates, challenge)
        finally:
            trace.reset(token)

    def _decide(self, t, operation, raw, templates, challenge):
        main, challenge_mod = self.main, self.challenge
        try:
            with t.step("parse"):
                scan = self.facescan.parse_facescan_bytes(raw)
        except self.facescan.FaceScanError as exc:
            t.error(exc.code, exc.message)
            return self._rejected(t, exc.code, exc.message, exc.status_code)
        try:
            vectors = tuple(self.decode(raw_template) for raw_template in templates)
        except Unavailable:
            t.error("MODEL_UNAVAILABLE", "stored template is not a valid vector")
            return self._rejected(t, "MODEL_UNAVAILABLE", "template unreadable", 503)
        user_id = "" if operation == "liveness" else challenge["subject"]
        nonce = scan["challenge"]["nonce"]
        now = challenge_mod._now_ms()
        # Durable store already enforced single use, binding and inclusive expiry;
        # this per-call record only carries the frozen action/params and issue age
        # into the engine's unchanged verdict, echo and capture-timing checks.
        record = {
            "action": challenge["action"],
            "params": dict(challenge["params"]),
            "issued_ms": now - int(challenge["age_ms"]),
            "expires_ms": now + challenge_mod.EXPIRY_MS,
            "used": False,
            "binding": (operation, user_id),
        }
        # The stored glow schedule (single-turn-glow-v1): the engine's flash check reads it.
        if "glow" in challenge:
            record["glow"] = copy.deepcopy(challenge["glow"])
        with challenge_mod._lock:
            challenge_mod._issued[nonce] = record
        try:
            failure, embedding, live = main._secure_analysis(scan, operation, user_id)
        finally:
            with challenge_mod._lock:
                challenge_mod._issued.pop(nonce, None)
        if failure is not None:
            body = json.loads(failure.body)["error"]
            return self._rejected(t, body["code"], body["message"], failure.status_code)
        pad = {"outcome": "live", "policy": self.pad.POLICY_VERSION}
        if operation == "enroll":
            t.set("outcome", "enrolled")
            result = {
                "quality": {
                    "score": t.fields["pad"]["minimum_detection_score"],
                    "frames_embedded": t.fields["pad"]["usable"],
                },
                "liveness": main._liveness_summary(live),
                "pad": pad,
            }
            t.emit(200)
            return Analysis(True, result, self.encode(embedding))
        if operation == "verify":
            with t.step("match"):
                score = max(main.cosine_similarity(embedding, v) for v in vectors)
            matched = bool(score >= main.PLACEHOLDER_THRESHOLD)
            t.match(score, matched, main.PLACEHOLDER_THRESHOLD)
            t.set("outcome", "match" if matched else "no_match")
            t.emit(200)
            return Analysis(
                matched,
                {
                    "pad": pad,
                    "match": matched,
                    "score": score,
                    "threshold": main.PLACEHOLDER_THRESHOLD,
                    "liveness": main._liveness_summary(live),
                },
            )
        t.set("outcome", "live")
        t.emit(200)
        return Analysis(True, {**live.as_dict(), "enforced": True, "pad": pad})

    @staticmethod
    def _rejected(t, code, message, status):
        t.emit(status)
        return Analysis(
            False, {"error": code, "message": message}, error=code, status=status
        )
