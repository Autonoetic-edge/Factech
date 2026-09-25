> HISTORICAL RELEASE REPORT: preserve this evidence, but do not use its next steps or deployment commands as current instructions. Read E:\Factech\HARDENING_PLAN.md and pad-evidence/consent-v6/REVIEW.md for the current handoff and last verified baseline.

# Facetech mandatory PAD evaluation release

Implemented and deployed **eval-pad-80a3dfa1dfe9**, frozen policy
**pad-sequence-v1**, on 19 September 2026. One agent was used.

- Test: https://facetech.31.97.186.120.sslip.io/
- Protected review: https://facetech.31.97.186.120.sslip.io/review
- Active engine/gateway source: `/opt/facetech-releases/eval-pad-80a3dfa1dfe9`.
- Compose project remains `facetech-demo`. Existing Caddy container was retained.
- Engine and gateway are healthy. TLS validation succeeded normally; page,
  review, review API, `/v1/info` and SDK return 401 without Basic Auth.
- **Fresh physical test results: none yet; awaiting the tester.** See
  [the complete phone protocol](PHYSICAL_TEST_ROUND_1.md).

## Implementation

Work is in `E:\Factech`. No Git repository was assumed. Original source copies
for the edited implementation/test areas are in `pad-evidence/baseline`.
The original `E:\Facetech` source/environment was not edited or used to install
dependencies, and its port-8000 service was not stopped (listener PID 6408 observed).
A separate `E:\Factech\.venv-pad` was created for implementation and conversion.

The main changes are:

| Area | Files | Behavior |
|---|---|---|
| Learned PAD | `engine/app/anti_spoof.py`, `engine/models/anti_spoof/*` | Pinned official two-model CPU ONNX ensemble, exact preprocessing, four outcomes, checksum/readiness checks, bounded inference, temporal coverage and same-face continuity |
| Mandatory gates | `engine/app/main.py`, `detect.py`, `trace.py` | Enrollment, verification and liveness require PAD, ordered challenge and heuristic liveness; template creation/matching follows gates; predecode bounds; protected diagnostics |
| Server challenge | `engine/app/challenge.py`, `head_sequence.py`, `liveness.py` | Randomized two-direction head rotation with timing/order checks, operation/identity binding, one-use nonce including liveness; browser pose/pass flags unused |
| Capture SDK/UI | `packages/face-sdk/src/{types,transport/gateway,workflow/session,camera/capture}.ts`, rebuilt `dist/*`, `apps/integration-demo/*` | 700 ms sequence cadence and timed cues; safe attack-enrollment identity separation; glasses metadata |
| Gateway/review | `mock-gateway/app.py`, `capture_store.py`, `review.html` | Binding headers forwarded; PAD/sequence diagnostics retained with consent and shown separately from heuristic liveness/identity scores |
| Reproduction/package | `engine/scripts/{convert_pad,benchmark_pad}.py`, `engine/requirements.txt`, `engine/Dockerfile`, `.dockerignore`, `deploy/*`, `README.md` | Conversion and parity, pinned numerical runtime dependencies, offline model verification at build/startup, versioned archive/staging/cutover/rollback |
| Tests | New `engine/tests/test_secure_pad.py`, migrated engine policy tests, SDK/UI/gateway tests | Required gates, fail-closed paths, transport contracts, nonce binding, ordering, continuity, review and test hygiene |

[Changed files and SHA-256 inventory](changed-files.json) lists 48 implementation,
test and model artifacts. Deployment/validation scripts and this report are
additional supporting artifacts. The [release manifest](release-manifest.json)
pins all 72 packaged runtime/model files. Recognition model files were reused
from the existing VPS release only after their SHA-256 values matched locally.

The engine remains **database-free, not stateless**: bounded enrollment templates
and nonce records live in process memory. Restart clears them, so new genuine
enrollment is required. No persistent biometric storage was added to the engine.
Consented captures remain in the gateway with seven-day retention.

## Exact official model provenance

Repository: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing

Revision: `b6d5f04ad78778917853b25c778acef6d5626d15`.
Official files were obtained as Git blobs from this repository. Its log directory
contains a Windows-incompatible filename; only the required paths were restored.
The repository license is Apache-2.0, bundled in `engine/models/anti_spoof/LICENSE`;
no separate checkpoint license was found. Hashes are our verified hashes of the
pinned official content, not a claim of upstream signed releases.

| Official checkpoint | SHA-256 |
|---|---|
| `2.7_80x80_MiniFASNetV2.pth` | `a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0` |
| `4_0_0_80x80_MiniFASNetV1SE.pth` | `84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990` |

