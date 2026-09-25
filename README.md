# Facetech

## Start here: current work and document map

Read [HARDENING_PLAN.md](HARDENING_PLAN.md) to continue implementation. It is the
authoritative milestone checklist and session handoff. The team is actively testing:
keep the live VPS capture UX, model/policy, timing and thresholds stable; implement
hardening locally and in isolated staging before a coordinated live cutover.

- [Hardening architecture](docs/hardening/ARCHITECTURE.md) and [acceptance contract](docs/hardening/ACCEPTANCE.md): M0 complete; self-service only.
- [M0 design evidence](docs/hardening/evidence/M0.md), [M1 completion evidence](docs/hardening/evidence/M1.md) and [M2 completion evidence](docs/hardening/evidence/M2.md): durable identity/session/HTTP authorization, real Keycloak synthetic staging, and the frozen engine pipeline behind the Analyzer port with model-bound PostgreSQL templates, idempotent operations and a synthetic restore drill; 666 tests pass after [M3 consent/privacy completion](docs/hardening/evidence/M3.md). [M1 runbook](docs/hardening/M1_RUNBOOK.md) documents the fresh self-enrollment transition. Legacy services and live testing unchanged; M4 is next. [M3 runbook](docs/hardening/M3_RUNBOOK.md) records key, purge and recovery contracts and open M7 dependencies.
- [Latest review and verified deployment baseline](pad-evidence/consent-v6/REVIEW.md):
  gateway eval-pad-0e45ad88290f / ui-v6, engine eval-pad-0d87e7b9a807.
- [Current team protocol](TEAM_TEST_PROTOCOL.md): consented physical testing and counting rules.
- [Frozen biometric policy](engine/models/anti_spoof/POLICY.md): pad-sequence-v2-slow.
- Reports under pad-evidence document their named historical releases. Their older
  NEXT WORK sections and deployment commands do not override the hardening plan.
- [Historical round 1](pad-evidence/PHYSICAL_TEST_ROUND_1.md) used a different policy;
  do not follow its timing or pool its outcomes with the current round.

Use E:\Factech explicitly. Preserve the original E:\Facetech and its port-8000
service. The local examples below require unused ports; they are not instructions
to replace an existing listener. The deployed evaluation UI redirects other origins
to the canonical VPS site; local UI testing needs an isolated test harness/configuration.

Browser face capture, a TypeScript SDK, and a Python biometric engine. `packages/face-auth` (the hardened gateway) serves the web pages and forwards requests to the engine; it is what production runs, via `deploy/amfatec`.

This is a controlled evaluation prototype. Recognition thresholds are not calibrated
for production. Learned PAD and ordered movement checks are mandatory, but physical
photo/video attack resistance under the current policy has not been established.
Templates and challenge nonces are held in memory. Restarting the engine clears them.

## Project layout

| Path | Purpose |
|---|---|
| `engine/app` | FaceScan validation, detection, embeddings, matching, liveness and API |
| `engine/models` | Pinned ONNX model files |
| `engine/eval` | Offline recognition and presentation-attack evaluation tools |
| `packages/face-sdk` | Camera lifecycle, challenge timing, encoding and transport |
| `packages/face-auth` | Hardened gateway: authentication, authorization and the engine proxy |
| `apps/verify` | Self-service enroll/verify page served by the hardened gateway |
| `apps/shared` | Shared guidance, messages and assets |
| `apps/vendor` | Pinned browser detector assets and verification script |
| `deploy/amfatec` | Local/staging stack for the hardened gateway |
| `tests-contract` | API error-code and client contract checks |
| `tests-integration` | Engine integration smoke checks |
| `deploy` | Deployment script |

## Requirements and setup

- Python 3.12
- Node.js 22.18 or later
- A webcam and a browser with camera permission
- HTTPS for remote camera access; localhost is supported for development

Run from the project root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r engine\requirements.txt pytest httpx ruff==0.14.14
npm --prefix packages/face-sdk ci
npm --prefix packages/face-sdk run build
.\.venv\Scripts\python.exe engine\scripts\download_models.py --verify
```

Model weights and browser detector assets are included in this working copy. To restore missing assets:

```powershell
.\.venv\Scripts\python.exe engine\scripts\download_models.py
bash apps/vendor/fetch.sh
```

Keep the dependency license notices with redistributed files. The Python vendor headers identify the InsightFace source. Browser detector assets retain their upstream notices and pinned bytes. Font licenses are beside the font files. Check the applicable model-weight terms before distributing or commercially deploying the models.

## Run locally

Use two terminals. Set the same `ENGINE_API_KEY` in both. The value below is for local development only.

Engine:

```powershell
cd E:\Factech\engine
$env:ENGINE_API_KEY = "local-development-only"
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Gateway: the hardened gateway in `packages/face-auth` requires PostgreSQL and a
configured Keycloak realm; it is not a single-process dev server. Run its stack with
[`deploy/amfatec`](deploy/amfatec) (`docker compose -f deploy/amfatec/compose.yml up`),
which serves `apps/verify` and the built SDK together with the engine. See
`packages/face-auth/README.md` for the durable-integration requirements and the
account/registration scripts under `deploy/amfatec`.

## 1. FaceScan format

The payload is a MessagePack map with `version`, `device`, `frames` and `challenge` fields.

- `version`: integer `1`.
- `frames`: exactly 12 entries containing JPEG `jpeg_bytes` and strictly increasing integer `ts_ms`; `pose` is optional.
- `device`: capture metadata including `user_agent`, `screen` dimensions and `tz_offset`.
- `challenge`: the issued `nonce`, `action` and `params`, plus capture metadata used by the SDK.
- Encoded payload: at most 350 KiB. HTTP body cap: 481,964 bytes.
- JPEG limits: 1,280-pixel long edge, 1,000,000 pixels and aspect ratio at most 3:1.

