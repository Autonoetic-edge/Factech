# Facetech

Browser face capture, a TypeScript SDK, and a Python biometric engine. The included mock gateway serves the web pages and forwards requests to the engine.

This is a prototype. Face matching and movement checks are implemented, but recognition thresholds are not calibrated for production and the current checks do not reliably reject photographs or video presentations. Templates and challenge nonces are held in memory. Restarting the engine clears them.

## Project layout

| Path | Purpose |
|---|---|
| `engine/app` | FaceScan validation, detection, embeddings, matching, liveness and API |
| `engine/models` | Pinned ONNX model files |
| `engine/eval` | Offline recognition and presentation-attack evaluation tools |
| `packages/face-sdk` | Camera lifecycle, challenge timing, encoding and transport |
| `apps/integration-demo` | Guided capture page |
| `apps/console` | Diagnostic console |
| `apps/shared` | Shared guidance, messages and assets |
| `apps/vendor` | Pinned browser detector assets and verification script |
| `mock-gateway` | Development proxy and optional evaluation capture storage |
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

Gateway:

```powershell
cd E:\Factech\mock-gateway
$env:ENGINE_API_KEY = "local-development-only"
$env:FACETECH_ENGINE_URL = "http://127.0.0.1:8000"
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Open the guided page at `http://127.0.0.1:8080/` or the console at `http://127.0.0.1:8080/console/`. Both pages use the built SDK in `packages/face-sdk/dist`.

The console sends its requests through the SDK transport: 8 seconds for a challenge or status call, 20 seconds for a scan, and the `X-Request-Id` of a failed call is shown with the error. Leaving the screen or hiding the tab cancels the run, aborts its request and stops the camera. Cancelling cannot undo work the server already finished: if an enroll, verify or revoke reached the engine before the cancel, it may have taken effect, so check the Subjects screen or run it again. After a run the console keeps only metadata (sizes, nonce, frame SHA-256 hashes), never the frames or the FaceScan; it has no mode that keeps a scan.

The mock gateway is a development proxy. It does not implement individual account authorization or the external crypto gateway's sealing and persistence. Do not expose it as a production authentication service.

### Phone on the same Wi-Fi

A phone browser only opens the camera on HTTPS. Serve the gateway over TLS on the laptop's Wi-Fi address and keep the engine on `127.0.0.1`. No Docker, tunnel or browser flag is needed.

1. Make a local test CA and a certificate for the laptop's Wi-Fi IPv4 address (`ipconfig`). Keep these files outside the repository and delete them after testing. Replace `192.168.1.5` with your address; `openssl` ships with Git for Windows.

```powershell
mkdir $HOME\facetech-phone-cert; cd $HOME\facetech-phone-cert
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 7 -subj "/CN=Facetech local test CA" -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign"
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=192.168.1.5"
Set-Content ext.cnf "subjectAltName=IP:192.168.1.5`nextendedKeyUsage=serverAuth`nbasicConstraints=CA:FALSE" -Encoding ascii
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 7 -extfile ext.cnf
```

2. Install `ca.crt` on the phone as a trusted CA. Android: Settings, Security, Encryption and credentials, Install a certificate, CA certificate. iPhone: open the file, install the profile, then turn on full trust in Settings, General, About, Certificate Trust Settings. Remove it after testing.
3. Start the engine as above on `127.0.0.1:8000`. Start the gateway on the Wi-Fi address with TLS:

```powershell
cd E:\Factech\mock-gateway
$env:ENGINE_API_KEY = "local-development-only"
$env:FACETECH_ENGINE_URL = "http://127.0.0.1:8000"
..\.venv\Scripts\python.exe -m uvicorn app:app --host 192.168.1.5 --port 8443 --ssl-keyfile $HOME\facetech-phone-cert\server.key --ssl-certfile $HOME\facetech-phone-cert\server.crt
```

4. Allow the port for the phone only, from an administrator PowerShell, and remove the rule afterwards. The Wi-Fi network must be a Private network and must not isolate clients.

```powershell
New-NetFirewallRule -DisplayName "Facetech phone test 8443" -Direction Inbound -Protocol TCP -LocalPort 8443 -RemoteAddress <phone IP> -Profile Private -Action Allow
Remove-NetFirewallRule -DisplayName "Facetech phone test 8443"
```

5. On the phone open `https://192.168.1.5:8443/`. The browser must show a normal padlock with no warning. Anyone who can reach this port can enroll and verify through the gateway, so stop it when the test ends.

## 1. FaceScan format

The payload is a MessagePack map with `version`, `device`, `frames` and `challenge` fields.

