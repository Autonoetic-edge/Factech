# AmFatec face-check page: UX audit and fix plan

22 September 2026. Scope: the participant journey served by `packages/face-auth/.../panel.py`
from `apps/verify` (sign-in, create account, consent, demo, camera, capture, result, workspace),
plus the auth gateway and Keycloak settings that shape it. Source read: `apps/verify/*`,
`apps/shared/*`, `packages/face-auth/src/facetech_auth/{panel,http,sessions,oidc,policy}.py`,
`packages/face-sdk/src/workflow/session.ts`, `engine/app/{head_sequence,liveness,main,challenge}.py`,
`deploy/amfatec/{init.sh,registration.sh}`.

Baseline before any change: `node --test apps/tests/verify-flow.test.js apps/tests/verify-bridge.test.js apps/tests/verify-preparation.test.mjs` = 37 pass.

Nothing here touches the frozen biometric policy, models, thresholds, capture timing or the SDK
source. Every fix is in page code, panel/gateway glue, or Keycloak realm settings, and each one
lists what it must NOT change.

---

## Part 1. The reported bugs, traced to code

### Bug A. "Go to Create account, come back, and the buttons are dead"  (also "sign in opens a page, go back, can't click")

**What happens.** On the sign-in screen, tapping Sign in or Create account puts the flow into
`busy: 'leaving'` and navigates away (`apps/verify/flow.js:322-325`, `apps/verify/main.js:225-226`).
That state is never cleared. When the person presses Back, the browser restores the page from
the back/forward cache (bfcache) with the JavaScript state exactly as it was: `busy` is still
`'leaving'`, so both buttons render `disabled` (`apps/verify/view.js:107`). `main.js` has a
`pagehide` handler that also destroys the 3D guide and stops the camera (`main.js:245-251`),
but no `pageshow` handler, so nothing recovers. Mobile Chrome and Safari use bfcache
aggressively, which is why every phone tester hits this.

The same trap exists on every other screen that leaves the page: "Continue to sign in" after
an expired session (`flow.js:384`) and "Return to workspace" (`flow.js:383`).

**The "pop page" is not a popup.** It is Keycloak's own login/registration page, reached by
full navigation to `/auth/login` or `/auth/register`. That is fine, but the page must survive
coming back from it.

**Fix (page, small).**
1. `main.js`: add `window.addEventListener('pageshow', (e) => { if (e.persisted) location.reload(); })`.
   A restored page reloads, re-runs `load()`, and shows the right screen for the real session
   state (signed in or not). This is the whole fix for the dead buttons and the dead 3D guide.
2. `flow.js`: also accept a `RESUME` event in the `signin` and `result` views that clears
   `busy: 'leaving'`, so a browser that fires `pageshow` without `persisted` (or blocks reload)
   still re-enables the buttons. Unit test: `signin` + `REGISTER` + `RESUME` + `REGISTER` yields
   `goRegister` twice.
3. `workspace.html` uses a plain link, nothing needed there.

**Verify.** Real phone: open `/`, tap Create account, press Back, tap Create account again.
Also: tap Sign in, sign in, press Back twice, confirm the page shows the signed-in state.

---

### Bug B. "Finished enrollment, tapped Return to workspace, and it sent me to Sign in / Create account"

**What happens.** The gateway's authenticated session is capped to the life of the Keycloak
*access token*: `sessions.py:206` returns `min(session.expires_at, touched_at + IDLE, claims["exp"])`,
and every request calls `oidc.py:218-247 active_token()`, which introspects the token and
refuses it once `exp` has passed. The realm sets `accessTokenLifespan: 300`
(`deploy/amfatec/init.sh:59`). There is no refresh-token path anywhere in `sessions.py`.
So **every account is silently signed out 5 minutes after Keycloak issued the token**, no matter
what `IDLE_SECONDS = 1800` and `ABSOLUTE_SECONDS = 28800` say.

`/workspace` then gets 401 from `/auth/session` and shows "Continue as yourself / Sign in or
create account" (`apps/verify/workspace.js:14-15`). The person just enrolled successfully, so
this reads as the app having forgotten them.

