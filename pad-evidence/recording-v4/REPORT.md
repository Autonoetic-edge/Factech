> HISTORICAL RELEASE REPORT: preserve this evidence, but do not use its next steps or deployment commands as current instructions. Read E:\Factech\HARDENING_PLAN.md and pad-evidence/consent-v6/REVIEW.md for the current handoff and last verified baseline.

# Consented recording UI v4

Gateway/UI release: eval-pad-c9a87c4f2266. Interface: ui-v4.
Engine remains eval-pad-0d87e7b9a807, policy pad-sequence-v2-slow.
One agent, working only in E:\Factech. Original E:\Facetech unchanged.

## Diagnosis and limits

The ui-v3 form fetched recording status once per page lifetime. A successfully
parsed response lacking enabled=true or evaluation_only=true permanently blocked
saving for that page, with no retry. This code path explains the reported error
condition, but the response seen on the Samsung was not supplied. Neither caching
nor a particular network/proxy failure is established as the cause.

The user subsequently reported confusion between the two recording options and
requested removal of processing without saving. Selected-consent behavior now has
an integrated form/page regression test. The original Samsung-specific failure is
not claimed reproduced or conclusively resolved until a new phone attempt works.

## Changes

- Removed processing-without-saving completely from this evaluation page. Enroll
  and verify require explicit consent and recording metadata, even if a missing
  or mismatched form returns null.
- Fresh authenticated same-origin recording check before camera access, unique
  request query, no-store, redirects rejected, eight-second timeout. Strictly
  validates enabled/evaluation flags, supported consent version and retention.
- Check recording again button recovers without a reload. Visible diagnostics
  include response HTTP/type and relevant policy fields; no frames or credentials.
- Consent selected message distinguishes checkbox state from storage availability.
  Status/metadata preflight failures preserve the selection. Completed/cancelled
  attempts reset it. Withdrawal during preflight/capture cancels; a late response
  cannot open the camera after cancellation, hiding or navigation.
- Duplicate taps cannot start overlapping attempts. Existing photo/video identity
  separation remains. New generated targets use r4- prefix for this UI round.
- Saved receipt remains required to establish storage. Missing/failed receipts
  instruct the tester to stop and report the request ID. No unsaved recovery claim.
- Corrected one invalid UTF-8 separator in HTML; no PAD, timing, threshold, model,
  SDK, gateway Python, storage or exporter changes.

## Validation

62 affected form/page tests passed, including real form + page + SDK wiring,
selected consent reaching X-Capture-Meta, stored receipt, reset, disabled/malformed
status, HTTP/auth/HTML/network/JSON errors, bounded timeout, recovery, fresh checking,
consent withdrawal, cancellation, hidden page and repeated taps. Tests use simulated
camera/network data; they are not physical usability or attack-efficacy evidence.
Final diagnostic value formatting and indentation cleanup passed syntax checks.
Unchanged model/security/backend suites were not rerun.

All 72 release checksums verified; exactly three packaged UI files differ from the
previous release. Initial staging guard compared complete Caddy compose objects
and stopped because the release-relative bind path differs. Corrected the guard
to compare Caddy settings/environment (Caddyfile content already hash-identical).
No live service was changed by that failed staging check. Staged gateway passed
served-content/status checks with zero captures. No synthetic scan was submitted.

Deployed only gateway with --no-deps. Engine and Caddy container IDs, images and
start times verified unchanged. Gateway/engine healthy. Caddy's gateway connection
serves exact ui-v4 page/scripts. Public health remains old engine build as intended;
unauthenticated root, recording status and review remain HTTP 401.

Gateway environment, persistent capture mount/database and seven-day retention
preserved. All eight pre/post metadata/decision records compare exactly equal.
Status enabled=true, evaluation_only=true, total=8, write/bank/purge failures=0.
No image/scan payload endpoints fetched, no biometric frame transfer, no capture
or label deletion/change, no exporter enabling. No verified local capture backup.
Original laptop port 8000 remains listening in PID 6408.

## Physical evaluation state and next attempt

No new physical attempt yet; saved captures remain 5-12. Earlier genuine enrollment
results remain 5 rejects / 5 labelled genuine attempts on the older fast build.
No new learned-PAD physical attack denominator. Similarity not reached is unavailable.

Open https://facetech.31.97.186.120.sslip.io/?v=ui-v4 on Samsung, verify interface
ui-v4 and Recording is ready. Use the same tester code for the same person; choose
Genuine person - self and accurate glasses/lighting. Use a fresh r4- target, e.g.
r4-p01 only for the same P01 participant. Select Save this attempt and confirm
Consent selected. Enroll once with phone fixed, forward initially, then small turns
toward the instructed own left/right with both eyes visible. Send request ID and
Recording saved capture number. If status blocks, send Recording check details.
If enrollment succeeds, verify the same target once with renewed consent. Review
receipt, PAD/head challenge, coverage, similarity and latency before further tests.
Physical attacks/wrong-person/glasses/lighting variation remain pending; use separate
-photo/-video attack-enrollment identities and freeze policy per round.

## Rollback (gateway only; preserves live engine state)

    cd /opt/facetech-releases/eval-pad-0d87e7b9a807
    docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build --no-deps gateway

Old gateway image also tagged facetech-mock-gateway:rollback-recording-v4.
Current release .env retains the old engine image intentionally. Staging gateway
stopped. Captures and prior releases retained.

## Weekly account usage

Start 63% used (+0 points from supplied prior reading).
After local implementation/testing 64% (+1 point).
Before live deployment 64% (+0 since testing, +1 overall).
