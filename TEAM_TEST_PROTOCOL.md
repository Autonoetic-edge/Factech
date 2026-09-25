# Facetech labelled team test protocol

Updated 19 September 2026 for engine `eval-pad-0d87e7b9a807`, gateway
`eval-pad-0e45ad88290f`, UI `ui-v6`, policy `pad-sequence-v2-slow`.
Use https://facetech.31.97.186.120.sslip.io/?v=ui-v6 and existing credentials.
This is a pilot to identify failures and collect reviewable evidence, not a production
accuracy claim. [HARDENING_PLAN.md](HARDENING_PLAN.md) governs implementation;
hardening proceeds separately while the team continues this frozen test round.

Capture takes approximately 16 seconds: 12 frames at 1400 ms, initially frontal,
then the server-issued ordered head turns. Follow the current page prompts, not
the eight-second instructions in the historical round-1 document. Consent is given
once per tester/version in this browser and remembered across attempts/reloads;
verify the selected tester is correct. Unticking withdraws future recording.

## Recording and test setup

- Use the existing test site and review page. Keep the existing credentials private.
- Recording requires explicit consent, including for people depicted in attack media. Keep nonconsenting people outside the full camera frame, not just the preview oval.
- Set the participant code, test case and lighting before each attempt. Labels describe the known test setup, never whether the engine passed it.
- Use a distinct test `user_id` for each enrolled identity. Record which identity is claimed separately from the person actually presenting. A wrong-person verification is a live person, so its PAD label remains `bona_fide` and its case is `different_person`.
- Start with Samsung A13 and daylight. Note browser/version, camera, glasses, orientation and the exact presentation device where relevant. Change one condition at a time.
- Keep liveness at 0.50 and matching at 0.55. Depth and moire remain advisory. Do not restart the engine between enrollment and verification: enrollments exist only in memory.
- Confirm the saved-capture receipt after each consenting submitted attempt. Missing receipt/diagnostics are missing evidence, not a successful save. A cancellation before upload has no frame record.

## First pilot block

Use consenting participants and a predetermined order. Three attempts per condition is a practical debugging block, not a statistically sufficient validation set. Keep every failure and restart; do not replace failed first attempts with successful retries.

| Block | Setup and UI case | Main question |
|---|---|---|
| Genuine baseline | Enroll self, then three separate `self` verifications in daylight | Are capture, enforced liveness and matching consistently successful? |
| Genuine room light | Three `self` verifications against the same enrollment | Does indoor exposure or motion blur cause failures? |
| Genuine stress | Three each in `dim` and `backlight`; glasses conditions recorded separately | Which signal or capture stage fails, and why? |
| Wrong identity | A second consenting live participant verifies against the first person's existing `user_id`; choose `different_person`; do not enroll that person into the first identity | Liveness may pass, but recognition should not match. A liveness rejection leaves recognition unmeasured. |
| Printed photo | Choose `print`; use consented media depicting the enrolled identity; test a still presentation and a presentation following the requested approach as separate conditions | Can a flat presentation satisfy the active motion checks? |
| Phone photo | Choose `screen_photo`; use a phone display and consented media depicting the enrolled identity; record still and moving presentations separately | Does liveness reject a displayed photograph even when the requested movement is attempted? |
| Phone video | Choose `screen_video`; use a phone display and consented video depicting the enrolled identity; record display brightness and movement | Does liveness reject a replay, including one with motion? |

Physical presentation tests use the ordinary test UI. API injection, virtual cameras and challenge tampering need a separate authorized test setup. The UI currently maps both screen cases to `screen_phone`; laptop-screen and virtual-camera tests must not be entered under those cases. The current UI cannot label them accurately.

Keep enrollment and verification results separate. A spoof accepted during enrollment is a different failure from a spoof passing liveness during verification. Test spoof enrollment only with a dedicated disposable test identity after documenting the baseline; do not contaminate a genuine participant's enrollment.

## Review each attempt in place

Use the protected review page. Start with frame indices 0, 3, 7 and 11, then inspect all 12 for failures, borderline results or inconsistent motion. Review timing and challenge parameters alongside the images rather than inferring them from appearance.

Record the following in the approved review workflow; this protocol does not export captures:

- Capture/request/build identifiers, claimed identity, presenter code, endpoint, independent label/case, lighting and device.
- Saved receipt and diagnostic availability, camera restarts, cancellations, elapsed capture time and final response/error.
- Face detection count, usable/settle/approach frame counts, frame gaps and embedded count.
- Each voting liveness signal, its raw measurements, score, threshold and failed-signal reason. The overall liveness score is the minimum voting signal, not a probability that a face is real.
- Depth and moire measurements, explicitly marked advisory. Missing measurements are unavailable, not zero.
- Similarity and match decision only when computed. A request rejected before matching provides no recognition score.
- Reviewer observations such as glare, blur, clipping, multiple faces, insufficient settle frames or wrong movement. Keep observations distinct from causal conclusions.

Compare a failure with a successful attempt under the same setup. Investigate guidance/timing, face detection and frame selection before proposing algorithm or threshold changes. A clear image alone does not establish spoof resistance.

## Counting results

Maintain separate counts for initiated attempts, cancellations before upload, submitted attempts, saves, missing diagnostics, PAD decisions and matching decisions. Report first attempts and retries separately.

Report genuine liveness rejections separately from overall genuine authentication failures. Report attack liveness passes by presentation type and photo/video case, and separately report attacks that also matched. A spoof rejected by recognition after passing liveness is still a PAD failure. A live wrong-person nonmatch is a correct recognition result, not a PAD rejection.

For matching, distinguish genuine self-verification nonmatches from wrong-identity matches. State the denominator of attempts that reached matching and report attempts blocked before matching separately. Do not mix enrollments into verification rates.

Show missing classes as unmeasured. Repeated attempts from one person do not provide independent participant coverage. Select any candidate algorithm or threshold using a development group; freeze it before testing separate participants and sessions. Agree device/lighting/attack coverage and acceptance criteria before that validation. The offline PAD sweep selects and scores on the same corpus and excludes unavailable measurements; it cannot approve deployment.

## Storage restriction and security follow-up

The prior capture-download command was rejected by automatic approval review with only `blocked by policy`. Its scope and remediation are unknown. Do not retry that transfer through another tool, create a replacement download script, or enable the evaluation exporter. There is no verified local backup from that attempt. Local source review and synthetic tests can continue independently.

Keep the VPS records while backup is unresolved, subject to the existing seven-day automatic retention. Not deleting manually does not suspend automatic expiry. Resolve the approval restriction through the environment's supported approval/policy process before attempting an export; no such resolution is established here. Do not treat this document or a test log as a backup of frames or scores.

The current shared demo credential permits review and deletion. A separate change should isolate reviewer routes (including images, metadata and DELETE) from tester credentials, with a narrowly scoped receipt route for the capture UI. Verify role boundaries, direct gateway exposure, response caching and CSRF behavior before deploying that change. No new credentials or access changes are part of this protocol.

The older export/relabel file-move/manifest defect remains unresolved and evaluation exports remain disabled. Any future repair must be tested with synthetic data under injected file-move and manifest-write failures before real captures are involved. Earlier phone rejections without stored frames cannot be reconstructed from diagnostic logs.
