# AmFatec VPS UX audit: cross-verification

Checked 22 September 2026 against the supplied audit, E:\Factech, the public AmFatec endpoints, and read-only inspection of the running VPS containers and Keycloak realm database. No application code, realm setting, account, biometric data, or deployed service was changed. This report is the only new workspace file.

## Verdict

The report identifies real problems, but it is not an accurate implementation specification as written. The dead navigation state, five-minute access-token expiry, guide failure dead end, camera restart behavior, repeated capture instructions, hidden progress, and incorrect liveness explanations are confirmed. The claimed missing forced reauthentication is false on the current VPS. Several timing statements and proposed fixes also need correction.

These checks establish code paths and deployed configuration. They do not establish how often a person encounters each problem, reproduce a physical phone's back/forward cache behavior, or measure biometric pass rates.

## Deployment and checks

- Public site: https://amfatec.31.97.186.120.sslip.io/
- Eight served files match the local files after CRLF normalization: index.html, main.js, flow.js, view.js, bridge.js, outcome.js, verify.css, workspace.js.
- Running gateway modules sessions.py, oidc.py, policy.py, and panel.py match local normalized SHA-256 hashes. Running engine head_sequence.py, challenge.py, and main.py also match.
- **operations.py differs between local source and the running gateway.** Do not assume full repository/deployment parity. No conclusion here depends on treating that module as identical.
- Gateway image: amfatec:ready-trial-20260921-gateway. Engine image: amfatec:switch-trial-20260922-engine. Image tags alone do not establish source parity; hashes above were read inside running containers.
- GET /auth/options returns registration=true. An unauthenticated GET /auth/session returns the expected 401.
- Both live /auth/login and /auth/register redirects include **max_age=0**. Registration also presents the separate username, password, password confirmation, email, first name and last name fields.
- The running realm has access_token_lifespan=300, sso_idle_timeout=1800, sso_max_lifespan=36000, registration enabled, reset password disabled, and password policy length(12) and notUsername(undefined). No client access-token-lifespan override was found.
- The gateway's configured template expiry is **19 October 2026, 19:06:38 UTC**. It is a fixed preview expiry, not 30 days after each participant enrolls.
- Baseline rerun: `node --test apps/tests/verify-flow.test.js apps/tests/verify-bridge.test.js apps/tests/verify-preparation.test.mjs` — **37 passed, 0 failed**.
- Additional direct state-machine checks reproduce REGISTER → RESUME → REGISTER remaining busy with no second navigation; GUIDE_OFF blocking Start; and landmarks/timing/motion/duplicates all being labelled movement.

## Reported bugs

| Item | Verification | Evidence / correction |
|---|---|---|
| A: dead buttons after Back | Confirmed code defect; physical-device reproduction pending | flow.js sets busy=leaving and handles no RESUME. main.js destroys the guide on pagehide and has no pageshow recovery. The same issue covers result navigation. A normal fresh page load does not preserve this state; the bug depends on restoration/navigation behavior. |
| B: session dies at five minutes | Confirmed deployed configuration and implementation | sessions.py has no refresh path, and principal expiry includes access-token exp. oidc.py rejects an expired signed token before it reaches introspection. The realm lifespan is 300 seconds. The 30-minute idle and eight-hour absolute limits do not extend that token. |
| B: silent SSO loop because max_age is missing | Contradicted by current VPS | oidc.py:138 sets max_age=0, and the public redirect confirms it. A real browser login round trip still needs testing, but the report's stated missing-parameter cause is absent. |
| B: enrollment requires recent login | Confirmed | policy.py:114 sets 300 seconds; both challenge.enroll and template.enroll require it. Token refresh or a longer token alone cannot make enrollment after six minutes succeed without fresh authentication or a deliberate policy change. |
| C1: automatic camera reopen on early movement | Confirmed | main.js watches through the forward cue until left/right; flow.js PREPARATION_RETRY returns to ready/openCamera. Existing preparation tests explicitly assert this behavior. Stale detector output can also trigger it; not every retry proves the user moved. |
| C2: no distinct started instruction | Confirmed | view.js:37–40 uses the same text for hold, starting, and forward. |
| C3: no visible capture progress | Confirmed; timings overstated | main.js skips frame-only rendering. Twelve samples spaced 1.4 seconds span approximately 15.4 seconds from first to last, followed by 0.7 seconds landing plus encoding/network. Settle is 3.2 seconds; switch is 9.0–9.4 seconds. The final turn is approximately 6.0–6.4 seconds of capture, not a fixed eight seconds. First direction is randomized. |
| C4: terse turn instructions and hidden model | Confirmed UX mismatch; pass-rate cause unmeasured | SDK asks for a small slow turn with both eyes visible; visible stage text says Turn left/right now. CSS hides the head. view.js also forces its pose to forward outside the demo, so revealing it alone would not animate capture cues. |
| C5: guide failure blocks Start | Confirmed | GUIDE_WAIT_MS=5000; GUIDE_OFF permanently sets guideOff for that page state, disables the button and refuses PRIMARY/PREPARED. The header comment promising fallback is stale. A five-second deadline creates a risk on slow connections, not a guaranteed failure on every first visit. |
| D: liveness reasons misclassified | Confirmed | outcome.js:54–58 maps every non-PAD LIVENESS_FAIL message to movement. Direct checks confirmed incorrect mapping for landmarks, timing, motion, duplicates. view.js displays Match score—Not checked for verification liveness failures and displays numeric scores. |