| Converted deployment file | SHA-256 |
|---|---|
| `2.7_80x80_MiniFASNetV2.onnx` | `6f27a4194ecaf355b5ee43afca868e271ddeff1535f65824d1cf14ebad748257` |
| `4_0_0_80x80_MiniFASNetV1SE.onnx` | `6f2e77cb8aca3789a1ecdae0cf647937a2c24aac2040aea367c6a0c61d04e0f7` |

The existing InsightFace hashes remain:

- `det_500m.onnx`: `5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a`
- `w600k_r50.onnx`: `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43`

Both PAD inputs are 80x80 BGR, float32 NCHW with values 0..255, **without**
normalization/grayscale/aligned-recognition crops. Context scales are 2.7 and 4.0;
crop clamping, inclusive coordinates and resize match upstream. Class 1 is live,
classes 0/2 are attacks with unspecified subtypes. Average the model softmax
vectors; upstream argmax must be 1. Scores are not calibrated probabilities.

Local policy additionally requires every usable frame to classify live, >=9/12
usable frames, >=2 in each temporal third, evidence near both ends, limited gaps
and pairwise identity continuity. These are provisional local decisions, not
upstream defaults or validated safe cutoffs. Existing heuristic **0.50** and
recognition **0.55** thresholds are unchanged; depth/moire stay advisory.
Full policy and reproducible acquisition/conversion instructions are in
[`POLICY.md`](../engine/models/anti_spoof/POLICY.md).

## Automated evidence and limits

- Conversion: nine inputs per checkpoint (three boundary/random crop cases plus
  six upstream sample/annotated sample images). Crops and tensors exactly matched;
  maximum absolute score error was **9.83e-7** / **1.25e-6**, classifications equal.
  This establishes preprocessing/numerical agreement, not spoof accuracy.
- Linux deployment portability: new deterministic inputs compared to upstream
  CPU outputs; maximum errors **7.15e-7** / **3.78e-10**, equal classifications.
- **42 new secure-PAD tests passed**, covering native model loading, missing/corrupt
  model, NaN/invalid scores, inference exceptions/deadline, one bad frame, temporal
  gaps, face switches, all three mandatory gates, nonce reuse/expiry/binding,
  invalid order/translation/scale, successful mocked paths, wrong-person mismatch,
  JSON/MessagePack/legacy encoding, canonical IDs, revoke and separate diagnostics.
  Most are explicitly mocked wiring tests. The native PAD-on-uniform-pixels test
  uses real ONNX outputs but mocked detections/identity/heuristic evidence.
- Initial broad engine run: 330 passed, 60 failed, 10 setup errors. Failures exposed
  retired policy expectations, e.g. spoof acceptance with enforcement off,
  reusable liveness nonces, MOVE_CLOSER-only acceptance and best-three embeddings.
  **54 obsolete test functions were replaced**, not silently skipped/xfail-marked;
  exact names and preserved originals are in `replaced-legacy-tests.json` and
  `baseline`. Current suite collects 349 tests. Retained real detector/recognizer
  cases plus two explicit real-PAD cases form the revised 12-case model guard.
- Affected engine reruns: 157 passed with one old parser-error expectation,
  subsequently fixed and passed; another affected subset had 99 passed with one
  health test lacking startup, fixed and passed. Final secure/review/health subset:
  **77 passed**. Error-contract suite: **13 passed**. Counts overlap and are not
  added together. A redundant final rerun of all unchanged tests was avoided.
- Gateway suite: **142 passed**; updated review-diagnostic tests and the new
  binding-header forwarding test passed afterward. Storage-consent, retention and
  deliberately disabled exporter regression coverage remained intact.
- SDK: **213 current tests passed across initial and affected reruns**. The first
  run's failures were confined to the session fixture's old unbound route; final
  session tests: **56 passed**, including new sequence cadence/cues/binding.
  TypeScript checking and SDK build passed.
- UI: original **136 passed**, affected subset **30 passed**, plus **3 new tests**
  for photo/video/glasses labels, sequence cue persistence and attack-enrollment
  separation. These are simulated browser/DOM tests, not physical camera evidence.
- Ruff passed for changed Python. Existing Starlette/httpx deprecation warnings
  remain. No physical efficacy assertion follows from these automated checks.

Performance uses repeated **public upstream still images**, resized/encoded as
browser-like fixtures, with 12 detections, 24 PAD calls and 12 continuity embeddings.
Local measurement: 2.36–2.40 s/scan and ~320 MiB RSS. VPS, under the intended
1.5-CPU/2-GiB engine limits: **1.91–2.22 s**, ~425 MiB benchmark peak RSS.
Staged HTTP processing: 3.12 s; deployed HTTP processing: **4.01 s**. Running
deployed engine used ~393 MiB and gateway ~40 MiB at the final snapshot.
These are small workload observations, not p95/load-test guarantees. Concurrent
isolated packaging checks took up to 8.36 s; CPU contention can increase latency.