The SDK records 12 frames at approximately 1400 ms intervals (about 16 seconds, policy `pad-sequence-v2-slow`) for the server-issued `HEAD_SEQUENCE` challenge (frontal hold, then both head-turn directions in randomized order/timing). Obtain a fresh challenge for every attempt with `X-Facetech-Operation: enroll|verify|liveness` and `X-User-Id` for enrollment/verification. Parameters are echoed unchanged. See [frozen PAD and challenge policy](engine/models/anti_spoof/POLICY.md).

## 2. API

Every `/v1/*` engine request requires `X-Engine-Key`. The gateway supplies this header; the browser must not hold the engine key. `/health` returns 503 until required models are loaded. Successful startup requires detection, recognition and checksum-verified PAD models; health is not evidence of biometric efficacy.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Process status, engine version and build ID |
| GET | `/v1/info` | Supported FaceScan versions and model information |
| GET | `/v1/challenge` | Issue a one-time challenge |
| POST | `/v1/enroll` | Add a template for a user |
| POST | `/v1/verify` | Compare a scan against that user's templates |
| POST | `/v1/liveness` | Require PAD, challenge and heuristic liveness; consume the bound nonce |
| DELETE | `/v1/templates/{user_id}` | Remove a user's templates |

Enrollment and verification accept either:

1. JSON: `{"user_id":"demo.user","facescan":"<base64 MessagePack>"}`.
2. Raw MessagePack with `Content-Type: application/msgpack` and `X-User-Id`.

Liveness accepts the same scan transports without a user ID. User IDs are trimmed, lowercased and restricted to 1–64 characters in `[a-z0-9._-]`.

Successful enrollment returns `template_id`, `quality`, `liveness` and a `pad` outcome. Verification returns `match`, `score`, `threshold`, `liveness` and `pad`. All three scan endpoints fail closed unless learned PAD, ordered challenge and heuristic liveness pass. Liveness failures now return an error, not HTTP 200. Similarity cannot override PAD failure.

`LIVENESS_ENFORCE` does not disable the required gates in secure HTTP endpoints; historical offline heuristic evaluation remains separate. The in-memory store retains up to five templates per user and 1,000 users, evicting the oldest entries at capacity. Use one engine worker because templates and nonces are process-local.

Decision responses include `X-Request-Id` and `X-Engine-Build`. The `facetech.decision` logger records signal decisions and timing without image bytes or embeddings. Set `FACETECH_BUILD_ID` to the release revision when building the engine image.

## 3. Errors

Coded failures use `{"error":{"code":"CODE","message":"description"}}`. Request-shape failures use HTTP 422 with `detail` instead. `BUSY` includes `Retry-After`.

### 3.1 Canonical error codes

| Code | Emitted by | Status | HTTP | Meaning |
|---|---|---|---|---|
| `UNAUTHORIZED` | engine | active | 401 | Missing or invalid engine key. |
| `PAYLOAD_TOO_LARGE` | engine | active | 413 | Payload or request exceeds the size limit. |
| `MALFORMED_SCAN` | engine | active | 422 | FaceScan structural validation failed. |
| `NO_FACE` | engine | active | 422 | No usable face was detected. |
| `MULTI_FACE` | engine | active | 422 | Enrollment requires a single face in the selected frames. |
| `USER_NOT_FOUND` | engine | active | 404 | The user has no stored template. |
| `MODEL_UNAVAILABLE` | engine | active | 503 | A required model file is unavailable. |
| `BUSY` | engine | active | 503 | Inference capacity is full; retry after the indicated delay. |
| `LOW_QUALITY` | engine | active | 422 | Insufficient settle-window evidence; capture again. |
| `LIVENESS_FAIL` | engine | active | 422 | Enforced liveness checks failed. |
| `CHALLENGE_FAIL` | engine | active | 422 | Missing, invalid, expired, spent or mismatched challenge. |
| `ENGINE_UNREACHABLE` | gateway | active | 502 | The gateway could not complete its engine request. |

### 3.2 Failure handling

`MODEL_UNAVAILABLE` means the engine responded without the necessary model. `ENGINE_UNREACHABLE` means the gateway did not receive a usable engine response. `CHALLENGE_FAIL` is a nonce/protocol failure; `LIVENESS_FAIL` is a signal failure. Use a new challenge when capturing again.

## Tests

Run from the root unless a working directory is shown:

```powershell
node --test "packages/face-sdk/tests/*.test.ts"
npm --prefix packages/face-sdk run typecheck
node --test "apps/tests/*.test.js"
.\.venv\Scripts\python.exe -m pytest tests-contract -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Engine, from `engine`:

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

With the hardened gateway and engine running (`deploy/amfatec`), `node apps/e2e_app_sdk.js` and `node apps/e2e_sdk_session.mjs` exercise the synthetic SDK pipeline against it. Synthetic fixtures validate software behavior, not biometric accuracy or presentation-attack resistance.

## Deployment

`docker-compose.yml` expects the separate production crypto gateway (`CRYPTO_IMAGE`) fronting the engine. Configure values from `.env.example`; never commit actual credentials. Build the SDK and verify model/browser assets before building images. Run Docker on the intended server or CI host. `deploy/amfatec` is the separate hardened-gateway (`packages/face-auth`) stack; see `packages/face-auth/README.md` for its data, consent and retention model.

Required third-party licenses and operational directives are retained. Local environments and dependency-install directories are not included; create them using the setup commands above.
