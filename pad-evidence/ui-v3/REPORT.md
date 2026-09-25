> HISTORICAL RELEASE REPORT: preserve this evidence, but do not use its next steps or deployment commands as current instructions. Read E:\Factech\HARDENING_PLAN.md and pad-evidence/consent-v6/REVIEW.md for the current handoff and last verified baseline.

# Capture UI fix and read-only result review

Build `eval-pad-0d87e7b9a807`; capture interface `ui-v3`; security policy remains `pad-sequence-v2-slow`. One agent. This is a new physical test round because the UI/positioning behavior changed, with no reuse of old attempts as validation.

The user reported seeing come-closer instructions without turns and no consent request at the correct evaluation URL. VPS read-only metadata review found no captures newer than 12, and no new external submitted scan since the slower deployment. The exact latest phone attempt cannot be identified without its request ID. The deployed root did contain the consent form and recording was enabled, but the form was hidden until its status request completed. The pre-capture positioning guide could wait indefinitely on come-closer. These are confirmed code paths; stale browser assets are a possible additional cause, not a proven diagnosis of that phone attempt.

## Changes

- Visible recording/tester form, explicit save-with-consent or process-without-saving choice before camera access. Neither choice defaults on; reset after each attempt. No-save never attaches recording metadata. A missing form script blocks camera start. A recording-status failure is visible and blocks start.
- Create tester ID button creates a random pseudonymous code and fresh `r3-...` enrollment target. Keep that code for the same consenting person; use another code for another person. Existing records were not assigned new labels or IDs.
- Positioning prompts explicitly say positioning only. After at most 8 seconds of guide positioning, the actual server-issued capture starts even if the advisory face-size guide has not armed. Detected multiple faces/bystanders instead cancel before recording. Existing backend single-face, PAD, continuity and challenge gates remain mandatory. Detector initialization and phone permission time are separate from this positioning limit.
- HEAD_SEQUENCE still records 12 frames over about 16 seconds with the v2 slow cues. No PAD, identity, heuristic liveness, head-rotation or nonce policy change; same official models/checksums and CPU runtime.
- UI revision and live server build displayed; versioned entry scripts/SDK plus no-store HTML/JavaScript/status responses prevent future mixed stale releases. Open the fresh query link rather than relying on an already open tab.
- Package builder excludes local test caches. Only relevant frontend, gateway cache middleware, tests and packaging code changed. Source backups and hashes are in this directory.

## Saved results available at review

All captures below are from the older `eval-pad-80a3dfa1dfe9` release, labelled P01/bona_fide/self/daylight/glasses enrollment. All have 12 submitted, 12 face-detected, 12 embedded and 12 PAD-usable frames, with temporal-thirds coverage [4,4,4]. Identity comparison was not reached (unavailable, not zero). Heuristic liveness score was 0 because the mandatory challenge signal failed; this is separate from learned PAD scores.

| Capture | Request ID | PAD outcome / reason | Live-class frames | Mean live-class score | Head challenge | Backend ms |
|---|---|---|---|---|---|---|
| 8 | `097afe3631cb47259310603b181d04fa` | live / - | 12/12 | 0.96143 | action_order_or_rotation | 2733 |
| 9 | `af10316f9e92491f95d5bb04d776702e` | insufficient_evidence / face_discontinuity | 12/12 | 0.93712 | not_frontal_and_still | 2660 |
| 10 | `1ec281478fdf4e2d97326a6a5d391308` | insufficient_evidence / face_discontinuity | 12/12 | 0.92655 | action_order_or_rotation | 2425 |
| 11 | `c67f30c4b8b04be7894404af9eadd518` | spoof / non_live_frame | 8/12 | 0.59064 | not_frontal_and_still | 3967 |
| 12 | `27c2313135a949a795f3fb56c9ef7b1a` | spoof / non_live_frame | 0/12 | 0.22934 | action_order_or_rotation | 2180 |

