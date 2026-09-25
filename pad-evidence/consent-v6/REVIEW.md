> Implementation handoff: [HARDENING_PLAN.md](../../HARDENING_PLAN.md). This review records the 19 September snapshot; its findings remain evidence, while the plan governs future work. Live counts may change during team testing and automatic retention.

# Facetech senior engineering review — 19 September 2026

Assessment of the current evaluation system, based on source inspection, existing
validation reports, and the three newly recorded physical attempts. Scores are
engineering judgments against production readiness, not measured accuracy or
certification. This is a focused repository review, not a penetration test or an
exhaustive proof that all paths are defect-free. One agent performed the work.

## New physical results

All three records identify tester T072C65047, self / bona_fide, daylight, glasses,
engine eval-pad-0d87e7b9a807, and frozen policy pad-sequence-v2-slow. Labels are
tester-supplied; no frame images were downloaded or visually adjudicated.

| Capture | Operation/result | Learned PAD | Head sequence | Heuristic score | Similarity | Engine time |
|---|---|---|---|---:|---:|---:|
| 13 | Enrollment accepted | 12/12 live | Passed | 0.84426 | Not applicable | 2173.66 ms |
| 14 | Verification rejected | 12/12 live | not_frontal_and_still | 0.00000 | Not reached | 1980.19 ms |
| 15 | Verification matched | 12/12 live | Passed | 0.82286 | 0.98256 | 2304.76 ms |

Each submitted 12 frames, detected and embedded all 12, and provided PAD temporal
coverage [4,4,4]. Capture spans were 16.195, 16.263 and 16.347 seconds respectively;
engine processing is additional and does not represent complete user waiting time.
The mean live-class PAD scores were 0.995104, 0.995595 and 0.988999. These are model
outputs, not calibrated probabilities of authenticity. Similarity is a cosine
score against the enrolled template, not a percentage confidence in identity.

Capture 14's first three mouth/nose yaw proxies were 0.0399, 0.2308, 0.2156.
Their spread, approximately 0.1909, exceeds head_sequence.py's frozen 0.15 initial
stillness limit. Its median baseline is within the frontal limit; the spread is
the failing part of this combined check. Later samples also suggest a late second
turn, but the validator returns at the initial failure, so no second failure was
reported. Without visual review we cannot attribute the variation confidently to
actual movement versus landmark measurement noise. The zero heuristic aggregate
reflects the mandatory challenge failure, not a zero face-match score.

Nonce validation succeeded and was consumed for all three. The separate
challenge.ok field reports nonce/action/parameter binding; head_sequence.ok reports
the observed motion. Thus capture 14's nonce success and head failure are consistent.

Enrollment acceptance: 1/1. Verification acceptance: 1/2; recorded genuine-labelled
verification rejection: 1/2. Combined: 2/3 accepted, one rejected. These observations
come from one tester in one condition and are not population FRR/FAR estimates.
Earlier 5/5 genuine-labelled rejections belong to the older fast policy and must
not be pooled with this round. There are still zero recorded physical attack
attempts under the current learned-PAD policy. Older capture 7's accepted phone
photo belongs to the heuristic-only build and remains an unresolved historical
failure, not evidence that the current model accepts or rejects that attack.

Receipts for all three report stored=true and diagnostics=true. The database
metadata confirms consent storage-consent-v1 and nonempty scan payload lengths
180723, 179252, 177971 bytes. No payload bytes were read into local files.
Total records: 11; storage, banking and purge failure counters: zero; retention: 7 days.

Request IDs:
- 13: 4b80401c35304702aa0894b54895990b
- 14: 6bc5f0067fe5401f82732e96eb9a3d3e
- 15: c131af98fff44185b8357b239fb7fe1d

## Revised scores

