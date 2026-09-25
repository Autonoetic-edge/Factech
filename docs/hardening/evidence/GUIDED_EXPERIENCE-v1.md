# Guided participant experience

Date: 2026-09-19. Status: **local design and interactive preview only**.
User request: make the overall process more guided, sequential and understandable.
Authority: HARDENING_PLAN.md, ARCHITECTURE.md and ACCEPTANCE.md remain unchanged.
No milestone is completed by this proposal. M2 remains the next implementation step.

Open [the interactive preview](guided-experience.html) directly in a browser.
It has no dependencies, network calls, camera access, authentication, storage,
inference or real results. A restrictive CSP blocks network/media requests.
All state is in memory; preview controls manually advance sample states.
Do not connect this artifact to live services or use it to collect participants.

## Participant journey

1. **Sign in** — use the participant's own account. No typed subject ID, delegated
   enrollment or administrator selection of someone else's account. Show the
   signed-in identity and provide the real identity provider's account-switch flow.
2. **Privacy** — explain required template use separately from optional evaluation
   recording. Optional recording starts off. Display purpose, authorized reviewers,
   retention, withdrawal and deletion information before the choice. Read previously
   granted, current-version consent from the server; do not force repeated agreement
   on every returning check. The returning preview assumes existing template consent.
3. **Get ready** — request camera permission following a user action. Show framing,
   lighting and phone-position guidance before issuing a short-lived challenge.
   Give one corrective instruction at a time. Start capture on an explicit action.
4. **Face check** — show one current instruction with a matching head illustration,
   optional user-enabled spoken guidance and a visible Stop action. Use server-issued
   challenge parameters and SDK timing events. Show processing only after submission;
   release the camera as soon as it is no longer needed.
5. **Result** — state setup saved, verification passed, rejected, retryable failure,
   or uncertain completion accurately. Provide one next action. Show recording status
   separately from the biometric decision. Put technical details behind an expander.

For returning participants, collapse already-satisfied sign-in and consent steps,
while keeping identity and privacy choices accessible. Never infer current consent
from localStorage. The preview keeps all five steps visible so the design can be
reviewed, and does not implement this production optimization.

## Capture guidance and frozen behavior

The example progresses from facing forward to a small turn in each prompted
direction. The preview uses a fixed sample order; production must read first_sign
and switch_ms from the actual challenge. Preserve HEAD_SEQUENCE, settle time,
turn switching, 12 frames at 1400 ms, matching/PAD gates and the challenge TTL.
The sample head diagram is illustrative, not proof of correct pose or liveness.
Map participant-left/right correctly for mirrored previews and validate on phones.
Do not substitute eye movement for a head turn, require a full profile, or instruct
the participant to return to center during a phase that requires holding the turn.

Use short phase-specific copy:
- "Face forward. Hold still."
- "Slowly turn a little to your left. Then hold."
- "Slowly turn a little to your right. Then hold."
- "Checking your result. Your camera is off."

Source inspection found legacy move-closer wording in apps/shared/messages.js
alongside HEAD_SEQUENCE-specific wording in the SDK and integration page. This
does not establish that every legacy message is displayed in the current flow.
Before implementation, map actual call sites and remove contradictory guidance
only in the separate hardened UI. No frozen source file was edited for this design.

Progress must describe observed capture/submission states, never invented success
percentages. Do not shorten capture, retune thresholds or add a new passive PAD
method through a UI change. A future liveness-policy experiment needs a separate
versioned round and held-out genuine/attack evidence.

## Failure and recovery rules

| Situation | Participant message / next action | Backend condition |
|---|---|---|
| Camera denied | Allow camera access in browser settings, then retry | No challenge spent or capture submitted |
| Face not framed / poor lighting | One actionable framing/lighting cue | Guidance is advisory; server owns acceptance |
| Liveness not confirmed | Explain start-still and small-turn instructions; new attempt | Never classify the participant as an attacker |
| No match | This attempt did not match the account's saved face | Do not auto-enroll or select another identity |
| Service busy before capture | Wait, then retry | Honor Retry-After; no endless automatic attempts |
| Sign-in expired | Sign in again as yourself | Recheck identity/consent; never reuse old challenge |
| Disconnect after submission | Check the existing result | M2 idempotency/status; no automatic second enrollment |
| Stop before submission | Stopped; start again when ready | Stop tracks/timers/work; discard transient frames |
| Leave after submission | Explain that a submitted operation may finish | Cancel does not undo commit; retain safe result reference |
| Optional recording failed | Show recording failure separately | Never manufacture biometric success/failure from storage status |

The preview includes first setup/returning modes, permission denial, sample capture
phases, success, unclear capture, liveness rejection, no match, busy/expired states,
stop and uncertain-result handling. "Saved result found" is an explicit design
control, not a server lookup. Preview switches are not part of the participant UI.
Existing-template management, actual account switching, spoken guidance, automatic
status polling, optional-recording failure and background/resume remain specified
behaviors to implement and verify; this preview does not claim to implement them.

## Delivery order and evidence

- **M2**: durable templates/challenges, current-authority idempotency, operation
  outcome recovery and explicit capacity failures underpin reliable navigation.
- **M3**: consent versions, optional recording, withdrawal, actual deletion and
  encrypted retention must support every privacy promise before rollout.
- **M4/M5**: bounded requests, reliable error taxonomy and strict acceptance predicates;
  integrate the proposed UI with the supported SDK without altering frozen timing.
- **M6**: keyboard/screen-reader flow, reduced motion, narrow screens, direction
  mirroring, permissions, slow networks, background/resume and Samsung A13 trials.
  Count first attempts, retries, abandonment and time to completion separately from
  recognition/PAD efficacy. No improvement in these metrics is measured yet.
- **M7/M8**: recovery/release checks and a coordinated new testing round before live use.

Local verification for this design: JavaScript syntax, offline/resource inspection
and comparison of all 100 protected baseline file hashes. No application behavior
was changed, so the unchanged 588-test suite is not rerun or reported as a new run.
Visual/interaction checks, if performed, are recorded below separately from tests.

Verification record, 2026-09-19: JavaScript syntax and offline resource/API inspection
passed; all 100 protected baseline hashes matched. The first syntax-check launcher
failed because Windows' default encoding could not encode the preview's checkmark;
explicit UTF-8 corrected the launcher and the check passed. Browser visual and
interaction verification is **pending**: Browser Use rejected the local file URL
under its browser security policy. No alternate browser, URL or serving workaround
was attempted. This is not a production-ready or device-validated interface.

Final source-only checks also confirmed unique static HTML IDs. Weekly usage was
5% before design work and 5% after checks: +0 percentage points at rounded
account-wide checkpoints. No services were started, no captures accessed, and no
live or E:\Facetech changes were made.