**Why it feels like "every new person needs 2 or 3 tries".** Sign-up, consent, the demo,
reading, camera permission and one 17-second capture already use 2-3 minutes; one retry pushes
the person over 5 minutes. Then:

- Any call after 5:00 → 401 → the result screen says "Your session ended" and offers
  "Continue to sign in" (`view.js:459`). Misleading: nothing the person did ended it.
- "Continue to sign in" goes to `/auth/login`. Keycloak still has the SSO session
  (`ssoSessionIdleTimeout: 1800`) so it redirects straight back **without a form**, and the
  gateway builds a new session with `authenticated_at = claims["auth_time"]`
  (`sessions.py:130`), which Keycloak leaves at the *original* login time on silent SSO.
- Enrolment actions require a login within 300 s (`policy.py:97-113`, `RECENT_LOGIN_ACTIONS`
  includes `CHALLENGE_ENROLL` and `ENROLL`). So a first-time person who took more than 5 minutes
  gets `RECENT_LOGIN_REQUIRED` → shown as the same "Your session ended" → sign in → silent
  redirect → try → same error. **This is a loop that only ends when the Keycloak SSO session
  itself expires (30 min idle) or the person signs out from `/workspace`.** The login request
  never sends `max_age` or `prompt=login` (`oidc.py:129-140`).

**Fix (gateway/realm; no biometric change).** Choose 1 or 2 for the token, and do 3 and 4.
1. **Preferred:** use the refresh token. `sessions.authenticate()` already has the whole
   token document in the vault. When introspection says inactive/expired, refresh with the
   stored `refresh_token`, re-seal, update `token_hash`, and continue; only if the refresh
   fails, deny. Keeps the 5-minute access token (good security) and the 30-minute idle /
   8-hour absolute session that the code already promises. Add a test in `tests-hardening`
   with the synthetic Keycloak: clock past 300 s, request succeeds, token hash rotated.
2. **Quick alternative for the preview:** raise `accessTokenLifespan` to 1800 (matching the
   idle timeout) on the running realm with `kcadm update realms/amfatec -s accessTokenLifespan=1800`
   (same mechanism `registration.sh` uses; the JSON import does not update an existing realm).
   One line, reversible, gets testers through today; still do 1 later.
3. **Recent-login for enrolment:** either raise `RECENT_LOGIN_SECONDS` to 900 for the preview,
   or (better) make `/auth/login?reauth=1` send `max_age=0` so Keycloak shows the password form
   and updates `auth_time`; the page uses that URL for the `expired` outcome. Without one of
   these the loop above remains even after the token fix.
4. **Page:** the session document already returns `expires_at`. `main.js` should schedule a
   gentle `SESSION_EXPIRED` dispatch 30 s before it (outside capture/processing, `flow.js:310`
   already handles the event) so the person is told *before* they invest a capture, with copy
   "Please sign in again before starting" rather than "Your session ended" after a good scan.
   Also on `/workspace`, a 401 should say "Your sign-in timed out. Your face was saved." only
   when that is true; simplest honest copy: "Your sign-in has timed out. Sign in again to see
   your workspace." with a **Sign in** button (not "or create account").

**Verify.** Sign in, wait 6 minutes, reload `/`: must stay signed in. Sign up, spend 6 minutes,
enrol: must succeed. Enrol, wait 6 minutes, Return to workspace: workspace loads.

---

### Bug C. "The face part is not smooth; everyone needs several tries"

Five separate causes, all visible in code. In order of impact:

**C1. The silent restart loop.** After Start, `main.js:174-213` watches the face for 250 ms of
movement (`framing.js disturbed()`) from the moment the SDK session starts until the *first
turn cue*, i.e. through the challenge fetch (network) **and** the SDK's own 3.2-second
"hold still" window. Any wobble cancels the SDK session, `camera.stop()`s the stream, drops
back to the `ready` view (the full-screen stage is torn down: `view.js:548`), reopens the
camera, restarts the guide, and auto-starts again (`flow.js:363`, `PREPARATION_RETRY`). On a
phone the camera light goes off and on, the page flashes the "Let's get you ready" copy, the
oval reappears with "Place your face in the oval", then "Getting ready" again. Nothing tells
the person they moved. From their side the app "glitched", and they move to look at it,
which triggers it again.