| Senior perspective | Previous | Current / 10 | Main reason |
|---|---:|---:|---|
| Architecture | 7 | 7 | Clear SDK/gateway/engine boundaries and independent gates; volatile state constrains scaling and recovery. |
| Backend maintainability | 6.5 | 6.5 | Useful modules and structured traces; main.py retains legacy helpers, review accesses store internals, thresholds remain scattered constants. |
| Face recognition / ML | 4 | 5 | Consistent aligned embeddings and all-frame continuity; one successful physical match, but 0.55 remains an uncalibrated placeholder. |
| Liveness / spoof resistance | 2 | 4 | Mandatory learned PAD and server-validated ordered challenge materially improve the design; current physical attack efficacy remains unmeasured. |
| Security | 3 | 4.5 | Key isolation, nonce binding, bounds and hardened containers; shared principals, no user ownership checks or trusted camera origin. |
| SDK engineering | 7 | 7 | Typed transport, bounded requests and careful cleanup/cancellation; successful responses can still carry unknown/not-enforced liveness. |
| Frontend / capture UX | 4 | 6 | Working receipts, origin routing, repeatable consent and guided sequence; long capture and one rejected verification still show friction. |
| QA / biometric validation | 5 | 5.5 | Substantial automated contract/failure-path tests and reproducible model parity; extremely small physical sample and no current attack cohort. |
| Deployment / reliability | 5 | 6 | Versioned releases, staging, rollback, health checks and gateway-only cutover; single-host service and volatile enrollment recovery remain limits. |
| Privacy / data operations | 5 | 5.5 | Explicit consent, protected receipts/review and seven-day purge; shared reviewer access, client-asserted consent, and no verified capture backup. |

Unweighted arithmetic average: 5.7/10. Critical security and validation gaps cannot
be averaged away. Suitable for controlled evaluation; insufficient evidence for
production biometric authentication.

## What the models actually do

1. SCRFD det_500m.onnx locates faces and five landmarks. Input size/pixel bounds
   precede decoding. Multiple faces fail the mandatory PAD path.
2. ArcFace w600k_r50.onnx produces normalized 512-dimensional embeddings from
   aligned faces. The PAD path embeds every usable face, requires pairwise cosine
   continuity >=0.55, and averages the same embeddings. Enrollment stores that
   vector; verification compares against up to five templates and accepts the
   maximum cosine score >=0.55, only after every liveness gate passes.
3. MiniFASNetV2 and MiniFASNetV1SE use 2.7x/4.0x contextual crops resized to 80x80,
   BGR float32 0..255. Mean softmax argmax class 1 denotes live. Every usable frame
   must classify live, with >=9 usable frames and temporal coverage requirements.
   Missing evidence, invalid outputs, timeout and model failure cannot accept.
4. The server issues a one-use, identity/operation-bound 30-second nonce with a
   randomized head order and switch time. Five-landmark anatomical ratios validate
   the recorded sequence. This is a pose proxy, not measured 3D depth.
5. Duplicate, motion, timing, landmark and challenge checks provide additional
   gates. Depth and moire remain advisory and must not be described as effective
   anti-spoof defenses. Browser face guidance is UX assistance, not an authority.

Pinned conversion/checksum/parity evidence supports correct numerical integration.
It does not establish effectiveness against different displays, cameras, lighting,
injected media or unseen attacks. Frame unanimity may increase genuine rejection.
Six order/timing combinations provide limited challenge diversity. No models or
biometric policy were trained, tuned or changed during this consent deployment.

## Priority findings and work needed

**High — Authorization is evaluation-wide.** Caddy uses one shared Basic Auth
principal, the gateway attaches one engine key, and engine endpoints accept caller-
selected identities without principal ownership checks. A holder of evaluation
access can enroll additional templates or delete another identity's templates.
Review access is similarly shared. Before production: distinct authenticated
principals, role-based review access, identity-bound enrollment/revocation authority,
and attributable access auditing. No attack or destructive test was run here.

**High — Biometric efficacy has not been established.** main.py still names the
recognition threshold PLACEHOLDER_THRESHOLD. No populated ROC report exists in
engine/eval; tools alone do not calibrate a production threshold. Collect consented
independent genuine/impostor and print/screen-photo/screen-video trials across
people/devices/conditions, freeze policies, report denominators and uncertainty,
and use held-out attempts for any revised policy. Do not tune against these three
records and count them again as independent validation.

**High — Camera origin is untrusted.** Backend nonce checks prevent simple reuse
but do not prove physical camera capture. Learned RGB PAD must face replay,
screen movement and injection tests. Stronger guarantees require an explicitly
different capture/threat model; turning the head alone is insufficient.

**Medium — Enrollment recovery and scaling.** store.py keeps at most 1000 users
and five templates per user in process memory, evicting oldest entries at bounds.
Restart loses templates; independent replicas have different state. Design explicit
capacity behavior and an authorized protected persistence/recovery strategy before
multi-instance production operation. This deployment preserved the engine process.

