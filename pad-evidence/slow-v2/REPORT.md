> HISTORICAL RELEASE REPORT: preserve this evidence, but do not use its next steps or deployment commands as current instructions. Read E:\Factech\HARDENING_PLAN.md and pad-evidence/consent-v6/REVIEW.md for the current handoff and last verified baseline.

# Slower capture evaluation — round 2

Build: `eval-pad-943dc39ab702`. Policy: `pad-sequence-v2-slow`.

The user reported rushed left/right prompts after physical enrollment attempts.
Capture remains bounded to 12 frames, now nominally 1400 ms apart (15.4 seconds, typically about 16 seconds including phone overhead). Face forward for 3.2 seconds; the second direction starts randomly at 9.0/9.2/9.4 seconds. Each turn therefore has roughly six seconds. Prompts ask for a small turn with both eyes visible, and a stationary phone. No extra biometric frames, model downloads or inference cost were introduced.

Backend challenge validation permits 15000..20000 ms spans and 1100..1900 ms frame gaps, with 1600 ms transition allowance. PAD coverage still requires 9/12 usable frames, two per third, start/end coverage, all usable frames classified live and pairwise continuity >=0.55. Maximum usable gap is 3800 ms to preserve allowance for one unavailable sample at the slower cadence; two consecutive missing samples still fail. Heuristic timing now uses this sequence's cadence instead of the legacy 500 ms nominal cadence. The recognition (0.55), liveness (0.50), PAD class decision and rotation magnitude thresholds are unchanged. The 30-second bound, nonce expiry/consumption/binding and inference/memory limits are unchanged. Slower sampling lowers temporal resolution; this is a usability change, not evidence of improved attack resistance.

## Recorded round-1 observations

Read-only metadata and score review; no frame images were transferred or relabeled. Captures 8–12 are labelled bona_fide/self/daylight/glasses and enrollment, all P01: **5 rejected / 5 recorded labeled genuine enrollments**, zero accepted. Which records correspond to the user's stated two attempts is not established. Labels are preserved as recorded and are not independent proof of capture origin. No new attack-validation denominator is available. Identity matching was not reached; similarity is unavailable, not zero. All five have 12 usable frames and [4,4,4] PAD coverage. Each has failed head-sequence validation. Capture challenge.ok indicates nonce validity only; head_sequence.ok is the physical-action result.

| Capture | Request ID | Overall rejection reason | PAD live-class frames | PAD mean live-class score | Head-sequence reason | Backend ms |
|---|---|---|---|---|---|---|
| 8 | `097afe3631cb47259310603b181d04fa` | liveness check failed: challenge | 12/12 | 0.96143 | action_order_or_rotation | 2733 |
| 9 | `af10316f9e92491f95d5bb04d776702e` | PAD insufficient_evidence: face_discontinuity | 12/12 | 0.93712 | not_frontal_and_still | 2660 |
| 10 | `1ec281478fdf4e2d97326a6a5d391308` | PAD insufficient_evidence: face_discontinuity | 12/12 | 0.92655 | action_order_or_rotation | 2425 |
| 11 | `c67f30c4b8b04be7894404af9eadd518` | PAD spoof: non_live_frame | 8/12 | 0.59064 | not_frontal_and_still | 3967 |
| 12 | `27c2313135a949a795f3fb56c9ef7b1a` | PAD spoof: non_live_frame | 0/12 | 0.22934 | action_order_or_rotation | 2180 |

Captures 9–10 had every PAD frame classified live but continuity failure; their very large landmark excursions suggest large turns may contribute, which cannot be confirmed without a controlled fresh attempt. Captures 11–12 had 8/12 and 0/12 PAD live-class frames. Slower timing does not address a model false rejection directly. All these observations are development evidence for this usability change, never independent validation of v2. Scores are uncalibrated model outputs.

## Validation of this change

151 affected engine tests passed across the initial 150 passing cases plus the corrected old challenge-parameter contract. This includes the native ONNX wiring checks, fail-closed endpoint/nonce/face-switch checks and 22 added slow timing/coverage cases. The only initial failure was the test's old expected issued timing; updated to the explicitly versioned schedule. SDK: 56 session cases plus one new insufficient-nonce-window case passed; typecheck/build passed. UI: 29 guided-page cases plus a new sequence-specific rejection-message case passed. Ruff passed. Counts across targeted runs are disjoint here; no redundant full baseline or unchanged model conversion/parity rerun. Mocked/simulated tests establish timing and enforcement, not fresh phone performance.

Weekly usage: start 60%, after local checks and immediately before deployment 61% (+1 percentage point). One agent used.

## Fresh physical round

Open https://facetech.31.97.186.120.sslip.io/ and refresh. Confirm policy pad-sequence-v2-slow. Use P01, self, accurate lighting/glasses metadata and explicit capture consent. Start a fresh genuine enrollment target `padr2-p01`. Keep the phone fixed, face forward until instructed, then turn a little toward your own prompted left/right while keeping both eyes visible. Hold each position until the next prompt. Expect about 16 seconds plus server processing. If enrollment succeeds, do one genuine verification against the same ID; if it rejects, retain the receipt and review before further tests. Send request IDs or capture numbers. Attack enrollments later must use `padr2-p01-photo` and `padr2-p01-video`; attack verifications use the genuine target. Follow the existing full test protocol with r2 identities. Do not mix round-1 and round-2 denominators. Policy remains frozen until reviewed.

## Rollback

On the VPS:
```
cd /opt/facetech-releases/eval-pad-80a3dfa1dfe9
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build engine gateway
```
This restores the previous mandatory-PAD build with its faster challenge, preserves the capture mount, and clears volatile engine templates/nonces on restart. No capture deletion/export is part of deployment or rollback.

## Deployment verified

All 72 release files (including unchanged model checkpoints and ONNX files) verified on VPS; image build loaded and warmed PAD. Staging real-model HTTP test: 12 usable frames, PAD spoof rejection, slow timing accepted, nonce reuse/binding enforcement and protected diagnostic stripping passed, 2465 ms processing. Deployed gateway enrollment rejected the public upstream fixture with PAD spoof, request `24f4618617fd4d81a1a4d9015a3492fa`, 2415 ms measured HTTP/check overhead. These static synthetic sequences are enforcement evidence, not physical attack tests. No recording consent was sent; no new capture was saved. Served SDK SHA-256 matches the local compiled build: `fb42da606da066b20c1d43e2f25f246b766b94c96db347d22f22e3cb7f2aedcd`.

Public HTTPS health returned build eval-pad-943dc39ab702; root and review returned 401 without credentials. Engine/gateway healthy, Caddy unchanged. Credentials, capture mount/database, seven-day retention verified equal before cutover. All eight existing metadata/decision records compared exactly equal afterward. Engine memory about 400 MiB; gateway 41 MiB. Original laptop port 8000 remains listening in PID 6408. No operations were run against the original E:\Facetech source/environment. Rollback images/releases retained. Exporter remains disabled. No VPS capture images were transferred.

Immediately before live cutover weekly usage remained 61% (+0 since local testing, +1 since this follow-up began). No new physical attempts on v2 have yet been reviewed. Changed source: engine challenge/rotation/PAD coverage/heuristic timing; SDK capture workflow; evaluation page/copy; corresponding tests; README/policy. Exact hashes are in changed-files.json and release-manifest.json. Existing model provenance/parity and original deployment evidence remain in ../IMPLEMENTATION_AND_DEPLOYMENT_REPORT.md. No model conversion or pretrained weights changed.