**C2. Nothing changes on screen when the check actually starts.** The stage line for
`hold` (waiting), `starting` (challenge being fetched) and `forward` (SDK settle phase) is the
same sentence: "Getting ready. Look straight. Stay at this distance." (`view.js:432-435`).
So from "Good. Tap Start." until the first turn (≈ 1.2 s prep + network + 3.2 s settle, often
6 seconds) the person sees no change and does not know stillness is being measured. The
result later says "Hold still at the start", which they believe they did.

**C3. No sense of time during the turns.** "Turn left now. Hold until the next instruction."
then, ~6 seconds later, "Turn right now. Hold until the next instruction." then ~8 seconds
of holding right with nothing happening, then "Checking now". Frame events arrive
(`state.frame`, 12 frames at 1400 ms) but are deliberately not shown (`main.js:61-64`).
People give up the pose early, look at the screen, or turn back to centre, which is exactly
what the head gate rejects (`head_sequence.py:86-99`: each turn must be held for 2 consecutive
sampled frames and never reversed inside its window).

**C4. The instruction contradicts the SDK cue.** The SDK says "Slowly turn your head *a
little*"; the page says "Turn left now." People do a full profile turn; landmarks drop out
(`yaw()` returns None, `head_sequence.py:22-37`), the phase has < 2 usable frames, and the
gate reports `phase_coverage`. The 3D head that would show the size of the turn is hidden on
the stage (`verify.css .visual.stage .guide{display:none}`); the demo that shows it is
skippable and shown once.

**C5. A dead end when the on-page face guide does not load.** The MediaPipe guide is ~10 MB
(`panel.py GUIDE_FILES`). `main.js:44` gives it 5 s after the camera opens. If that passes,
`GUIDE_OFF` puts the page in a state where the line reads "Camera guide unavailable. Close the
camera and reload to try again." and **Start is disabled** (`view.js:596` passes
`state.guideOff` as `waiting`; `flow.js:352,355` refuse PRIMARY/PREPARED when `guideOff`).
The flow.js header comment still says Start should work without the guide; the code no longer
does. On mobile data on a first visit this is a guaranteed "try again later".

**Fixes (page only; SDK, timing, policy untouched).**

C1 → *Restart in place, and say so.*
- `bridge.js handoff()`: hand the SDK `stream.clone()` instead of the stream. The SDK stops
  its clone on cancel; the page keeps its own tracks live, so a retry needs no `getUserMedia`,
  no black frame, no camera-light blink. (SDK unchanged: it just receives a MediaStream.)
- `flow.js`: `PREPARATION_RETRY` stays in `view: 'camera'` with `preparing: true` and a new
  `notice: 'moved'`; effect `restartScan` (not `openCamera`). No stage teardown.
- `view.js`: while `notice === 'moved'` show "You moved. Hold still and we'll start again."
  for at least 1.2 s (the preparation window), then the normal line.
- `main.js`: stop the disturbance watch **once the SDK's first cue arrives** (the SDK's own
  settle window is judged by the server; the page's watch was meant only to cover the gap
  before capture). Keep it until `SDK_CUE forward`, not until the first turn.
- Tests: update `verify-preparation.test.mjs` ("preparation retry reopens camera") to
  "preparation retry stays on the stage and restarts the scan".

C2 → *Three distinct lines for three distinct moments.*
- Waiting for placement: the guide lines (unchanged).
- After Start, before the SDK cue: "Hold still…" with a thin progress ring that fills over
  the 1.2 s preparation.
- SDK `forward` cue: "**Checking has started.** Hold still and look straight." and start a
  visible countdown of the settle (3 s).
  All from SDK events; no page timer decides anything.

C3 → *Show time and progress, from SDK events only.*
- Show the frame counter as a 12-segment ring around the oval (`SDK_FRAME` gives
  `index/total`), and the current instruction with "Keep holding" while frames arrive.
- Change turn lines to "Turn a little to your left and hold it there." / "Now a little to
  your right and hold it there." and, on the last phase, "Almost done. Keep holding."
  Rationale is the gate itself: 2 consecutive frames held (≥ 2.8 s) and no reversal.
