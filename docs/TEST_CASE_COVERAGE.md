# Test-case coverage: proposed R/L/S cases vs what Factech has (23 Sep 2026)

Source: three screenshots of a proposed test plan (Registration R1–R12, Login L1–L9,
Liveness S1–S5). Every "have" claim below was checked against the code in E:\Factech
on 23 Sep 2026; file references are given so the claim can be re-checked.

## How the product is actually shaped (this changes several rows)

- **Account registration and login are Keycloak.** The form has first name, last name,
  email, username and a 12-character password; email is the username
  (`registrationEmailAsUsername`, docs/UX_FIX_LOG.md step 8). Password reset is disabled.
- **The face check is a step *after* sign-in**, on the workspace ("Face check for your
  AmFatec account", apps/verify/workspace.html). It is not a password replacement. A user
  who has not enrolled sees the `notenrolled` outcome; enrolling saves a template on the
  signed-in account (engine `/v1/enroll`, main.py:446).
- **Every decision is server-side.** The page guide is a hint, never a judge
  (apps/verify/framing.js header comment). The engine decides PAD, liveness, challenge and
  match; the page only maps error codes to outcomes (apps/verify/outcome.js:84-97).
- **Match threshold is a placeholder** (`PLACEHOLDER_THRESHOLD = 0.55`, main.py:32) and the
  biometric track forbids changing thresholds without a held-out ROC
  (docs/hardening/BIOMETRIC_TRACK.md "Boundaries").
- **Evidence so far is thin**: 17 retained records across three incompatible generations;
  under the current policy enrolment accepted 2/3 and verification 2/6, all daylight with
  glasses; **no impostor and no physical-attack records** (BIOMETRIC_TRACK.md lines 31-40).

Legend: ✅ have it · 🟡 partly / exists but unmeasured · ❌ do not have it
Adopt: **Yes** (as written) · **Yes, changed** (adopt with the change stated) · **No**

## 1. Registration

| # | Scenario | Have | What happens today | Adopt? | Why |
|---|---|---|---|---|---|
| R1 | Valid name, email, good face → registered, success shown | ✅ | Keycloak form creates the account; enrol returns `template_id` and the page shows the enrolled outcome. | Yes | Baseline happy path. Note the known pass-rate problem is the head sequence, not the form (2/3 enrol under current policy). |
| R2 | Email already registered → blocked before camera, "Log in instead?" | ✅ | Keycloak refuses a duplicate email on the registration form, before the workspace or camera exist. Its "Back to Login" link is the "log in instead" affordance. | Yes | Already true. Only the link wording lives in the Keycloak theme, not in this repo. |
| R3 | Invalid or empty email/name → inline error | ✅ | Keycloak inline validation; `user-profile.json` makes email required (UX_FIX_LOG.md step 8). | Yes | Already true. |
| R4 | Camera permission denied → clear message, no crash | ✅ | `CAMERA_NOTICE.denied` with browser/iPhone settings hint and "No check has started" (view.js:51). | Yes | Already true. |
| R5 | No face → "Position your face in the oval", no capture | ✅ | Guide cue `none/notface/hidden` → line "Place your face in the oval."; Start is not offered until `armed`. Server backstop: `NO_FACE` → quality outcome. | Yes | Already true. |
| R6 | Two faces → "Only one person, please", no capture | ✅ | Guide cue `multi` → "Only one face, please." Server backstop: PAD `multiple_faces` → `MULTI_FACE` (main.py:490). | Yes, changed | Page copy exists. Gap: `MULTI_FACE` maps to the generic quality text "We need a clearer view" (outcome.js:89). Give it its own result line: "Only one person can be in the frame." |
| R7 | Too dark or backlit → "Find better light", no capture | ❌ | The guide has `dark`/`bright` cues but the limits are **off** (`LUMA_MIN = LUMA_MAX = null`, face-guide.js:31) and framing maps them to the "hold" line anyway (framing.js:12). The engine has no exposure check; a dark capture fails later as `LOW_QUALITY` or `LIVENESS_FAIL` with "try in even light". | Yes, changed | Adopt as a **hint, not a block**, and only after measuring luma on real phones and skin tones (the limits are null because they were never measured). Turn the limits on, give `dark`/`bright` their own line, keep Start available. A hard block risks refusing darker faces and evening use. |
| R8 | Face too far → "Move closer" | ✅ | Guide cue `far` → "Move closer." (framing.js:11, view.js STAGE_CUE). | Yes | Already true. |
| R9 | Blurry or moving → "Hold still" | 🟡 | Movement: yes (`SHAKE_STEP`/`SHAKE_TICKS` → "shaky"/"hold"). Blur: the sharpness measure runs (Laplacian variance) but `SHARP_MIN = null` (face-guide.js:25), so blur never changes the line. | Yes, changed | Keep the motion hint as is. Enable `SHARP_MIN` only after measuring on the supported phones; same false-refusal caution as R7. |
| R10 | Same person registers with a second email → rejected as duplicate face | ❌ | Templates are stored per account (`store.enroll(user_id, …)`, store.py:13). There is no 1:N comparison across accounts anywhere in the engine or gateway. | **No (for now)** | Three reasons. (1) A cross-account search is new scope with real privacy cost: templates are per-actor, encrypted and purged per actor; a 1:N index breaks that model. (2) The 0.55 threshold is a placeholder with no measured false-match rate, so a real-time dedupe would wrongly block siblings and look-alikes with a confusing "duplicate" refusal. (3) The tenant is a small, admin-provisioned evaluation tenant. If duplicates matter later, do it as an offline admin report, not a registration-time block. Record this as a product decision. |
| R11 | Tab closed midway → nothing saved, clean restart | ✅ | Nothing persists unless the engine returns enrolled; page restore handled (UX step 1 bfcache), `cancelled` outcome, `restartScan`. | Yes | Already true. |
| R12 | Network drops after submit → check existing result, no double entry | ✅ | Gateway requires an idempotency key (`IDEMPOTENCY_KEY_REQUIRED`, http.py:475); page shows `uncertain` ("Check existing result") and `pending` outcomes (view.js OUTCOMES). | Yes | Already true and better than the proposal. Keep as a regression test. |

## 2. Login (read "logged in" as "face check passed"; the password sign-in is Keycloak's)

| # | Scenario | Have | What happens today | Adopt? | Why |
|---|---|---|---|---|---|
| L1 | Registered user, same conditions → passes, welcome shown | ✅ | `/v1/verify` returns `match` at ≥ 0.55; page shows the success outcome. | Yes | Exists. The **measured** pass rate is the problem: 2/6 verifications under the current policy, five of nine attempts lost to the head-sequence gate. That is what docs/SINGLE_TURN_PLAN.md addresses. |
| L2 | Same user, different lighting or room | 🟡 | The code has no lighting dependency, but there is **no evidence**: all nine current-policy records are daylight. | Yes | Essential robustness test. Record PAD, challenge and match outcomes separately per attempt, as BIOMETRIC_TRACK.md line 115 requires. |
| L3 | Glasses on/off, cap, beard change | 🟡 | Untested. Records are glasses-on only. | Yes | Same as L2. Note the account can hold up to 5 templates (`MAX_TEMPLATES_PER_USER`, store.py:4) but the page refuses a second enrolment (`already` outcome), so appearance drift has no self-service remedy yet. |
| L4 | Same user, different device | ✅ | Templates are not device-bound; sign in on the new device, then face check. | Yes | Add the device list to the test sheet: the biometric track wants supported devices locked, and none are listed yet. |
| L5 | Different registered person uses someone else's email → rejected | 🟡 | They first need that account's **password** (Keycloak). If they have it, verify compares against the account's templates and returns `nomatch` ("No match this time", no template added). No impostor records exist. | Yes, changed | This is the zero-effort impostor test the track requires. Run it with a shared test password, and log the similarity score of every impostor attempt; that is the data the threshold decision needs. |
| L6 | Unregistered email → "No account found, register?" | ✅ (differently) | Keycloak shows the generic "Invalid username or password" and a separate Register link when registration is enabled. | **No** | "No account found" confirms which emails are registered (account enumeration). Keycloak's generic message is the correct behaviour; keep it. The registration link on the login page already covers the intent. |
| L7 | Look-alike or sibling → rejected, scores noted | ❌ | No records at all. The threshold is a placeholder. | Yes | Highest-value recognition test. **Do not tune the 0.55 threshold from a handful of these**; collect scores and decide with a held-out ROC (BIOMETRIC_TRACK.md "Boundaries"). Expect some to pass today; that is a finding, not a bug to patch inline. |
| L8 | 3 failed attempts → fallback offered, attempts limited | 🟡 | Limits are rate windows, not strike counts: 10 scans/min and 120 requests/min per principal, 3 concurrent scans, `BUSY` with `Retry-After` (limits.py `Limits`). Nothing counts consecutive no-match results. The "fallback" already exists in the sense that the user is signed in by password before the face check. | Yes, changed | Adopt a **cool-down after N consecutive `no_match`** per account (e.g. 3 → 15 min), with its own outcome text. Reason: an impostor holding the password can currently retry 600 times an hour against a placeholder threshold. Do not add "fall back to password": the password step already happened. What the fallback should be depends on what the face check gates; today it gates only the evaluation workspace, so "contact your administrator" is the honest fallback line. |
| L9 | Service slow or timeout → "busy, try again", no false success | ✅ | Gateway abandons the engine call after 10 s (`http.py`), page shows `retry`/`server`; `BUSY` → busy outcome with Retry-After; `offline` when unreachable. Success only comes from a server verdict. | Yes | Already true. |

## 3. Liveness

| # | Scenario | Have | What happens today | Adopt? | Why |
|---|---|---|---|---|---|
| S1 | Printed photo of a registered user → rejected | 🟡 | Designed to reject twice over (MiniFASNet PAD + the timed head sequence), but POLICY.md calls the PAD "an initial RGB PAD candidate, not validated production protection", and there are **no current-policy physical-attack records**. | Yes | Must-run. Report PAD outcome, challenge outcome and final outcome separately: an attack stopped by the challenge is not a PAD detection (BIOMETRIC_TRACK.md line 116). |
| S2 | Photo on a phone screen → rejected | 🟡 | Same as S1, plus history: one phone-screen photo was **accepted** by the older heuristic build (BIOMETRIC_TRACK.md line 32). | Yes | Priority attack class because of that past acceptance. Include screen tilting, which POLICY.md names as an open weakness. |
| S3 | Video replay on a laptop screen → rejected | 🟡 | Weakest class. POLICY.md line 111: "Head turning alone does not prevent video replay." The challenge only randomises the first side (2 orders) and the switch time (3 values), so a replay of the right order defeats the challenge and only the PAD is left. | Yes | Must-run, and add the separate injection / virtual-camera case that POLICY.md says to test apart from screen replay. If S3 passes through, that is the single most important finding this round. Note: the single-turn plan keeps randomised side and target, so it does not make S3 worse, but it does not fix it either; only PAD does. |
| S4 | Real user in dim light → accepted | 🟡 | Will be attempted (no light gate), untested. Failure text today points to "even light". | Yes | This is the false-reject side of R7 and the reason R7 must stay a hint. Run it before enabling any luma limit. |
| S5 | Very fast or no head movement → clear hint to repeat, not a hard fail | 🟡 | It is a hard fail with a hint: reason `movement` → "We couldn't follow the head turns. Next time: stay still until…" (view.js WHY_NOT_LIVE). Finer hints (`hold_still`, `turn_sooner`, `turn_less_or_more`) exist only in the dev-only v3 gate (head_sequence.py:136-139). | Yes, changed | "Repeat without failing" is not possible: the challenge nonce is single-use and expires in 30 s, so a retry is always a new capture. Adopt "clear hint + one-tap retry" instead, which docs/SINGLE_TURN_PLAN.md delivers through the live turn meter, countdown and hint codes carried into the result. |

## 4. Summary

| Adopt as written (mostly already true) | Adopt with a change | Do not adopt |
|---|---|---|
| R1–R5, R8, R11, R12, L1–L4, L9, S1–S4 | R6 (own copy for two faces), R7 (light hint after measurement), R9 (blur limit after measurement), L5 (needs the password; log impostor scores), L7 (no threshold tuning), L8 (no-match cool-down, no password fallback), S5 (hint + retry, not in-capture repeat) | R10 (cross-account face dedupe, for now), L6 wording (account enumeration) |

## 5. Cases the proposal misses that Factech already needs

- Consent before first enrolment (`consent` outcome), recent-login freshness for enrolment
  (`RECENT_LOGIN_REQUIRED` → `reauth`), session expiring during a check (`expiring`,
  `expired`), gateway admission (`BUSY`). All exist; keep them in the sheet.
- Over-turn (90°) and under-turn, per docs/SINGLE_TURN_PLAN.md Part 1.
- Injection / virtual camera, separate from screen replay (POLICY.md).
- Second enrolment on an account that already has a template (`already` outcome) and
  template refresh after appearance change (not offered today; see L3).