The head gate does require two consecutive qualifying samples, but two samples 1.4 seconds apart do **not** prove a 2.8-second hold. The deployed trial also allows one leading opposite sample in the second-turn window before the correct turn; the report's blanket claim of no reversal allowance omits that exception. Later reversals still fail. See engine/app/head_sequence.py:86 onward.

## The 24 additional UX items

These classifications distinguish an observable implementation issue from a proposed design preference.

| Item | Result |
|---|---|
| 1: workspace/internal vocabulary | Text exists; simplifying it is a design choice. |
| 2: Checking sign-in never changes on failure | Confirmed: account-line is updated only on successful load. |
| 3: verification title during enrollment | Confirmed static terminology mismatch. Mode-specific titles are reasonable. |
| 4: preparation chrome before authentication | Confirmed render path: steps, capture-time label and preparation panel are populated even on signin/unsupported/unavailable states. |
| 5: repeated 01 labels | Confirmed intro/demo/ready labels. |
| 6: create-account versus sign-in ordering | Confirmed ordering and new-account body; primary-button choice is a product decision. |
| 7: Continue as yourself | Confirmed copy; replacement is editorial. |
| 8: Already enrolled wording | Confirmed copy. |
| 9: registration friction | Live form fields, 12-character policy and disabled password reset confirmed. Historical five email refusals are documented, not independently re-counted here. SMTP availability and the quality of password hints are unverified. |
| 10: consent and retention copy | Marketing heading confirmed. Use the actual fixed preview expiry above; no promise of a fresh 30 days per account. |
| 11: administrator deletion contact | Confirmed wording. An actual support route/contact is still needed before replacing it. |
| 12: demo phases/durations | Three small movements heading confirmed. Correct durations and randomized turn order before adding them. |
| 13: demo buttons go to same destination | Confirmed. Neither existing choice opens the camera. Keeping two equivalent actions is optional UX work. |
| 14: Start naming mismatch | Confirmed: Get ready & start versus Good. Tap Start. |
| 15: Start disabled until face positioned | **Report needs correction.** Current button waiting argument is guideOff, not framing readiness. With the guide available, Start arms preparation even before good placement; fresh readiness gates capture afterward. Proposed explanation does not describe today's behavior. |
| 16: camera permissions guidance | Generic guidance exists. Device/version-specific iOS instructions were not verified; do not insert an untested fixed Settings path. |
| 17: Stop confirmation | Immediate cancellation confirmed; adding confirmation is a UX decision, and prompt camera release must remain available. |
| 18: landscape oval unchanged | **Incorrect.** verify.css:75 overrides oval height to 46dvh and vertical position to 36% below 480px viewport height. Real-device overlap testing is still worthwhile. |
| 19: crowded failure result | Multiple verdict/reason/score/reference elements confirmed; simplifying presentation is reasonable. |
| 20: expiry copy | Existing copy confirmed. Do not promise Your progress is safe unless the specific persisted state is known; capture progress is not resumed after reauthentication. |
| 21: Retry-After ignored | Confirmed. bridge.js forwards it to the SDK but stores reply as status/body only; decide() discards SDK retryAfterMs. The page needs to retain that information before it can display a cooldown. |
| 22: server wording on success | Confirmed. Saved to your account fits enrollment; verification success should say verification passed, not imply a new template was saved. |
| 23: workspace branding/status/date | Existing status and action already come from templates. Proposed date requires endpoint support; exact setup date is not currently displayed. |
| 24: sign-out only in workspace | Confirmed main page has no sign-out action. |