- Reduced-motion users get the same information as text ("Frame 7 of 12").

C4 → *Match the SDK's words and show the size of the turn.*
- Put the small 3D head back on the stage (top corner, ~90 px) during capture only, driven by
  the existing `SDK_CUE` pose. It already exists (`head-guide.js`) and already mirrors
  participant-left to screen-left, consistent with the mirrored preview
  (`verify.css #preview{transform:scaleX(-1)}`).
- Make the demo auto-play once for first-time *and* first-verify visits (it is currently once
  per enrolment only) and rename "Skip the demo" to "Skip" — the word "demo" suggests a sales
  demo.

C5 → *Never a dead end.*
- Extend `GUIDE_WAIT_MS` to 15 s and show "Loading the face guide…" with the vendor download
  progress if available; fall back to the fixed oval with a **working** Start after that (as
  the flow.js comment already promises). Restore the PRIMARY path for `guideOff` in flow.js
  and keep a test that the fallback still cannot fake `PREPARED`.
- Prefetch the guide on `LOADED` for all modes (already done) and additionally on the
  sign-in screen so it is cached before sign-up.

**Verify.** Real phone, three people, count restarts and first-try passes before/after; the
plan does not claim a pass-rate improvement until measured. `apps/e2e_verify_browser.mjs`
(fake camera) must still end on the server's NO_FACE refusal.

---

### Bug D. Misleading result messages ("live face not checked", "head movement did not follow the prompt")

**What happens.**
- A liveness refusal shows two score rows: "Live-face check — Not passed" and "Match score
  — Not checked" (`view.js:481,484`). To a participant "Not checked" reads as "the app didn't
  check my face", i.e. a broken app.
- `outcome.js:54-58 whyNotLive()` calls **every** non-PAD liveness failure "movement". The
  engine's message is "liveness check failed: <signals>" where the signal list can be
  `challenge` (head movement), `landmarks` (face hard to track), `motion`/`duplicates`
  (not a live-looking picture), `timing` (slow camera) (`engine/app/main.py:395-398`,
  `liveness.py`). Only `challenge` is about the prompts. So a slow phone or a lost face is
  told "The head movements did not follow the prompts" (`view.js:73`). That is the message
  the testers found insulting and wrong. `apps/shared/messages.js:64-70` already parses these
  signals correctly; the verify page does not reuse it.
- The head-gate reason (`not_frontal_and_still`, `phase_coverage`,
  `action_order_or_rotation`, `opposite_action_in_window`, `timing`; `head_sequence.py`) is
  computed but not carried in the `message`, so the page cannot say *which* part to change.
- "Not saved. Let's try once more." is followed by raw numbers (0.55 threshold etc.) that mean
  nothing to a participant and undercut the calm tone.

**Fix.**
1. `outcome.js`: parse `failed_signals` from the message exactly as `messages.js` does; map
   `challenge → movement`, `landmarks → tracking`, `motion|duplicates → notlive`,
   `timing → slowcamera`, PAD → spoof. Tests for each string.
2. `view.js`: one reason line per case, written as an instruction, never as a verdict on the
   person:
   - movement: "We couldn't follow the head turns. Next time: stay still until the screen
     says the check has started, then turn a little and keep the turn until the next
     instruction."
   - tracking: "We lost your face partway. Keep your whole face inside the oval, including
     during the turns."
   - notlive: "The picture didn't change the way a live face does. Try in even light, with
     the phone in your hand, not on a table."
   - slowcamera: "Your camera was too slow for the check. Close other apps and try again."
   - spoof: keep, but drop "Use your own face" (accusatory); say "The camera image didn't
     look like a live face to the system. Try in even, natural light."
3. Remove "Not checked" rows and the numeric scores from the participant screen; keep the
   request reference. (Reviewers get numbers elsewhere.) If the team wants numbers for testing,
   show them behind "Details" and never in the headline.
4. **Backend ask (M5, not a page change):** include the head-gate `reason` in the
   `LIVENESS_FAIL` message for the `challenge` signal so the page can distinguish "moved at
   the start" from "turned too far / not enough" — the engine already computes it and the v3
   gate even has hint constants (`HOLD_STILL`, `TURN_SOONER`, `TURN_LESS_OR_MORE`).