Overall: **5 rejected / 5 recorded labelled genuine enrollments**. Learned PAD classified at least one frame non-live on 2/5 attempts; another 2/5 failed continuity despite all 12 frames being class-live; 1/5 passed the PAD gate but failed the head challenge. The labels describe the recorded test context, not independent proof of physical origin. Raw scores are uncalibrated. No new labelled attack attempts exist on the PAD release: attack accepts have no physical-test denominator yet. Old capture 7's confirmed screen-photo accept predates learned PAD. The uncertain photo-enrollment claim remains uncertain.

No capture JPEGs/scans were transferred. Read-only metadata/scores were retrieved through the gateway review API over SSH. Unsaved frames cannot be recovered. The disabled exporter remains disabled, and captures/labels were not deleted or altered.

## Checks

39 relevant UI cases passed across targeted runs: initial 36 passed, two old exact-string expectations failed on the new positioning prefix; both corrected and passed, with one added bystander-timeout case. Additional targeted overlap verified both turn prompts after a permanently far-face guide. 23 gateway cases passed, including no-store responses and existing proxy contracts. Ruff passed. Existing Starlette deprecation warnings remain. Model conversion/parity and unchanged backend policy tests were not rerun.

All 72 packaged file checksums verified on VPS; required PAD loaded during image build and readiness. Staged and deployed content checks verify visible form, explicit choice, tester generation code, bounded positioning, no-store assets and HEAD_SEQUENCE timing. These are automated/simulated checks, not physical camera evidence.

Deployment preserved credentials, capture mount/database, seven-day retention and existing Caddy. All eight saved metadata/decision records compare exactly equal afterward. Both new containers are healthy; public health shows the new build and unauthenticated root/review still return 401. Original laptop port 8000 remains listening in PID 6408. Original E:\Facetech source/environment and unrelated services were untouched. No image downloads from captures or exporter changes.

Weekly usage: 62% at investigation start; 62% after local testing (+0 percentage points); 62% immediately before deployment/cutover (+0). One agent.

## Fresh physical attempt

Open https://facetech.31.97.186.120.sslip.io/?v=ui-v3 and check interface ui-v3 / build eval-pad-0d87e7b9a807. Tap Create tester ID (or retain P01 for the same participant and use a new r3-p01 target). Choose Genuine person — self and accurate lighting/glasses. Explicitly consent to saving only if everyone shown has consented. Enroll once: position briefly, face forward at first, then small turns toward your own instructed left/right, phone fixed and both eyes visible. If enrollment succeeds, verify the same user_id once, making the recording choice again. Send the request ID/capture number. Review a failed enrollment before repeated attempts.

Subsequent photo/video enrollments must use isolated targets ending -photo/-video, and photo/video cases must remain distinct. Attack verifications target the genuine enrollment. Keep this policy fixed; a later policy change requires a new version and fresh validation attempts. This RGB candidate remains vulnerable to model/domain limitations, replay and untrusted browser capture origin; head turns alone are not replay protection.

## Rollback

On VPS:
```
cd /opt/facetech-releases/eval-pad-943dc39ab702
docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build engine gateway
```
Rollback images `facetech-engine:rollback-pad-0d87e7b9a807` and `facetech-mock-gateway:rollback-pad-0d87e7b9a807` retained. Restores the previous slow PAD release. Restart clears volatile templates/nonces; captures remain mounted. No capture cleanup is part of rollback.

## Deployed real-model enforcement check

Request `15773169cddc4402b112aa4e96bed1d9` through the deployed gateway returned HTTP 422 `PAD spoof: non_live_frame`, measured 2626.77 ms including check overhead. The test repeated the official public upstream F1 image, resized to 640-pixel long edge and JPEG quality 80 (28,459 bytes/frame). No recording consent was sent and the capture count stayed at eight. Sensitive diagnostic headers were stripped. This establishes active model enforcement, not phone-screen attack efficacy.

The first smoke fixture attempt used the larger original public JPEG and correctly received HTTP 413 for request size; no PAD inference result was claimed for that attempt. The bounded-fixture retry above passed. Initial docker cp to container tmpfs was refused by its read-only-root setting; the public fixture was then supplied through stdin into the writable temporary directory. No VPS capture frames were involved.