- `version`: integer `1`.
- `frames`: exactly 12 entries containing JPEG `jpeg_bytes` and strictly increasing integer `ts_ms`; `pose` is optional.
- `device`: capture metadata including `user_agent`, `screen` dimensions and `tz_offset`.
- `challenge`: the issued `nonce`, `action` and `params`, plus capture metadata used by the SDK.
- Encoded payload: at most 350 KiB. HTTP body cap: 481,964 bytes.
- JPEG limits: 1,280-pixel long edge, 1,000,000 pixels and aspect ratio at most 3:1.

The SDK records 12 frames at approximately 700 ms intervals for the server-issued `HEAD_SEQUENCE` challenge (frontal hold, then both head-turn directions in randomized order/timing). Obtain a fresh challenge for every attempt with `X-Facetech-Operation: enroll|verify|liveness` and `X-User-Id` for enrollment/verification. Parameters are echoed unchanged. See [frozen PAD and challenge policy](engine/models/anti_spoof/POLICY.md).

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

Gateway, from `mock-gateway`:

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

With both local services running, `node apps/e2e_app_sdk.js` and `node apps/e2e_sdk_session.mjs` exercise the synthetic SDK pipeline. Synthetic fixtures validate software behavior, not biometric accuracy or presentation-attack resistance.

`node apps/e2e_guided_browser.mjs` runs the real guided page in an installed Edge or Chrome, headless, with the browser's synthetic camera and fixture responses. It checks the wording and colour for each result, cancel and camera denial. Add `--live http://127.0.0.1:8080` to drive a running gateway and engine instead. The synthetic camera shows no face, so no live run can succeed.

## Deployment and capture data

The private team evaluation deployment uses the existing HTTPS edge and a password-gated Caddy gateway. The guided page offers an unchecked consent control, a person code, test case and lighting. Only consenting submitted scans are retained; cancelled camera attempts have no uploaded frames. Test labels are independent of the engine verdict. The full camera frame is stored, including anything outside the preview oval.

`FACETECH_EVALUATION_DAYS=7` selects the database-only evaluation policy. Retention is persisted in the database and applies to scans and their linked diagnostics together, with hourly cleanup and cleanup before review reads. `/review` provides a protected frame viewer, measurements, capture receipts and deletion. `FACETECH_DIAGNOSTIC_HEADER=1` sends the engine decision only over the authenticated internal engine-to-gateway response; the gateway strips it from browser responses and stores it alongside the consenting scan. In this mode normal decisions are not also written into engine logs. Missing diagnostics or match scores mean unavailable/not computed, not zero. The engine remains database-free but has bounded in-memory enrollment templates and nonce state; restart loses enrollments. FaceScan encoding, recognition threshold 0.55 and heuristic threshold 0.50 are unchanged. Learned PAD has its own mandatory gate and versioned temporal policy.

Evaluation stores refuse the export command, including after restart without the evaluation environment variable. The known export/relabel failure-recovery defect is not repaired by this mode; managed file exports must remain disabled until separately repaired and verified. Captures are capped at 5,000 records and 350 KiB per scan; a receipt reports when an attempt was not saved. No raw-capture backups or independent exports are configured for the private pilot. Host/provider snapshots and manually copied data are outside the app's deletion guarantee. Team credentials permit all team reviewers to view/delete pilot captures; this is not individual account authorization or production authentication.

`docker-compose.demo.yml` runs the engine, mock gateway and password-gated Caddy demo. `docker-compose.yml` expects the separate production crypto gateway. Configure values from `.env.example`; never commit actual credentials. Build the SDK and verify model/browser assets before building images. Run Docker on the intended server or CI host.

Capture storage is disabled unless `FACETECH_CAPTURE_DB` is set. Storage also requires the matching consent marker in `X-Capture-Meta`; the current pages do not send it. Use `mock-gateway/export_captures.py --help` for labeling, exporting and deleting evaluation captures. Raw captures, exports, local databases and credentials are not included in this copy.

Stored captures older than 90 days are deleted when the store opens, every hour while the gateway runs, after a new capture, and on every `/capture-status` read, so no new traffic is needed. A failed purge is counted and shown in `/capture-status` as `purge_failures` and `last_purge_error`, and `export` refuses to run until a purge succeeds.

Every `export` rebuilds the destination folder from the labels in the store: a scan whose label changed moves to its new class folder, a scan no longer in the store is removed, and the folder gets one current `manifest-vN.jsonl`, `SHA256SUMS-vN` and `dataset.json` (version N). The store keeps an inventory of every exported file. `label` moves already-exported copies and rewrites the manifest; `delete-subject` and `delete-request` remove the matching exported files and manifest entries in every folder the tool exported to, even after the store row is gone. `purge` cleans the store only. The inventory does not cover copies made outside this tool (a folder copied elsewhere or sent to another team) or exports made before the inventory existed; those must be deleted by hand. Exported copies are not covered by the 90-day store purge until the next export rebuilds the folder.

Required third-party licenses and operational directives are retained. Local environments and dependency-install directories are not included; create them using the setup commands above.