---

## Part 2. Every other confusing text and flow issue found

Grouped by screen. Line references are `apps/verify/view.js` unless noted.

**Global chrome (index.html)**
1. "WORKSPACE / Your identity", "A personal space for your account security.",
   "Self-service access" — internal vocabulary with no meaning to a participant. Replace with
   the product name and one line: "Face check for your AmFatec account."
2. Sidebar "Your account — Checking sign-in" stays "Checking sign-in" forever when signed out
   or when loading fails (only updated on success, `main.js:109`). Set it in every branch:
   "Not signed in" / "Signed in as <username>" (needs `preferred_username` on
   `/auth/session`; M5 ask) / "Couldn't check".
3. Page title and H1 say "Face verification" even during first-time set-up; the result then
   says "Face saved". Use "Face check" everywhere; "Set up your face check" for enrolment,
   "Confirm it's you" for verification.
4. The "~16 sec capture" clock, "POSITION GUIDE / BEFORE YOU BEGIN", "Camera off", "A little
   space. A clear view.", "Sit comfortably, with light in front of you.", the 3-step Prepare /
   Face check / Result strip, and "Linked to the account you are signed in with" all render on
   the **sign-in**, **unsupported** and **unavailable** screens (`view.js:557-576`). Hide the
   whole visual panel and step strip until there is an account.
5. Three screens carry the eyebrow "01 / …" (intro "01 / A more personal sign-in", demo
   "01 / Demo", ready "01 / Prepare"). Number only the three real stages and give the intro
   and demo no number.

**Sign-in screen**
6. The primary button is "Sign in" and "Create account" is secondary, but the body is a
   numbered guide starting with "Create your account". For a self-registration preview the
   new-person path should be primary: "Create account" first, "I already have an account" as
   the link. Order by the person's situation, not by system importance.
7. "Continue as yourself." is a private-joke heading; use "Sign in to your account".
8. Note "Already enrolled? Sign in to go straight to face verification." — "enrolled" is
   never explained; say "Already set up? Sign in."
9. Keycloak's registration page (outside this repo, realm settings): the incident log shows
   five `email_in_use` refusals from one person (`AMFATEC_INTEGRATION.md:140`). The form asks
   first name, last name, email, username and a 12-character password and rejects duplicate
   emails. Set `registrationEmailAsUsername=true` (one identifier), drop first/last name via
   the realm user-profile config, and show the 12-character rule in the field hint. Also
   `resetPasswordAllowed:false` means a forgotten password is a dead end; if SMTP is not
   available, at least say so on the form. These are realm changes via `kcadm`, documented in
   `TEAM_ACCOUNT_SETUP.md`.