## Required corrections to the fix plan

1. **Navigation recovery:** a persisted pageshow reload is a reasonable small starting point. A RESUME state event does nothing unless main.js dispatches it. Clearing busy alone does not reconstruct the destroyed head guide or revalidate a session.
2. **Session refresh:** distinguish expiry from revocation/inactive credentials. Do not automatically turn every inactive-token refusal into renewal. Preserve logout, actor suspension, identity binding, absolute/idle lifetime and concurrent-request guarantees. Expired tokens currently fail signature-claim validation before introspection, so refreshing only after introspection reports inactive misses the expiry path. Retain existing max_age=0; distinguish recent-login-required from token expiry in the UI.
3. **Pre-expiry warning:** today's expires_at is token-capped. Refresh support changes that meaning. A one-shot warning could wrongly sign out a renewable session. Check recent-authentication eligibility separately before enrollment, and avoid dropping an expiry event during capture without a later recheck. A global expiry result must also clean up a live waiting camera.
4. **Guide fallback:** merely enabling PRIMARY cannot work. Preparation needs detector samples, and startScan unconditionally starts a watchdog that cancels when baseline is absent or stale. A fallback requires an explicit path that bypasses only unavailable browser guidance, retains every server check, and tests cancellation/cleanup. Extending the timeout alone still leaves permanent-load failures blocked.
5. **Continuous-camera retry:** a cloned stream can preserve a camera source, but the SDK assigns its clone to the shared video element and clears srcObject on stop. The SDK also emits CAMERA_STOPPED, setting cameraLive=false, and main.js calls camera.stop() after completion. Retry must deliberately restore preview attachment and state, retain the owner only while retrying, and stop all tracks on exit. A clone-only change is incomplete.
6. **Stopping the watchdog on forward:** technically feasible without SDK edits, but it changes the trial's intentional browser behavior during initial stillness. Document and test that change; it is not purely a wording fix. Retain server enforcement and zero submission on cancellations before capture.
7. **Timing/progress:** frame-based progress is available without SDK changes. A smooth one-second countdown is not directly emitted by this HEAD_SEQUENCE SDK path; it emits cue changes and frames. Any display interpolation must remain advisory and must never advance the actual cue. Use actual challenge parameters where available and avoid a fixed left-first sequence.
8. **Failure instructions:** use the existing signal parser pattern, with defined precedence for multiple signals and a neutral fallback. Do not copy legacy move-closer wording from shared/messages.js. The proposed instruction to hold the phone rather than rest it on a table is unsupported and can increase shake; preserve steady-phone guidance.
9. **Policy boundary:** changing RECENT_LOGIN_SECONDS changes authentication policy, even though it leaves biometric thresholds unchanged. The report's no-policy-change promise conflicts with that option. A 900-second preview value should not be presented as a necessary UX fix.

## Suggested priority and remaining validation

First address navigation recovery, token renewal/recent-auth handling, and guide failure recovery. Next address camera retry lifecycle, cue clarity, and reason mapping. Apply editorial and registration changes afterward with the corrected behavior above.

Outstanding acceptance checks: real phone Back navigation; a signed-in six-minute expiry/reauthentication journey; enrollment near the five-minute recent-login boundary; an enrollment-to-workspace journey; slow/failed guide loading; real-camera retry and track cleanup; landscape layout; and first-try success measurements under TEAM_TEST_PROTOCOL.md. No new biometric submission or user-account creation was performed for this audit.

Passing the existing 37 tests is reproducible, but those tests also encode some reported undesirable behavior. It does not mean these UX problems are absent or that the proposed fixes have been validated.
