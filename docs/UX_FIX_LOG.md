# UX fix log

Work items from `docs/UX_AUDIT_AND_FIX_PLAN.md` Part 3, in order. One entry per step.
Backups of every edited file are under `C:\Users\hp\AppData\Local\Temp\claude\factech-backup\<step>\`.
Manual checks run against a scratchpad mock gateway (a Node server that serves `apps/verify`
with the panel's CSP and fakes `/auth/*` and `/v2/*`), driven by headless Chrome over CDP.
That is a page check, not a biometric check: the fake camera has no face in it.

## Step 1 — bfcache recovery (22 Sep 2026)

Files: `apps/verify/main.js` (`pageshow` handler: reload when `persisted`, else `RESUME`),
`apps/verify/flow.js` (`RESUME` clears `busy: 'leaving'` in every view; other `busy` values
are real operations in progress and are left alone — assumption, see the report).
Tests: `apps/tests/verify-flow.test.js` +1 ("coming back from the sign-in page re-enables the
buttons"). Page suites 37 → 38 pass.
Manual: mock gateway, 390×844 headless Chrome: Create account → mock registration page →
history Back → both buttons enabled → Create account works again; no console errors.
Deferred/unverified: the `persisted` (true bfcache) branch cannot be exercised in headless
Chrome; a real phone Back-button check is still owed.

## Step 2 — session lifetime (22 Sep 2026)

Files: `packages/face-auth/src/facetech_auth/oidc.py` (`Provider.refresh`, refresh grant with
the same client/response checks as the code exchange), `sessions.py` (`authenticate` refreshes
once when the provider token is expired/inactive and the cookie holder is inside the idle and
absolute limits; the engine, which is handed a token, never refreshes; the session document's
`expires_at` is no longer capped by the token's `exp`), `contracts.py` + `postgres.py`
(new repository duty `rotate_token`, same guards as `touch`), `apps/verify/main.js` (a warning
`SESSION_EXPIRED early` 30 s before `expires_at`), `flow.js` + `view.js` (`expiring` and `reauth`
outcomes, both lead to sign-in), `outcome.js` (`RECENT_LOGIN_REQUIRED` → `reauth`, no longer
shown as a timeout), `workspace.js` (401 copy: "Your sign-in has timed out. Sign in again to
see your workspace." with a plain Sign in to `/auth/login`), `docs/hardening/TEAM_ACCOUNT_SETUP.md`
(the kcadm `accessTokenLifespan=1800` alternative, documented and NOT run).
`policy.py` unchanged: `RECENT_LOGIN_SECONDS` stays 300. The authorize URL already carries
`max_age=0` (authlib 1.8 keeps it; checked by running the client), so "Continue to sign in"
already forces the password form; a test now pins that.
Tests: `tests-hardening/auth_fixtures.py` (MemoryRepo `rotate_token`, refresh grant on the
fake IdP), `test_oidc_sessions.py` +6 (max_age=0; refresh inside limits rotates the hash, keeps
csrf and the absolute expiry, engine accepts the new token, old token dead; no refresh past
idle/absolute, on a refused grant, or during an outage). Baseline `test_oidc_sessions` +
`test_http_authorization` = 262 passed; after the change the four suites `test_oidc_sessions`,
`test_http_authorization`, `test_http_edges`, `test_registration` = 374 passed (1 pre-existing
Postgres-gated skip); the full `tests-hardening` count is in the final report. `verify-flow.test.js` +1 → page suites 39.
ruff check/format clean on the changed Python.
Manual: mock gateway with a 34 s session: Prepare → warning screen at ~4 s ("about to time
out … Nothing is lost", Continue to sign in); `/workspace` signed out shows the new copy.
Screenshot `factech-backup/shots/d-step2-expiring.png`.
Deferred/unverified: `postgres.rotate_token` ran only through lint (the owned Postgres stack
was not started); the 6-minute test against a real Keycloak; Keycloak's handling of
`max_age=0` on the live realm; the warning timer is scheduled once at load and is not
re-armed after the gateway refreshes the token.

## Step 3 — guide load dead end (22 Sep 2026)

Files: `apps/verify/main.js` (`GUIDE_WAIT_MS` 5000 → 15000; guide prefetched on the sign-in
screen too; no page-side settle watch when there is no guide), `flow.js` (`guideLoading` flag
set on `CAMERA_OK`, cleared by the first `GUIDE` line or `GUIDE_OFF`; with `guideOff`, `PRIMARY`
starts the scan directly and `PREPARED` is ignored, so the fallback cannot fake readiness),
`view.js` ("Loading the face guide…" line; the fixed-oval line replaces "Camera guide
unavailable… reload"; Start sleeps while loading and, with a guide, until the face is placed;
with no guide it works by itself).
Tests: `verify-flow.test.js` +1 (loading flag) and the guide-off assertions rewritten
(fallback Start → `startScan`; stale `PREPARED` does nothing; guide dying mid-preparation
cancels it). Page suites 39 → 40 pass.
Manual: 390×844 mock gateway. Guide reachable: guide line, Start asleep with no face.
Guide blocked (fails fast): fixed oval and a working Start at once. Guide delayed 25 s:
"Loading the face guide…" with Start asleep, fallback Start at 15.0 s from the camera opening,
Start runs the SDK (forward cue shown), journey ends on the server's NO_FACE refusal.
Screenshots `m-step3-loading.png`, `m-step3-fallback.png` (the loading shot was taken
during the 0.2 s stage fade-in, hence the page showing through; the fallback shot is clean).
Deferred: no vendor download progress (the detector is loaded with a dynamic import, which
exposes none); a real phone on mobile data is still owed.

## Step 4 — restart in place, stream clone, early movement (23 Sep 2026)

Files: `apps/verify/bridge.js` (`handoff()` gives the SDK `stream.clone()`, so the SDK owns and
stops only the clone; new `reattach()` puts the page's own stream back on the preview after the
SDK's stop nulls it; `handedOff` removed), `flow.js` (`notice` field; capture `PREPARATION_RETRY`
returns to the camera view with `preparing: true`, `notice: 'moved'` and effect `restartScan`;
with no guide it just returns to the camera view with Start), `main.js` (`restartScan` effect
resets the preparation and detector without reopening the camera; the SDK cancel on a disturbed
settle is only turned into a restart when nothing was sent, the tab is visible and the camera is
still live; the settle watch already stopped at the first left/right SDK cue — unchanged),
`view.js` ("You moved. Hold still, starting again." stage line while `notice === 'moved'`).
Tests: `verify-bridge.test.js` (fake streams gain `clone()`; test 1 rewritten: the SDK gets a
clone and stopping it leaves the page's stream alive; +1 restart-in-place), `verify-preparation.test.mjs`
(camera stub gains `reattach`; early movement now asserts notice 'moved', zero reopen, one
reattach, zero submissions, notice cleared on the next capture), `verify-flow.test.js`
(retry assertions rewritten). Page suites 40 → 41 pass.
Manual: 390×844 mock gateway, guide blocked (the only way to start without a face): during
capture the preview runs on a clone with the page's own stream still live; ✕ before submission
posted zero scans; after the stop both streams are released; no console errors.
Deferred/unverified: the "You moved" line and the restart itself need a real face in front of
the guide (the fake camera never reaches `preparation.disturbed`); covered by the unit tests only.

## Step 5 — distinct stage lines, frame ring, settle bar, head on the stage (23 Sep 2026)

Files: `apps/verify/view.js` (STAGE_CUE: "Hold still. Getting ready." after Start, "Checking has
started. Hold still and look straight." on the SDK's forward cue, "Turn a little to your left and
hold it there." / "Now a little to your right and hold it there." on the turns; new pure
`stageText(state)` gives the line and the sub-line "Keep holding · Frame n of N", "Almost done.
Keep holding · …" from the third cue on; the frame ring's dash pattern is one segment per SDK
frame; the head guide's pose follows the SDK cue during capture; screen-reader forward line
matches), `index.html` (12-segment SVG ring inside the oval, a fill bar), `verify.css` (head guide
90 px top-right during capture only; ring; bar filling 1.2 s while preparing and 3.2 s on the
forward cue, hidden under reduced motion — decoration, it decides nothing; sub-line style),
`main.js` (the "frame alone changes nothing" repaint shortcut removed, so every SDK frame paints).
No page timer advances a cue: the line changes only on `SDK_CUE`, the count only on `SDK_FRAME`.
Tests: `verify-flow.test.js` +1 (`stageText` from flow state: starting → forward "Keep holding ·
Frame 1 of 12" → right "Almost done. Keep holding · Frame 11 of 12" → checking; each frame is a
new state). Page suites 41 → 42 pass. `tests-panel` inline-code and listed-files tests pass on
the edited HTML.
Manual: mock gateway, guide blocked (fallback Start), 390×844 and 1280×900: "Hold still. Getting
ready." → "Checking has started…" with the fill bar → "Keep holding · Frame 1 of 12" → the turn
line with ring segments equal to the frame index (3 = 3) and the bar gone → "Almost done. Keep
holding · Frame 7 of 12"; the small head turns with the cue (shot 2.5 s into the left cue);
journey ends on the server's NO_FACE. No console errors.
Screenshots `m-step5-started.png`, `m-step5-turn.png`, `m-step5-last.png`, `m-step5-head-turned.png`,
`d-step5-desktop-*.png`. (The desktop check's "top-right of the viewport" assertion failed only
because the camera area is centred at 960 px there; the head sits top-right of the camera area.)
Deferred: the 3.2 s bar length is the engine's settle constant written into CSS, not read from
the challenge; a real-face run to judge the turn size against the head is still owed.

## Step 6 — result reasons from the signal list, no raw scores, Retry-After (23 Sep 2026)

Files: `apps/verify/outcome.js` (imports `failedSignals` from `apps/shared/messages.js`, unchanged
and hash-protected; `whyNotLive` maps challenge → movement, landmarks → tracking, motion and
duplicates → notlive, timing → slowcamera, `PAD …` → spoof; the first failed signal in the
engine's order gives the advice, an unknown signal gives none; the same mapping on the
`decision.error` path; a non-200 reply's `retryAfter` is carried into the outcome),
`bridge.js` (`reply.retryAfter`: whole seconds from `Retry-After`, capped at 300 so a bad header
cannot lock the page), `flow.js` (`retryAfter` in state; a result with it fires `waitRetry` and
ignores PRIMARY until `RETRY_READY`), `main.js` (`waitRetry` effect: one timeout that dispatches
`RETRY_READY`; this is a result-screen timer, not a capture cue), `view.js` (reason lines as
instructions, the plan's wording; `scoreRows` deleted — no "Not checked", "Not passed" or
numbers on success or refusal; the request reference stays; busy lead "You can try again in
N seconds." with Try again asleep until then), `packages/face-auth/src/facetech_auth/panel.py`
(`shared/messages.js` added to `GUIDE_FILES` so the gateway serves it beside the guide files),
`tests-panel/test_panel.py` (the unlisted-path example swapped to `/shared/framing.js`).
Assumption: several failed signals → the first one in the engine's list is the advice shown.
Tests: new `apps/tests/verify-outcome.test.js` (3 tests: every signal string, the
`decision.error` path, Retry-After only with a value), `verify-flow.test.js` +1 (busy waits out
Retry-After; no header, no wait), `verify-bridge.test.js` +1 (a 429 with `Retry-After: 20`
reaches `classifyReply` as `retryAfter: 20`). Page suites 42 → 47 pass (four files now).
`tests-panel` 13 passed. ruff check clean; `ruff format --check` flags three lines in
`panel.py`/`test_panel.py` that were already unformatted in the backups and are left untouched.
Manual: 390×844 mock gateway, guide blocked but `/shared/messages.js` served: landmarks →
"We lost your face partway…", no "Not checked"/numbers, reference kept; "timing, challenge" →
the slow-camera line; PAD → the softer spoof line; no-match → no numbers; 429 with
`Retry-After: 6` → "You can try again in 6 seconds.", Try again asleep, awake after 5.8 s
with the plain busy line, then back to Prepare. No console errors.
Screenshots `m-step6-liveness.png`, `m-step6-busy.png`.
Deferred: the head-gate reason inside the LIVENESS_FAIL message (backend ask, M5); the deployed
page root must carry `apps/shared/messages.js` beside `apps/verify` (it already carries
`face-guide.js` from the same folder) — not checked on the VPS.

## Step 7 — copy pass (Part 2 items 1–8, 10–17, 19–22) and the signed-out chrome (23 Sep 2026)

Files: `apps/verify/index.html` (title, H1 and footer say "Face check"; sidebar "AMFATEC / Face
check / Face check for your AmFatec account.", "Self-service access" removed, breadcrumbs
"AmFatec / Face check"; ids for the H1 and the "Linked to…" line), `workspace.html` (same
sidebar and breadcrumbs; its own headings are item 23, out of scope), `workspace.js` (account
line in every branch: "Not signed in" / "Signed in" / "Couldn’t check"), `main.js` (account line
"Signed in as <preferred_username>" when the gateway sends one, else "Signed in"; ✕ during
capture asks "Stop the check? Nothing has been sent yet." with the browser's own confirm, never
before capture), `view.js` (sign-in: "Sign in to your account.", Create account leads when
registration is open with "I already have an account" as the second button, "Already set up?
Sign in."; consent: "Before we start." with what is stored, up to 30 days for this preview, how
to delete, no stage number; demo: "How the check works." with the three phases and their
durations, "Continue" + "Skip"; stage button "Start" with "Start switches on when your face is
in position." under it while asleep; iPhone camera hint in the denied notice; refusal keeps
verdict, one reason, one action, reference — the "Decided by the server" note is gone; expired:
"Your sign-in timed out. Sign in again to continue; your progress is safe."; success note
"Saved to your account." (set-up) / "Confirmed for your account." (verification); H1 by mode:
"Set up your face check" / "Confirm it’s you"; with no account the visual panel, step strip,
clock and "Linked to…" line are hidden and the copy column takes the full width; `COPY` export),
`verify.css` (three small rules: `.stage-note`, `.experience-body.solo`, and the second sign-in
button at the primary's width — the Part 3 row lists no CSS file; these are the least needed
for the hiding and the note).
Assumptions: item 12's phase order is written "one side … the other side" because the policy's
`first_sign` decides which turn comes first; item 22's "Saved to your account" is used for
set-up and "Confirmed for your account" for verification, where nothing is saved.
Not done: item 11 (a real contact route for template deletion) — the repo names none, so the
privacy dialog still says "ask your workspace administrator"; item 18 (landscape phones) and
item 23–24 belong to other rows.
Tests: `verify-flow.test.js` +1 (the Part 2 wording is pinned: expired lead, iPhone hint, "Good.
Tap Start."; no "server" in any result lead; the retired strings are absent from `index.html`
and `view.js`). Page suites 47 → 48 pass. `tests-panel` inline-code test passes.
Manual: 390×844 and 1280×900 mock gateway: signed out → new title/H1, Create account first,
no panel/strip/clock/linked line, "Not signed in"; signed in first time → "Before we start",
H1 "Set up your face check", panel back, no stage number; demo "How the check works" with
Continue/Skip and the durations; Prepare keeps "01"; camera screen with the guide → "Start"
asleep with its explanation; returning → H1 "Confirm it’s you"; ✕ during capture asks first,
No keeps capturing, Yes ends on "Stopped." with nothing sent. No console errors.
Screenshots `m-step7-*.png` (signin, consent, demo, camera) and `d-step7-desktop-*.png`.

## Step 8 — registration form hints (written, not applied) and Sign out on the main page (23 Sep 2026)

Files: `deploy/amfatec/init.sh` (a new realm's `realm.json` carries
`registrationEmailAsUsername:true`), `deploy/amfatec/registration.sh` (`enable` sets
`registrationEmailAsUsername=true` instead of `false`, so enabling registration no longer undoes
the hint), new `deploy/amfatec/realm-hints.sh` (`show | apply | revert` with kcadm on the running
realm: e-mail as the account name, first/last name optional and admin-edit only via the
declarative user profile in the new `deploy/amfatec/user-profile.json`; `apply` saves the
current profile first for `revert`), `docs/hardening/TEAM_ACCOUNT_SETUP.md` (new section
"Registration form hints (written, not yet applied)": procedure, check, rollback, what stays
theme work), `apps/verify/index.html` + `verify.css` + `main.js` (Sign out in the top bar, shown
once the account is known, POST `/auth/logout` with the CSRF token, then back to `/`; a 401 also
goes to `/`; any other failure re-enables the button with "Sign out didn’t finish. Try again").
NOT run against any realm: no kcadm, no VPS action. `bash -n` passes on the three scripts;
`user-profile.json` parses, with `required` only on `email`.
Deferred (theme work, documented in the setup doc): the 12-character rule as a field hint before
the first refusal; a line saying password reset by e-mail is not available. Unverified: the
user-profile JSON against Keycloak 26.7.4 (the image in `compose.yml`) — `apply` keeps
`user-profile.before.json` so a refused or wrong profile can be put back.
Tests: page suites stay at 48 pass; `tests-panel` inline-code test passes with the new button.
Manual: 390×844 and 1280×900 mock gateway: signed out → no Sign out; signed in → Sign out in
the top bar; click → one POST `/auth/logout` carrying `X-CSRF-Token` → sign-in screen, "Not
signed in", button gone. No console errors. Screenshots `m-step8-signout.png`,
`d-step8-desktop-signout.png`.

## Liveness Phase 1 — one short turn, pre-checks, auto-start (24 Sep 2026)

Plan: `docs/LIVENESS_UPGRADE_PLAN.md` Phase 1 (details `docs/SINGLE_TURN_PLAN.md` Parts 1–4).
Backups: `...\factech-backup\liveness-phase1\` (every file below as it was before this item).
Flag: `CHALLENGE_POLICY=single-turn-v1` on the engine. Unset or any other value = today's
HEAD_SEQUENCE, unchanged. The page has no flag: it switches screen on the action the engine sends.

Engine (protected; rows added to `HARDENING_PLAN.md`, approved by the user's in-session
instruction):
- `engine/app/challenge.py`: `selected_policy()`, `issuable_actions()` follow the flag; LOOK
  settle drawn from 1200–2000 ms (target band unchanged); capture-before-issue binding now runs
  for every action, not only HEAD_SEQUENCE.
- `engine/app/anti_spoof.py`: new policy name `pad-single-v1` (same rules as v2-slow, gap limit
  1400 ms instead of 3800 ms, so one missing sample is allowed and two are refused at 500 ms);
  `policy_for(action)`; `evaluate(..., policy)` defaults to v2-slow.
- `engine/app/main.py`: `_secure_analysis` accepts `challenge.issuable_actions()` ("current
  single turn required" when the flag is on and an old HEAD_SEQUENCE nonce arrives); PAD runs
  under `policy_for(action)`; responses report that name via `_pad_policy(scan)`.
- `engine/models/anti_spoof/POLICY.md`: appended "pad-single-v1" section; v2-slow text untouched.
- Cadence: Option A (500 ms × 12, the SDK's existing non-sequence path). SDK untouched.
- `liveness.py` untouched: its single-action verifier and 500 ms timing already existed.
Hashes (sha256, first 16): challenge.py v4 `1cdcee7c9a56140d`, before this item `2b4e080bbf0936ca`
(already differed from v4), after `000b3b1db4e3d23d`; anti_spoof.py `707f02992a267b58` →
`733d4fbcc4869062`; main.py `a147a667211bbe97` → `f8e738071fbedb5f`; POLICY.md
`811d1d169afc922c` → `34159a4e244c8c7f`. **No new manifest written:** `head_sequence.py` and
`challenge.py` already mismatched v4 before this item (earlier switch-trial work), and a
`--rebaseline` would sign those off too. The reviewer should rebaseline once both are accepted.

Page (`apps/verify`, SDK untouched):
- `cues.js`: `poseOfAction()` (LOOK_LEFT → left, LOOK_RIGHT → right, else null) and `TURN_SIGN`.
  Sign convention is assumed, not measured: if phones show it inverted, swap the two poses there.
- `flow.js`: `SDK_INSTRUCTION`, `SDK_PHASE`, `TURN_SAMPLE` (single turn only; phase moves only on
  SDK events); **auto-start**: a `good` guide line arms preparation by itself; Start stays only
  as the fallback when the face guide is off.
- `framing.js`: pre-checks in order one face → distance (guide cues) → frontal (|yaw| ≤ 0.30) →
  light (face luma < 55, or whole frame ≥ 60 brighter = backlight) → blur (Laplacian < 12);
  light/blur hints give way after 10 s of the same hint (plan §9). `READY_MS` 1200 → 800.
  Turn meter `turnStep()` (baseline = median hold yaw; < 0.35 more, ≤ 0.80 good, > 0.80 far,
  no face = lost; a zone must repeat 3 ticks). Display only, never cancels or advances anything.
  **LUMA_MIN / BACKLIGHT_GAP / SHARP_MIN are provisional, set by judgement, not measured.**
- `main.js`: instruction/phase events wired; the settle watch stops at `phase 'move'`; the guide
  keeps running during the turn only to feed the meter; frame luma sampled at 32×24 for backlight.
- `view.js`: single-turn lines "Hold still." / "Turn a little to your LEFT|RIGHT." (the direction is
  always a word), meter lines ("A little more." / "Good. Hold it there." / "A little less." /
  "Too far. Turn back until both eyes show."), countdown digits from `SDK_FRAME` only while
  turning, "Keep holding…" for the last second, never 0; reduced motion → "Frame N of 12" and no
  ring/meter; settle bar length = the engine's `settle_ms`; `aria-live` carries line + meter line.
  Pre-check lines "Face the camera." / "Find more light on your face." / "Hold the phone steady." /
  "Only you in the frame."; "Good. Hold still." Result lines per plan §6.3 (one shared line for
  every spoof reason). Demo/intro/time copy made policy-neutral ("Under 20 seconds").
- `head-guide.js`: ANGLE ±0.42 → ±0.20 rad (≈11°).
- `index.html` + `verify.css`: arrow on the turn side and the turn meter (both `aria-hidden`);
  preparation bar 0.8 s; settle bar uses `--settle-ms`.

Tests: engine 381 → 395 pass (new `engine/tests/test_single_turn.py`, 14: flag off/unknown keeps
HEAD_SEQUENCE, flag on issues both sides with 1200–2000 settle, binding for LOOK, verifier pass /
wrong way / no turn / moved in hold at 500 ms, timing 500 ms, pad-single-v1 gap limit, endpoint
wiring both ways); ruff clean. Page suites: `apps/tests` 223 → 241 pass (new
`verify-single-turn.test.mjs` 9, 2 new main.js wiring tests; 3 updated for the intended changes:
auto-start effect, "Good. Hold still.", 800 ms ready). `tests-panel` 13 pass. One console-app test
(`console.test.js` f12) failed once under the full parallel run and passed 3/3 alone — timing
flake, not in any touched file.
Not run: the browser e2e (`apps/e2e_verify_browser.mjs` needs the full local stack), screenshots,
and anything on a phone. Nothing pushed to the VPS.

Still open before the flag goes on (Phase 1 gate, owner's team round): honest first-try rate and
time vs the Phase 0 baseline; attack kit against pad-single-v1; phash duplicates at 500 ms; the
left/right sign on real phones; the provisional light/blur limits from logged luma/sharp.
Phase 2's nose check (to ship with this in `log` mode) is **not** part of this item.

## Liveness Phase 2 — nose check (log only), 500 ms duplicate/motion, pre-check trace (24 Sep 2026)

Plan: `docs/LIVENESS_UPGRADE_PLAN.md` Phase 2, plus the Phase 1 risk "near-duplicate frames at 500 ms".
Backups: `...\factech-backup\liveness-phase2\` (every edited file as it was before this item).
Approval: rows "(Phase 2)" added to `HARDENING_PLAN.md` BEFORE any protected edit, on the user's
in-session instruction. `head_sequence.py` NOT edited. NOT rebaselined.

Engine (protected):
- NEW `engine/app/geometry.py`: `nose_residual(detections, ts_ms, settle_ms)`. Homography fitted by
  least squares from the 4 non-nose points of ALL settle frames to each turned frame; the mean
  settle nose is projected; residual = |projected − detected| / inter-ocular. Turned = after the
  settle window and |yaw − settle median| ≥ 0.15 (`liveness._yaw_proxy`, the challenge floor).
  Record: `score` (p90), `settle_frames`, `turned_frames`, `baseline_yaw`, and per turned frame
  `{index, yaw, residual}`. Fewer than 2 settle or 3 turned frames = `inconclusive`, never a failure.
- `main.py`: `PAD_GEOMETRY` (default off; `log`; `on` behaves as `log`, nothing rejects in this
  phase). `_log_geometry` runs after liveness, before the PAD/liveness refusals and before
  matching, so rejected (attack) scans are logged too. An exception becomes
  `{"outcome": "error"}` in the trace, never a decision. The PAD policy name is passed on to
  `check_liveness`. The `X-Facetech-Precheck` header is recorded for direct `/v1` calls.
- `liveness.py`: `check_liveness(..., pad_policy=None)`. Only for `pad-single-v1`: `duplicates`
  allows a group of 5 (the longest hold, 2000 ms at 500 ms) instead of 3, and `motion` compares
  frames 3 apart (1500 ms, the v2-slow time scale) instead of neighbours. Thresholds, MOTION_FLOOR
  and MOTION_LIVE unchanged. The detail gains `max_identical_frames` / `pair_stride` only under
  that policy.
- `trace.py`: `geometry` and `precheck` fields, present only when set. `precheck` keeps
  luma/frame_luma/backlight/sharp/hint/capped as scalars; >256 chars or bad JSON = `{"unreadable": true}`.
Hashes (sha256, first 16), before → after: liveness.py `361e9153ad8d1f20` (= v4) → `cc9f048196f2a036`;
main.py `f8e738071fbedb5f` (Phase 1) → `4667f1717939cabd`; trace.py `cb42f51e4e5936c5` (= v4) →
`fe43a504271683d0`; geometry.py new `c6dcdf85cae73790`; HARDENING_PLAN.md `97d8c14582c432c3` →
`8da4743d80bb4d6a`. `hardening_verify_baseline.py`: 7 mismatched of 100 (the 5 from Phase 1 and
the switch trial, plus liveness.py and trace.py), 0 missing. Rebaseline is still the reviewer's
call once the switch-trial row is decided.

Pre-check transport (not protected): the page sends the reading as the `X-Facetech-Precheck`
header on the scan POST (and its recheck). The SDK and the FaceScan wire format are untouched.
- `apps/verify/framing.js` `precheckReading()`; `main.js` keeps the last reading from the guide
  loop and hands it to the bridge at start; `bridge.js` adds the header.
- `packages/face-auth` `http.py`: the gateway forwards the header (printable, ≤ 256 chars, else
  dropped) and the engine role puts it in the scan payload; `operations.py` passes it to
  `Analyzer.analyze(..., precheck=None)`; `inference.py` records it with `trace.precheck`.
  `tests-hardening/postgres_fixtures.py` fake analyzer takes the new keyword.

Tests: engine 395 → 415 (new `engine/tests/test_nose_geometry.py`, 20: real turn scores > 0.04
and grows with yaw; flat print swung or tilted < 0.005; noise keeps them apart; too few turned
or settle frames = inconclusive; mode default off; trace only when on; precheck bounded;
**4 byte-identical still hold frames + the live_left turn pass under pad-single-v1 and are still
refused without it**; a 6-frame hold and a 12-frame replay still refused; stride test; other
policies unchanged). Byte-for-byte check against the backed-up liveness.py: 240 of 240 identical
`as_dict()` outputs (5 fixture sets × as-is/4-still/replay × 500/1400 ms × 5 actions, with
policy absent and v2-slow). tests-hardening 731 pass, 100 skipped (Postgres-gated), including new
`test_precheck_header.py` (5). Page suites 241 → 244 (bridge header, `precheckReading`, main.js
wiring). tests-panel 13. ruff clean on changed files (`liveness.py` has an older format diff in
the timing signal that this item did not touch). The console f12 flake failed once in a full run
and passed alone, as in Phase 1.
Not run: e2e browser, phones, a real Keycloak or Postgres stack. Nothing pushed to the VPS.

Open: `GEOM_MIN` and the `on` mode come from the team round's shadow logs (starting guess 0.04).
The synthetic flat print only registers as a turn when it is swung hard (≈0.6 rad) and held close,
so on real prints the check may often read `inconclusive`. The shadow data will show how often.
The light/blur limits can now be set from `precheck` in the traces.

## Liveness Phase 3 — AENet beside MiniFASNet (log only, NON-COMMERCIAL demo only) (24 Sep 2026)

Plan: `docs/LIVENESS_UPGRADE_PLAN.md` Phase 3, flag `PAD_ENSEMBLE`, default `minifas`.
**AENet (CelebA-Spoof) is non-commercial research only: demo only, never in a product.**
This is recorded in `engine/models/anti_spoof/POLICY.md`, in `app/aenet.py`, in the exporter,
and in every trace record (`ensemble.license`).
Backups: `C:\Users\hp\AppData\Local\Temp\claude\factech-backup\liveness-phase3\` (main.py,
trace.py, POLICY.md, HARDENING_PLAN.md and this file, as they were before this item).
Approval: rows "(Phase 3)" added to `HARDENING_PLAN.md` BEFORE any protected edit, on the user's
in-session instruction. `head_sequence.py` NOT edited. `anti_spoof.py`, `manifest.json` and the
MiniFASNet models NOT edited. NOT rebaselined. Nothing pushed to the VPS.

Engine (protected):
- NEW `engine/app/aenet.py` (instead of a class in `anti_spoof.py`, which stays untouched):
  `mode()`: `minifas+aenet:log` and `minifas+aenet` are recognised, anything else = `minifas`.
  `AENetPAD` loads `models/anti_spoof/aenet/manifest.json` + ONNX, checks the sha256, reads crop
  scale / size / channel order / scaling / mean-std / live index from the manifest, 1 thread,
  2 s run timeout, softmax output validated. The frames are the 8 most frontal single-face settle
  frames (`liveness._yaw_proxy`; first 4 frames when the settle time is unknown), from which the
  5 sharpest (Laplacian variance of the crop) are scored; fewer than 3 = `inconclusive`. Record:
  per-frame `{index, yaw, sharpness, live}`, `median_live`, `latency_ms`, `over_budget` (> 250 ms).
  A missing model = `unavailable` / `model_missing`; any load failure is remembered until restart.
- `main.py`: `_log_ensemble` runs after `_log_geometry` (after PAD and liveness, before the
  refusals and matching, so attack scans are logged too). Under `minifas` it returns before
  doing anything. Otherwise the trace gets `ensemble = {mode, enforced: false, license,
  minifas: {outcome, frames, median_live}, aenet: record}` and a timing step `aenet`. An exception
  becomes `{"outcome": "error"}`. `minifas+aenet` behaves as log: the "either median below its
  threshold" rule is NOT wired. Model not warmed at start-up (`warm_models` unchanged), so a
  missing model can never stop the engine.
- `trace.py`: `ensemble` field, present only when set.
- `POLICY.md`: new "AENet ... NON-COMMERCIAL, DEMO ONLY" section appended; nothing else edited.
Not protected: NEW `engine/scripts/export_aenet.py` (next to `convert_pad.py`, rather than the
plan's `engine/tools/`): imports upstream `models/AENet.py`, exports only the single (N, 2)
live/spoof head with softmax (opset 17), checks ONNX/PyTorch parity (≤ 1e-4), times 10 local
runs, writes `aenet/AENet.onnx` + `aenet/manifest.json` (licence, upstream revision, checkpoint
and ONNX sha256, preprocessing). **Its preprocessing defaults (crop scale 1.0, BGR, /255, no
mean/std, live index 0) were written without the upstream source and must be checked against
upstream `tsn_predict.py` / `client.py` before the scores are trusted.**
Hashes (sha256, first 16), before → after: main.py `4667f1717939cabd` → `c092a796cb04df31`;
trace.py `fe43a504271683d0` → `488725f6be52879c`; POLICY.md `34159a4e244c8c7f` →
`96b0c5d2b74f6bc2`; HARDENING_PLAN.md `8da4743d80bb4d6a` → `b74c956a1992ae43`; aenet.py new
`ad1c631e2082e5d4`; export_aenet.py new `c9f36465e44861aa`; test_aenet_ensemble.py new
`e90ad276b681069c`; this file before `263954756b9a5090`. Unchanged: anti_spoof.py
`733d4fbcc4869062`, head_sequence.py `979f69037d6aaec0`, manifest.json `c8274623d794d716`.
`hardening_verify_baseline.py`: 7 mismatched of 100, 0 missing, the same 7 files as after
Phase 2 (main.py, trace.py and POLICY.md were already among them).

Tests: engine 415 → 430 (new `engine/tests/test_aenet_ensemble.py`, 15: mode parsing; frontal
pool = settle frames only, most frontal first; 5 sharpest of the pool scored; < 3 frames or
undecodable = inconclusive; missing model = unavailable and remembered; a tiny real ONNX stub
through `AENetPAD` (channel order, live index, checksum mismatch refused); crop stays in frame;
MiniFASNet median; trace only when on, never loaded when off; **an AENet P(live) of 0.0 changes
no response: flag off, log and `minifas+aenet` give the same verify body, `match: true`**).
Flag-off byte-for-byte: 72 requests (enroll/verify/liveness × 6 fixture scan sets × 500/1400 ms ×
head-sequence and single-turn-v1), real models, through the engine as it is now and through a
copy with the backed-up main.py/trace.py and no aenet.py: responses + trace lines (timestamps,
ids, nonces and timings stripped) byte-identical, 237 580 bytes, with `PAD_ENSEMBLE` unset,
`minifas` and an unknown value. The random turn direction and target were pinned (an unpinned
control run of the old engine differed from itself). 48 of the 72 reached PAD and the new call
site; all 72 were refusals (the fixtures are refused by PAD here), so the accepted path under
flag off is covered by the endpoint test above, not by this comparison. tests-hardening 731
pass, 100 skipped. ruff check + format clean on changed files.
Not run: the real AENet (no weights in the repo; not downloaded), the exporter, VPS CPU latency,
e2e browser, phones. Nothing pushed to the VPS.

Open: obtain the CelebA-Spoof checkpoint (non-commercial terms), verify the preprocessing against
upstream, export, measure latency on the VPS CPU (budget 250 ms), then run the kit + team round
in `minifas+aenet:log`. The default and the AENet threshold come from that data; if the pair adds
more than 2 points of honest rejections without catching more kit attacks, keep `minifas`.

## Liveness Phase 4 — soft colour glow, ENGINE PART ONLY (24 Sep 2026)

Plan: `docs/LIVENESS_UPGRADE_PLAN.md` Phase 4, engine side only. SDK and page NOT touched.
Backups: `E:\factech-backups\liveness-phase4\` (challenge.py, main.py, trace.py, head_sequence.py
for reference, HARDENING_PLAN.md, this file; each as it was before this item).
Approval: 4 rows "(Phase 4)" added to `HARDENING_PLAN.md` BEFORE any protected edit, citing the
user's in-session instruction. `head_sequence.py` NOT edited. NOT rebaselined. Nothing pushed,
nothing downloaded.

Engine (protected):
- `challenge.py`: `CHALLENGE_POLICY=single-turn-glow-v1` = single-turn-v1 (LOOK_LEFT/RIGHT,
  settle 1200-2000 ms, pad-single-v1) plus a glow schedule drawn by `flash.draw_schedule()`,
  stored in the nonce record and returned by `issue()` as an extra top-level `glow` key (only
  under that policy). `ChallengeVerdict.glow` (default None) carries the engine's own copy on a
  successful check; a client echo of `glow` is ignored. `_params_match`, params, EXPIRY_MS and
  HEAD_SEQUENCE untouched.
- NEW `flash.py`: palette blue/green/magenta/amber (no saturated red), neutral white; 3-4 colours,
  no colour twice in a row, first start 300-500 ms, steps 500-700 ms (50 ms grain), fades 400 ms,
  so at most 2 changes per second. `check()` measures forehead + both cheeks (boxes from the 5
  keypoints) per frame as rg-chromaticity, subtracts the pre-glow baseline, and takes the cosine
  correlation with the expected chromaticity shift of the displayed colour at each frame's time,
  at lags -2..+2 frames. Pass = best correlation >= FLASH_MIN 0.5 at |lag| <= 1; best lag 2 =
  `fail/lag`; otherwise `fail/low_correlation`. `inconclusive`, never fail: no schedule, no
  pre-glow baseline frame, baseline face luma > 200 or > 30 % clipped (`too_bright`), fewer than 3
  measurable glow frames, eyes < 12 px apart, undecodable frames.
- `main.py`: `FLASH_CHECK` off (default/unknown) = never run. `log`: `_log_flash` runs after the
  geometry and ensemble steps (so refused scans are logged too) and writes `flash`
  `{mode, enforced, outcome, reason, score, lag, correlations, gain, schedule, frames, ...}` plus a
  timing step. `on`: the same, and a `fail` is refused as LIVENESS_FAIL "liveness check failed:
  pad_flash", after the existing PAD and liveness refusals, before matching. An exception is
  `{"outcome": "error"}`, never a refusal.
- `trace.py`: `flash` field, present only when set.
Hashes (sha256, first 16), before → after: challenge.py `000b3b1db4e3d23d` → `36b68eae772a842d`;
main.py `c092a796cb04df31` → `dce0ff9350e6175f`; trace.py `488725f6be52879c` →
`588d283d7d3826b0`; HARDENING_PLAN.md `b74c956a1992ae43` → `2a932fab3aa053df`; flash.py new
`fb54ef8596ab0dc5`; test_flash_glow.py new `9c1ffb461734f68d`; this file before
`f8dda692de3eecfc`. Unchanged: head_sequence.py `979f69037d6aaec0`, anti_spoof.py
`733d4fbcc4869062`, liveness.py `cc9f048196f2a036`. `hardening_verify_baseline.py`: 7 mismatched
of 100, 0 missing, the same 7 files as after Phase 3.

Tests: engine 430 → 455 (new `engine/tests/test_flash_glow.py`, 25, synthetic non-biometric
frames: flat skin-coloured patch lit by ambient + glow, fixed keypoints): with colour passes
(0.99, lag 0); camera one frame late passes (lag 1); two frames late fails on lag; without colour
fails (0.30); lit by another challenge's colours fails (0.39); too much ambient light (luma and
clipped) = inconclusive; unmeasurable cases = inconclusive; schedule soft/slow over 300 draws;
fades; no glow outside the glow policy; glow bound to the nonce, tampered echo ignored; endpoint
through real JPEG: off never runs the check, log never refuses, on refuses only `fail` (sunny =
inconclusive = 200), error logged not decided. tests-hardening 731 pass, 100 skipped. ruff check +
format clean.
Flag-off byte-for-byte (Phase 3 method, `flagoff_drive.py` plus the issue() shape): 72 requests
(enroll/verify/liveness × 6 fixture sets × 500/1400 ms × head-sequence and single-turn-v1), real
models, through the engine now and through a copy with the backed-up challenge/main/trace and no
flash.py: responses + trace lines + issue() key sets byte-identical, 250 720 bytes, with
FLASH_CHECK unset, `off` and `bogus`. As in Phase 3, all 72 are refusals (48 reached PAD); the
accepted path under flag off is covered by the endpoint test, not by this comparison.
Not run: real faces, real phone screens, VPS, e2e browser.

Open: SDK + page session (timing contract in `engine/app/flash.py` docstring); then a team round
in `FLASH_CHECK=log` to set FLASH_MIN and check the turn-overlap and auto-exposure effects on
real phones.

## Liveness Phase 4 — soft colour glow, PAGE PART (24 Sep 2026)

Plan: `docs/LIVENESS_UPGRADE_PLAN.md` Phase 4, page side; timing contract in `engine/app/flash.py`.
**SDK NOT touched** (no approval rows needed: `apps/verify` is not in the protected manifest).
Backups: `E:\factech-backups\liveness-phase4-page\` (bridge.js, flow.js, main.js, view.js,
verify.css, index.html, this file; each as it was before this item). Engine not edited,
`head_sequence.py` not edited, NOT rebaselined, nothing pushed, nothing downloaded.

Why no SDK change: the SDK's `getChallenge` keeps only nonce/action/params, but every SDK request
already goes through the page's `bridge.js`, so the page reads `glow` from a copy of the challenge
reply there. The SDK fires its first `frame` event synchronously right after grabbing frame 0, on
the same `performance.now()` clock as `ts_ms`, so that event is the schedule's t = 0.

Page (`apps/verify`):
- NEW `glow.js`: `glowOf(body)` validates the schedule (else null = page unchanged);
  `glowColour(glow, t)` is `flash.displayed()` in JS (neutral before the first step, 400 ms linear
  fade to each step's rgb at start_ms, same fade back to neutral at end_ms);
  `reducedGlowColour(glow, t)`: prefers-reduced-motion = one slow linear change from neutral to
  the FIRST issued colour between its start_ms and end_ms, then held (no second change).
- `bridge.js`: `glow` reset on every challenge fetch, read with `res.clone()` on a 200 only;
  the response handed to the SDK is the same object as before.
- `main.js`: on a LOOK instruction with a glow, paints neutral at once (frame 0 = baseline lit
  by neutral), starts the schedule clock on the SDK's frame event index 1 (never a page timer),
  and repaints `--glow` per animation frame while the view is `capture`. Stopped on result,
  cancel, retry-in-place and pagehide. "The screen will glow softly while we check." shown the
  first time only (`localStorage` `facetech.glowNoteSeen`; no storage = shown every time).
- `flow.js`: `SDK_INSTRUCTION` carries `glow: true` / `glowNote` only when the challenge has a
  glow; otherwise the challenge state is exactly `{ pose, settleMs }` as before.
- `view.js`: `glowing` class on the stage for a glow challenge; the one-time line is the hold
  sub-line and is announced (`aria-live`); `COPY.GLOW_NOTE`.
- `verify.css`: `.stage-ui.glowing`: the oval's surround takes `rgb(var(--glow))` (lights the
  face), the hold bar is hidden (the glow replaces it), the line turns dark to stay readable.

Hashes (sha256, first 16), before → after: bridge.js `dc61602eae7dfae1` → `570f0dcb148422fe`;
flow.js `3f7e113813781867` → `9a930a3833414a2e`; main.js `412cb0c80b9f2801` → `2413df314ebe5035`;
view.js `ebbba914917cfd50` → `f50268b5ecc3ae2a`; verify.css `e08608025cc66e8f` →
`694f78dcccec2914`; glow.js new `e7c8b80c17680200`; this file before `35a136fae82b05fa`.
Unchanged: index.html `a7c96503be0e2b96`, SDK session.ts `549b6f1629249aa1`, head_sequence.py
`979f69037d6aaec0`, HARDENING_PLAN.md `2a932fab3aa053df`. `hardening_verify_baseline.py`: 7
mismatched of 100, 0 missing, the same 7 as after the engine part.

Tests: page 244 → 252 (`verify-single-turn.test.mjs` +4: colours equal the engine's own
`flash.displayed()` at 12 offsets (±1 for rounding), malformed/absent schedule = no glow,
reduced motion = one monotone change, one-time line and no-glow state; `verify-preparation.test.mjs`
+3 main.js wiring: neutral until frame 0 however long the wait, then the schedule from frame 0,
later frames do not move the clock, stop clears it; reduced motion; no glow = no `--glow`, no
animation frame; `verify-bridge.test.js` +1: glow read beside the SDK, the SDK gets the same reply,
bad/absent/error = null, no clone = nothing read). One full run had 1 failure that did not repeat
in 3 reruns (the known parallel-run flake). Engine 455 (with `E:\Factech\.venv-pad`; the
`E:\Facetech\.venv` interpreter lacks `onnx` and fails collection of test_aenet_ensemble.py),
tests-hardening 731 pass / 100 skipped, tests-panel 13.
Flag-off byte-for-byte (page equivalent of the Phase 3/4 method): the backed-up flow.js/view.js/
bridge.js vs the current ones, same inputs: 32 000 seeded events (800 runs × 40, half scripted
into capture; 7 089 on the single-turn screen, the rest HEAD_SEQUENCE and every other view),
normal and reduced motion: states, effects, stage text and the rendered fake DOM after every
event byte-identical, 117 793 991 bytes each, sha256 `733489266d76f3c3` both. Bridge: challenge
replies without a glow key (HEAD_SEQUENCE, LOOK_LEFT, 401, 503, with and without clone()):
calls and SDK-visible replies identical (1 931 bytes). main.js flag-off is covered by the
"no glow" wiring test and the unchanged existing wiring tests, not by this comparison.
Not run: a real browser, a phone screen, e2e.

**Open / found:**
- **The /v2 account gateway never issues a glow (or a single turn).** `packages/face-auth`
  `operations.py` builds its own challenge, hard-coded `HEAD_SEQUENCE`, and `inference.py` passes
  the engine only action/params/age. So under the real account flow `apps/verify` always gets
  HEAD_SEQUENCE whatever `CHALLENGE_POLICY` says, and the engine's flash check would have no
  schedule (`inconclusive`). This predates Phase 4 (it applies to Phase 1 too) and is outside
  this item (gateway, not page/SDK). It needs its own approved change before any team round.
- Reduced-motion people see one colour, not the issued sequence, so their correlation will be
  low. With `FLASH_CHECK=on` that could refuse them; the page cannot tell the engine. Decide
  from the log-mode data (or add a reduced-motion hint to the scan) before `on`.
- The glow runs for about 2 s from frame 0 and can overlap the turn (settle 1200-2000 ms), as
  noted in the engine part.

## Liveness Phase 5 — enrolment polish (24 Sep 2026)

Plan: `docs/LIVENESS_UPGRADE_PLAN.md` Phase 5. Backups: `E:\factech-backups\liveness-phase5\`
(main.py, trace.py, HARDENING_PLAN.md, this file, view.js, outcome.js, head_sequence.py as
reference; each as it was before this item). Approval: 3 rows "(Phase 5)" added to
`HARDENING_PLAN.md` BEFORE any protected edit, citing the user's in-session instruction.
`head_sequence.py` NOT edited. NOT rebaselined. Nothing pushed, nothing downloaded. SDK not touched.

Engine (protected):
- NEW `engine/app/enrolment.py`. `photo()`: of the 3 most frontal single-face settle frames
  (`aenet.frontal_pool`), the sharpest (`aenet.crop` 112 px, Laplacian variance), ties to the
  more frontal, then the earlier frame. `bar()`: nose (PAD_GEOMETRY) ok only if `scored`; glow
  (FLASH_CHECK) ok only if `pass` or `inconclusive/too_bright` (poor light). No other
  `inconclusive` is accepted. A part is judged only when its check ran (flag log/on); `refuse`
  = failed parts whose flag is `on`, `would_refuse` = every failed part. `geometry.mode()` folds
  `on` into `log`, so enrolment reads PAD_GEOMETRY itself to see `on`.
- `main.py`: `_enrolment()` runs for `enroll` only, after every existing refusal (PAD, liveness,
  pad_flash). Under CHALLENGE_POLICY single-turn-v1 / single-turn-glow-v1 the template is the
  photo's embedding (via the existing `_embed_selected`); no photo or failed alignment keeps
  today's mean. An enforced failure is LIVENESS_FAIL "liveness check failed: pad_enrol_geometry"
  and/or "pad_enrol_flash". `_log_geometry` now returns its record. Verify and liveness untouched.
  All flags off: neither photo nor bar runs, nothing is written.
- `trace.py`: `enrolment` field `{photo?, bar?}`, present only when set; timing step `photo`.
Page (`apps/verify`, not protected): `view.js` success after enrolment "Your face is saved." →
**Go to workspace** (copy moved into `COPY.SUCCESS`; verify keeps "Verified. / Return to
workspace"); `outcome.js`: any `pad_*` signal (pad_flash, pad_enrol_*) gives the one shared calm
spoof line (plan §6.3). Enrolment already used the same capture as verification; no photo step
existed, none added.

Hashes (sha256, first 16), before → after: main.py `dce0ff9350e6175f` → `0dedd71efd7dfb1d`;
trace.py `588d283d7d3826b0` → `6ee203c06e56d10d`; HARDENING_PLAN.md `2a932fab3aa053df` →
`6e748922a92c70e3`; view.js `f50268b5ecc3ae2a` → `ba2d5a0030c4db65`; outcome.js
`44dde0a944a892ba` → `c9dca594e81ba438`; enrolment.py new `9d32a6edfbc808c8`;
test_enrolment_polish.py new `01863f29d6449e2a`; this file before `bc0b3c2553f9ff66`.
Unchanged: head_sequence.py `979f69037d6aaec0`, challenge.py `36b68eae772a842d`, anti_spoof.py
`733d4fbcc4869062`, liveness.py `cc9f048196f2a036`, geometry.py `c6dcdf85cae73790`, flash.py
`fb54ef8596ab0dc5`, aenet.py `ad1c631e2082e5d4`, SDK session.ts `549b6f1629249aa1`.
`hardening_verify_baseline.py`: 7 mismatched of 100, 0 missing, the same 7 as before.

Tests: engine 455 → 478 (new `test_enrolment_polish.py`, 23: bar absent with all flags off;
nose must be conclusive, only `on` refuses; glow pass / too_bright ok, too_few_frames /
no_glow_issued / fail / error / missing refused under `on`, logged under `log`; each part
enforced by its own flag; photo = sharpest of the 3 most frontal settle frames, turned and
post-settle frames never chosen; no usable frame = no photo; photo only under single-turn
policies; endpoint: all flags off saves today's mean and writes no trace field, single-turn
saves the photo, no photo falls back to the mean, `log` records would_refuse and returns 200,
`on` refuses an unsure nose at enrolment while verify still passes and no user is stored,
glow live/sunny accepted and too_few_frames refused). tests-hardening 731 pass / 100 skipped,
tests-panel 13, page 252 → 254 (`pad_*` → spoof line and pinned success copy; the enrol
navigation check below); the known console f12 flake failed in some full runs, passes otherwise.
ruff check + format clean on changed Python.
Flag-off byte-for-byte (Phase 3/4 driver plus a forced-accept pass): 72 requests (enroll/verify/
liveness × 6 fixture sets × 500/1400 ms × head-sequence and single-turn-v1), real models, through
the engine now and through a copy with the backed-up main.py/trace.py and no enrolment.py, with
PAD_GEOMETRY/FLASH_CHECK unset, `off` and `bogus`. Pass 1 as in Phase 3/4 (PAD refuses the
fixtures). Pass 2 forces PAD and liveness to pass in both engines, so all 24 enrolments reach
the template and its bytes are compared. **All-flags-off half (head-sequence): responses,
trace lines, issue() shapes and saved templates byte-identical in all 6 runs, 749 184 bytes**
(sha256 `68c8ea2f5cb1341e` pass 1, `04acf5726e9e3635` pass 2). Single-turn-v1 half: pass 1
identical; pass 2 differs only on the 12 enrolments, in the `enrolment` trace field and, where
a photo was embedded, the template: the intended change. (One pass-2 run done while the test
suites were running also showed PAD deadline differences; rerun unloaded, it was clean.)

Phase 0 navigation checks, rerun as automated flow checks (the phone retest is still the team's):
Create account → Back → Create account again, and Sign in → Back → both buttons work (existing
tests, pass); NEW: enrol → "Your face is saved" → Go to workspace = one `goWorkspace` (no
sign-in step), a double tap does nothing, Back → the button works again (pass). `main.js`
bfcache reload and `/workspace` untouched. Not run: e2e browser, a real phone.

Deviations / open:
- **The enrolment photo is gated on the single-turn CHALLENGE_POLICY**, not on a new flag: the
  plan gives it none, and all-flags-off must stay today. A one-frame template may match less
  well than today's mean of up to 12 frames; check first-try verify rates in the team round.
- With FLASH_CHECK=on, enrolment on a non-glow challenge (`no_glow_issued`) is refused (strict
  reading of "the inconclusive shortcut isn't allowed"). Together with the Phase 4 page finding
  (the /v2 gateway always issues HEAD_SEQUENCE), **FLASH_CHECK=on would block every account
  enrolment through /v2** until the gateway issues glow challenges. Same for PAD_GEOMETRY=on if
  turns are too small to score. Keep both at `log` until then.
- The success-screen copy changes whatever the flags (the page has no flag, as in Phase 1).

## Liveness Phase 4b — the account gateway issues the policy challenge (24 Sep 2026)

Fixes the gap found in the Phase 4 page item: `packages/face-auth` `operations.py` built its own
HEAD_SEQUENCE challenge and ignored `CHALLENGE_POLICY`, so single turn and glow never reached the
account flow. Backups: `E:\factech-backups\liveness-phase4b\` (operations.py, inference.py,
postgres_fixtures.py (not edited), this file). **No protected file touched** (the v4 manifest
lists no `packages/face-auth` file; its 12 `packages` entries are the SDK), so no approval rows.
Engine, SDK and page NOT edited; `head_sequence.py` NOT edited; NOT rebaselined; nothing pushed.

- `inference.py` `FrozenAnalyzer.challenge_parameters()`: under single-turn-v1 / single-turn-glow-v1
  returns `{action, params[, glow]}` drawn by the engine's own `challenge.issuable_actions()`,
  `_draw_params()` and `_draw_glow()` (→ `flash.draw_schedule()`), the same calls `issue()` makes;
  no number is copied and nothing is put in the engine's in-memory nonce table (the durable store
  is the authority). Any other policy: None. `_decide` copies the stored `glow` into the engine's
  per-call record, so `ChallengeVerdict.glow` and the flash check see the schedule.
- `operations.py`: `PostgresOperations.challenge_parameters()` asks the analyzer (only an analyzer
  that has the method, i.e. FrozenAnalyzer on the engine side where the challenge route runs);
  `parameters = self.challenge_parameters() or {today's HEAD_SEQUENCE dict}`. The parameters,
  glow included, are stored in `challenges.parameters` with the nonce and returned as today
  (`{nonce, **parameters, issued_ms, expires_ms}`), so the page reads `glow` at the top level as
  it does from the engine. The claim still compares action + params exactly
  (`head_parameters_match`); the client's echo never carries or replaces the glow: the engine gets
  the stored copy through `data["challenge"]`.

Hashes (sha256, first 16), before → after: operations.py `d8be5123bf1ae32e` → `126fccc20d21291a`;
inference.py `dae8ecc841e3c7a3` → `3973013d944a1d03`; test_policy_challenge_flow.py new
`94f52f549cccc9cc`; this file before `27b99d278acba137`. Unchanged: challenge.py
`36b68eae772a842d`, flash.py `fb54ef8596ab0dc5`, head_sequence.py `979f69037d6aaec0`,
HARDENING_PLAN.md `6e748922a92c70e3`. `hardening_verify_baseline.py`: 7 mismatched of 100,
0 missing, the same 7.

Unset byte-for-byte (old vs new, as in the earlier phases): the backed-up and the current
operations.py driven through the real `PostgresOperations.execute()` challenge path, each SQL
statement compiled with the PostgreSQL dialect and its parameters recorded, the same seeded
`secrets` and clock on both sides; analyzer absent (test fakes) and the real FrozenAnalyzer;
CHALLENGE_POLICY unset, empty, `head-sequence`, `bogus`, `Single-Turn-V1` (wrong case); enroll/
verify/liveness × 40 seeds; plus the backed-up vs current `_decide` engine record for a stored
head-sequence challenge. 1 205 cases: responses, stored rows (switch-trial `switch_ms`
9000/9200/9400 included), audit rows and engine records byte-identical, 1 404 220 bytes each,
sha256 `9c6a32a0240b7f22` both. Control: the same harness under `single-turn-glow-v1` reports
the difference at once (LOOK_* + glow stored).

Tests: new `tests-hardening/test_policy_challenge_flow.py` (7), end to end through the real
PostgresOperations (issue → stored with the nonce → claim → FrozenAnalyzer/engine → commit), with an
in-memory stand-in for the connection because no PostgreSQL runs here: a glow challenge is
issued with engine-drawn LOOK params and schedule and stored exactly as issued; enrolment and
verification each hand the engine their own stored schedule and FLASH_CHECK=log writes a `flash`
record (mode log, not enforced, outcome pass, schedule = the stored one) to the decision trace;
a tampered client glow is ignored; single-turn-v1 has no glow; unset/`head-sequence`/`bogus`
issue today's HEAD_SEQUENCE; drawing never touches the engine's nonce table. Counts: engine 478,
tests-hardening 731 → 738 pass / 100 skipped (of which the face-auth tests: 672 pass / 100
skipped), tests-panel 13, page 254. ruff check + format clean.
Not run: the Postgres-gated suites (no local PostgreSQL; the SQL itself is unchanged apart from
the stored JSON value), a real browser, the VPS.

Open: `CHALLENGE_POLICY` must be set on the **engine side** of the account stack (where the
challenge route and FrozenAnalyzer run); the gateway side needs nothing. Deployment config
(`docker-compose*.yml`, protected) not edited.