Public F1/F2 classified spoof and T1 classified live. A repeated live-labelled
still is not a genuine capture and must fail other gates. These classifications
are only smoke evidence, not independent mobile-screen evaluation.

## Deployment validation

Versioned archive was staged separately with an empty capture mount and no host
ports. Both images built successfully, including model checksum/inference checks
inside the image. Runtime without PAD files refused startup with exit 3. The
native VPS all-gates test used an isolated process and seeded only its own volatile
template: all three operations rejected real-model spoof results despite potential
self-similarity ~1.0; the isolated template count stayed unchanged. Server time in
this test was a fixture, not evidence of physical challenge completion.

Cutover verified equality of existing Basic Auth hash, engine key, database path,
capture mount and seven-day retention without printing credentials. It recreated
only this project's engine/gateway and retained Caddy; unrelated services and
the laptop service were not changed. Staging containers were stopped.

Live checks:

- `/health` reports engine 0.2.0 and `eval-pad-80a3dfa1dfe9`.
- Native PAD HTTP rejection, request `e7de749da9ed46cd82b6951165cac308`:
  HTTP 422, `PAD spoof: non_live_frame`, 12 usable frames, ~4.01 s.
- Full gateway enrollment rejection, request `73460fe054174987ac26ff4ce93d3b3c`:
  HTTP 422, `PAD spoof: non_live_frame`; diagnostics header stripped.
- Reuse and wrong-operation submissions rejected. No consent was sent for these
  public-fixture checks; capture count stayed **3**, and no template was created.
- Served SDK SHA-256 matches local:
  `43a0e3af733c676339414d1724889a7a4bc9d667eee0e0dd707e2ff1c84205bf`.
- Pre/post metadata and scores for captures **5, 6, 7** compare identical. Capture 7
  remains the confirmed earlier screen-photo acceptance, request
  `74a887eac9e24a038869d2c8f3ca8359`, liveness .79793 and similarity .95553.
  Which earlier enrollment was a photo attack remains unknown; nothing was relabelled.

## Rollback

Previous release and credentials remain at `/opt/facetech-releases/eval-d36fa4946ddf`.
Previous images also have `rollback-pad-80a3dfa1dfe9` tags. Run over the existing
strict key-based SSH connection if rollback is required:

```
cd /opt/facetech-releases/eval-d36fa4946ddf
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build engine gateway
```

[`deploy/rollback_pad.sh`](../deploy/rollback_pad.sh) contains this procedure.
It restores the **older heuristic-only system with a confirmed screen-photo
failure**; stop the PAD round and tell testers if rolling back. Restart loses
in-memory enrollment/nonces. Preserve `/var/lib/facetech/eval-d36fa4946ddf`, the
existing captures directory. Do not delete, relabel or export its records.

## Data restrictions and remaining weaknesses

No VPS capture/JPEG transfer to the laptop was attempted. Metadata and score
review used the permitted SSH/docker-exec internal API path. There is **no verified
local capture backup**; no claim is made that VPS records are safe to delete.
The database exporter remains disabled and its source hash matches the pre-change
copy (`7b4f08111e70a3a90cdb6c393b5a0f5f5207f4aedf3ff2259319f66e0c13509c`).
Earlier unsaved frames cannot be recovered by this implementation. Existing
seven-day automatic retention still applies; it was not extended.

MiniFASNet remains a candidate vulnerable to domain shift, screen/camera/lighting
changes and unseen attacks. Source-image aspect ratio and InsightFace boxes differ
from the upstream Caffe demo; numerical preprocessing parity does not eliminate
that domain difference. Frame unanimity and continuity may reject genuine users.
The five-landmark rotation measure is anatomical relative pose evidence, not
trusted 3D depth. Six direction/timing combinations offer limited replay challenge
diversity. Neither head turning nor blinking alone stops replay. Browser media
can be injected or sourced from a virtual camera; RGB inputs do not attest origin.
Review authorization is still shared Basic Auth, not per-reviewer authorization.

Freeze the policy for the documented phone round. Investigate integration first
if screen attacks pass, then report model limitations and consider a better model
or provider with documented mobile-screen evidence. Any change requires a new
policy/build and fresh attempts; no threshold manipulation or tuning-set claims.

## Weekly usage

The usage request arrived after work had started, so no pre-start reading exists.

| Milestone | Weekly used | Change |
|---|---:|---:|
| First available, mid-work baseline | 57% | Baseline |
| After local implementation/testing | 59% | +2 percentage points |
| Before staging deployment | 59% | +0 points since local; +2 overall |
| Immediately before live cutover | 59% | +0 points since local; +2 overall |

These are account-wide integer readings, not isolated billing for this task.