**Intro / consent**
10. "Your face. Your way in." + "A more personal sign-in" is marketing copy on a consent
    screen. Use "Before we start" and state plainly what is stored, for how long
    (`AMFATEC_TEMPLATE_EXPIRES_AT`: templates expire 30 days after deploy — say "for this
    preview, up to 30 days") and how to delete.
11. Privacy dialog: "ask your workspace administrator" — a self-registered tester has no
    administrator. Give the real contact or route.

**Demo**
12. "Three small movements." but the buttons are Forward / Left / Right and the real check is
    hold-then-two-turns. Title it "How the check works", show the three phases with their
    durations (hold ≈ 3 s, left ≈ 6 s, right ≈ 8 s) since those come from the fixed policy.
13. "I'm ready" and "Skip the demo" go to the same screen; keep one button "Continue" and a
    text link "Skip".

**Ready**
14. "Enable camera" then the stage button says "Get ready & start" while the guide line says
    "Good. Tap Start." Use the same word: button "Start", line "Good. Tap Start."
15. The disabled Start has no explanation. Add under it: "Start switches on when your face is
    in position."
16. Camera-denied notice is good; add the OS-specific one-liner for iOS ("Settings > Safari >
    Camera") since that is the device the team uses.

**Stage / capture**
17. The stop "✕" is labelled "Close camera" before capture and "Stop face check" during it;
    fine, but there is no confirmation during capture. A mis-tap throws away 17 s. Ask
    "Stop the check?" only during capture.
18. Landscape phones (`@media(max-height:480px)`) drop the `<br>` and shrink the text but the
    oval formula `min(52dvh,420px,101vw)` still fills the height; test on a real device.

**Result**
19. "03 / Not passed" + big cross + "Not saved." + "Let's try once more." + reason + scores +
    "Decided by the server. Nothing was saved from this attempt." + "Reference …" — seven
    pieces for one outcome. Keep: verdict, one reason, one action, reference.
20. `expired` copy "Your session ended" (see Bug B) → "Your sign-in timed out. Sign in again
    to continue; your progress is safe."
21. `busy` says "Please wait a moment" but ignores `Retry-After` from the 429; show the
    seconds and disable Try again until then (header is already forwarded by `bridge.js`).
22. Success says "Confirmed by the server for your account." — "server" is developer
    language; "Saved to your account."

**Workspace**
23. "Your identity — Your account security, in one place." for a page with one button.
    Rename "Your face check", show status, the date it was set up (M5: expose template
    `created_at`), Run a face check, Sign out.
24. Sign out exists only here. Add Sign out to the main page's account area so a shared
    phone can be handed over without knowing about `/workspace`.

---

## Part 3. Order of work

Each step is independently shippable and has its own test. Do them in this order; 1-3 remove
the failures people are hitting, 4-6 make the capture understandable, 7-8 are copy.

| # | Change | Files | Test | Risk / must not change |
|---|---|---|---|---|
| 1 | bfcache recovery (`pageshow` reload + `RESUME`) | `main.js`, `flow.js` | new flow test; phone Back-button check | none |
| 2 | Session lifetime: refresh-token path (or realm `accessTokenLifespan`), recent-login handling, pre-expiry warning | `sessions.py`, `oidc.py`, `policy.py` or `init.sh`/`kcadm`, `main.js`, `workspace.js` | `tests-hardening` synthetic Keycloak; 6-minute manual test | gateway only; no engine, no policy |
| 3 | Guide load dead end: 15 s, loading state, working fallback Start | `main.js`, `flow.js`, `view.js` | flow tests for `guideOff` | fallback must not fake `PREPARED` |
| 4 | Restart in place with a "You moved" line; stream clone in the bridge; stop the watch at the first SDK cue | `bridge.js`, `flow.js`, `view.js`, `main.js` | `verify-preparation.test.mjs`, `verify-bridge.test.js` (real SDK) | SDK untouched; still zero submissions on early movement |
| 5 | Distinct lines for waiting / started / turning; frame ring and countdown from SDK events | `view.js`, `verify.css`, `main.js` | `verify-flow.test.js` (frame state now renders) | no page timer may advance a cue |
| 6 | Result reasons from the signal list; drop "Not checked" and raw scores; Retry-After | `outcome.js`, `view.js` | outcome tests per message string | none |
| 7 | Copy pass (Part 2 items 1-8, 10-17, 19-22) and hiding the visual panel when signed out | `index.html`, `view.js`, `workspace.html/js` | `verify-flow` snapshot of strings; screenshots desktop + 390×844 | none |
| 8 | Keycloak registration form and realm hints (item 9), Sign out on the main page (24) | `init.sh`, `registration.sh`, `TEAM_ACCOUNT_SETUP.md`, `index.html`, `main.js` | live realm check | realm change during a release window |

Backend asks to file for M5 (not blocking): `preferred_username` and template `created_at`
on `/auth/session` / templates list; head-gate `reason` inside the `LIVENESS_FAIL` message;
`max_age` support on `/auth/login`.

## Part 4. How success is measured

- Zero dead-button reports after Back (step 1).
- A tester who takes 10 minutes from sign-up to first pass reaches `/workspace` signed in (step 2).
- Number of automatic restarts per attempt, logged in the page console for the team round only,
  goes to ≤ 1 (step 4).
- The team round records first-try pass counts before and after steps 4-6, using the existing
  `TEAM_TEST_PROTOCOL.md` counting rules. No pass-rate claim is made before that.