**Medium — Resource protection depends on the edge.** Engine parsing and inference
have bounds, queue limits and deadlines; Caddy limits requests to 1 MB. Gateway
proxy reads request.body() before forwarding, and application code contains no
per-principal rate limiting. Keep the gateway private and add explicit admission
and abuse controls for a production exposure. No load-test capacity claim is made.

**Medium — SDK success semantics need tightening/documentation.** postScan parses
liveness into passed/failed/unknown/not-enforced while session.ts can return ok=true
for a shaped HTTP 200 regardless of that verdict. The deployed engine currently
enforces its gates, so no current bypass was demonstrated. Future integration
should reject contradictory successful responses or make the consumer's required
acceptance predicate explicit; verify ok=true also does not mean match=true.

**Medium — Privacy operations remain evaluation-grade.** Capture storage is SQLite
with raw biometric scans; no application-level encryption or individual reviewer
audit was found in the reviewed implementation. Host disk encryption was not
established. Consent is client-supplied metadata, not authenticated proof of a
person's consent. Seven-day retention is valuable but does not replace access
control or recovery design. The exporter remains disabled. No verified local
capture backup exists; no deletion or transfer is authorized by this report.

**Medium — Usability requires independent measurement.** The current head-position
test rejected one of two verification attempts despite high PAD live scores.
Measure first-attempt completion and rejection reasons before revising the policy.
Do not weaken a gate simply to turn a recorded failure into a pass.

Reviewed source includes engine main/anti_spoof/head_sequence/challenge/embed/store/
auth/facescan, gateway app/capture_store/evaluation, SDK session/run/transport,
capture UI and tests, model manifest/policy, Compose/Caddy and CI definitions.
Existing parity/security reports were reused; unchanged model/security suites were
not rerun, and hosted CI was not asserted to have run for these local edits.

## Consent change and deployment

ui-v6 remembers an explicit grant per tester code and consent version in this
browser's localStorage, and restores the last tester and enrollment target. It
does not store camera frames in browser storage. Enrollment/verification completion,
rejection or ordinary cancellation no longer resets consent. New tester codes need
their own grant; returning codes restore their existing grant. Unticking withdraws
future recording and cancels active work; existing saved captures retain their
seven-day expiry. Another tab's withdrawal/cleared storage is handled. If browser
storage is unavailable, consent lasts on the current page and the UI says so.
Clearing browser data, a new browser/device or a changed consent version can require
consent again. Previously selected per-attempt consent is not silently migrated.

Changing the tester or target while a capture is active cancels that capture.
Status checks remain fresh and strict, consent is rechecked after asynchronous
preflight, and every submitted recorded attempt still supplies the gateway's
required storage-consent-v1 metadata. User-authorized standing consent text is
visible beside the checkbox. This is an evaluation UX preference, not a new
authenticated consent ledger or a legal-compliance assertion.

76 UI/form/integration tests passed, including repeat enrollment then verification,
reload restoration, distinct/new testers, withdrawal, unavailable browser storage,
version mismatch, cross-tab withdrawal and identity changes during capture.
Only three runtime UI files changed; 72 release checksums verified. A PowerShell
pipe appended a trailing carriage return after staging completed, causing a final
shell error; staging was inspected successfully and cutover used an uploaded LF
script. Both deployed services are healthy and exact ui-v6 files are served via
Caddy's gateway connection. Protected public routes still return 401 without
Basic Auth; public health returns the intended unchanged engine build.

Gateway release: eval-pad-0e45ad88290f. Engine: eval-pad-0d87e7b9a807.
Engine and Caddy IDs/images/start times were verified unchanged. All 11 before/after
metadata and decision records compare equal. No scans were submitted by this work.
No VPS frame bytes were downloaded, records deleted/relabelled, or exporter enabled.
Original E:\Facetech was not edited; its port-8000 listener remains PID 6408.
The Samsung execution of the new remembered-consent flow is still to be confirmed
by the user; simulated browser tests and deployed-source checks are complete.

Gateway-only rollback:

```sh
cd /opt/facetech-releases/eval-pad-c574af5467d2
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build --no-deps gateway
```

Previous image tag: facetech-mock-gateway:rollback-consent-v6.
Weekly usage: 66% at start, 66% after implementation/tests, 66% before deployment:
+0 percentage points this turn at the requested checkpoints (account-wide rounding).
