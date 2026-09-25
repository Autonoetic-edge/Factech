# Face Check v2: easier for people, harder for photos and screens

Version 2 (23 Sep 2026). Replaces version 1 of this file; the backup is in
`C:\Users\hp\AppData\Local\Temp\claude\factech-backup\plan-v2\`.
Builds on:
- `UX_AUDIT_AND_FIX_PLAN.md`: steps 1–8 are done locally (`UX_FIX_LOG.md`). This plan does not redo them.
- `SINGLE_TURN_PLAN.md`: the engine detail, turn size, countdown and measurement protocol. This plan points to its parts instead of copying them.

Rules (unchanged):
- Work only in `E:\Factech`.
- `engine/` and `packages/face-sdk/` follow the protected-file procedure in `HARDENING_PLAN.md`.
- Back up files to `...\factech-backup\<item>\` before editing, and log each item in `UX_FIX_LOG.md`.
- Every new behaviour is behind a flag that is off by default. Pushing to the VPS changes nothing until you set a flag.
- Open-source models only.

---

## 1. The idea in one paragraph

Today a person spends about 23 seconds on the check:
- about 6 s getting ready;
- about 17 s of capture: 12 frames, 1.4 s apart, with two full turns and long holds.

Every one of those seconds is a chance to fail, which is why new people need 2–3 tries. Security today mostly comes from asking the person to do more. The plan turns that around: **the person does less, and the computer checks more.**
- The person's part shrinks to "hold still, turn a little once, done", about 6 s.
- The new security checks all run on frames we already capture, so they cost the person nothing:
  - a second anti-spoof model;
  - a 3D check on the nose;
  - a soft colour glow during the hold the person is already doing.

So a harder check does not mean a harder task.

## 2. Targets (both must hold; neither is traded for the other)

| Measure | Today | Target | How measured |
|---|---|---|---|
| Honest first-try pass | not measured (reports say 1 in 3) | ≥ 90 % | 10+ new people, 3 phones, 2 light levels (§8) |
| Time from Start to result | about 23 s | ≤ 9 s | page console timestamps, team round only |
| Printed photo passes | not measured | 0 of 20 | attack kit (§8) |
| Photo shown on a phone or screen passes | not measured | 0 of 20 | attack kit |
| Replayed video of a turn passes | not measured | as low as possible; reported honestly | attack kit |
| Honest people wrongly rejected, added by each new check | — | ≤ 2 points per check | shadow-mode logs (§4) |

If a security check pushes honest first-try passes below 90 %, lower its threshold or leave it in log-only mode. Do not accept the lower pass rate.
Present the result as "resists printed photos and screens"; never call it "spoof-proof".

## 3. What the person experiences (the target flow)

The same capture screen is used for both enrolment and verification. Only the result screen and the quality bar differ (§6.4).

```
Open ──► Camera on ──► Get placed (live hints) ──► auto-start ──► HOLD ~1.5 s ──► TURN once ──► Done
         ~1 s          until OK, 1 hint at a time   ring fills    soft colour     arrow + meter   ~1 s
                                                     0.8 s        glow (A4)       ~3 s
```

Words on screen: at most one line at a time, each an instruction.

| Moment | Line | Visual |
|---|---|---|
| Placing | "Move closer" / "Move back" / "Find more light" / "Face the camera" / "Only you in the frame" | oval grey → green |
| Placed | "Good. Hold still." | ring fills 0.8 s, then starts itself |
| Hold | "Hold still" | ring segments fill per frame; soft glow (A4) |
| Turn | "Turn a little to your LEFT" (always the word, not only the arrow) | arrow, turn meter, small 3D head |
| Turn too far | "A little less" | meter shows overshoot |
| Checking | "Checking…" | spinner under 1 s |
| Pass | "You're verified" / "Your face is saved" | tick, one button |
| Fail | one cause-specific line (§6.3) | "Try again" |

Words removed: step numbers, "demo", "session", "liveness", "PAD", scores, "not checked", model names.

## 4. How every new check is introduced: shadow first

Each security check goes through three modes, controlled by its flag:
1. **`off`**: the code is not run.
2. **`log`** (shadow): it runs and writes its score to the trace, but never rejects anyone. It runs for at least one team test round.
3. **`on`**: it rejects. Its threshold is picked from the shadow logs so that it rejects every photo or screen in the kit and adds at most 2 points of honest rejections.

Result: no check ever reaches a real person untested, and each rollback is a single flag.

---

## 5. The phases, in order

The UX work comes first because it is where the pain is.
The security work is placed so that the new, shorter flow never ships with weaker protection than today. The nose check starts logging in the same release as the single turn.

### Phase 0 — Ship what's done, and get a baseline (you, no code)
1. Push UX steps 1–8 to the VPS.
2. Retest your exact complaints on a real phone:
   - Create account → Back → Create account again;
   - Sign in → Back → both buttons still work;
   - enrol → the workspace link opens the workspace, signed in.
3. Build the **attack kit** once and keep it (§8).
4. Run 5 new people plus the kit on today's flow. Record first-try passes, time and trace IDs in `docs/PAD_RESULTS.md` under "Baseline".
   Without this baseline, no later improvement can be shown.

### Phase 1 — One short turn, clearer screen (about 1 session; engine + page)
Flag: `CHALLENGE_POLICY=single-turn-v1` (default: today's `HEAD_SEQUENCE`).
**The page does not have its own flag.** It switches its screen based on the action the engine sends: `HEAD_SEQUENCE` gives today's screen, `LOOK_LEFT`/`LOOK_RIGHT` gives the new one. So the page and the engine can never disagree.

Engine (details: `SINGLE_TURN_PLAN.md` Part 3):
- `challenge.py`:
  - `issuable_actions()` follows the flag;
  - the capture-before-issue binding (about line 252) applies to every action, not only `HEAD_SEQUENCE`;
  - LOOK `settle_ms` narrowed to 1200–2000.
- **Cadence 500 ms × 12 frames (Option A)**. This is the main time saving, from 17 s to 5.5 s. It needs a new PAD policy name (`pad-single-v1`) with its gap limits amended.
  Risk: near-duplicate frames at 500 ms, because the phash duplicates signal allows at most 3 identical frames. Check this in the Phase 1 test round.
- `main.py _secure_analysis` (about lines 461–466) accepts the flag's actions.
- Tests: flag off gives today's behaviour, byte for byte; flag on issues LEFT/RIGHT, verifies them and binds them.
- Security note: today's sequence has 2 possible orders, and the single turn has 2 possible directions. The randomness is the same, so this is not a downgrade. The random settle time and target stay.

Page (`apps/verify`; SDK untouched, because it already emits `instruction`, `phase`, `frame` and `guide`):
- `cues.js`: `poseOfAction()`; the non-sequence path handles `SDK_INSTRUCTION` and `SDK_PHASE`.
- **Live pre-checks before auto-start** (`framing.js`, which already has the face box and yaw). Show one hint at a time, most important first:
  1. one face;
  2. distance (face-box width);
  3. frontal (|yaw| ≤ 0.30);
  4. light: mean brightness of the face box, plus backlight when the face is much darker than the background;
  5. blur: variance of the Laplacian on the face box.
  These remove the most common causes of failure before the check even starts.
- **Auto-start**: all pre-checks OK and still for 800 ms → the ring fills → start. Keep a Start button only when the face guide failed to load, as the fallback from step 3.
- **Turn meter + "a little less"** (`SINGLE_TURN_PLAN` 1.4). It is display only and never decides anything.
- `head-guide.js:5` ANGLE ±0.42 → ±0.20 rad, so the 3D head shows a *small* turn.
- Countdown: only the digits on the turn phase, from `SDK_FRAME` (`SINGLE_TURN_PLAN` Part 2). Never a page timer.
- Accessibility:
  - `aria-live` carries every line;
  - the line always names the direction in words;
  - `prefers-reduced-motion` turns the ring and meter into text such as "Frame 7 of 12".
- Tests: the 47 page tests plus the new flow/meter/pre-check tests; the engine tests.

**Ship together:** Phase 2's nose check in `log` mode (below), so the shorter flow already collects 3D data.
**Gate to go on:** turn the flag on in a team round. Honest first-try pass rate and time must beat the baseline, and printed and on-screen photos must pass no more often than at baseline.
Also measure the left/right sign on real phones (`SINGLE_TURN_PLAN` 3.3). If it comes out inverted, change the one line in the action→pose map.

### Phase 2 — Nose 3D check (about ½ session; engine only)
Flag: `PAD_GEOMETRY=off|log|on`. It ships as `log` together with Phase 1.
- New `engine/app/geometry.py`, a pure function over the SCRFD 5-point keypoints the engine already has:
  - fit a homography from the 4 non-nose points of the settle frames to each turned frame;
  - project the nose;
  - residual = |projected − real| / inter-ocular distance.
  A flat print or screen keeps the residual near 0 even when tilted or bent; a real face gives a residual that grows with the turn.
- Score = the 90th percentile of the residual over turned frames. If there are too few turned frames, the result is `inconclusive`, never a failure.
- `on` mode rejects below `GEOM_MIN`, set from the shadow data (starting guess 0.04), with reason code `pad_flat_surface`.
  Wire it into `main.py` after the challenge check and before matching, and add it to `trace.py`.
- Tests: synthetic planar keypoints are rejected; synthetic 3D-rotated keypoints are accepted; too few frames gives inconclusive.
- User cost: zero. It reuses the turn the person already makes.
- Limit: it does not stop a replayed video of a real turn. That is Phase 4's job.

### Phase 3 — AENet next to MiniFASNet (about 1 session; engine only)
Flag: `PAD_ENSEMBLE=minifas|minifas+aenet:log|minifas+aenet`. Default `minifas`.
- Weights: the CelebA-Spoof AENet. Its licence is **non-commercial research only**, so it is for the demo only. Record that in `models/anti_spoof/POLICY.md`. Do not ship it in a product.
- `engine/tools/export_aenet.py` converts it to ONNX offline (PyTorch is not needed at runtime). Put the sha256 in the manifest; the existing loader already checks it.
- `AENetPAD` in `anti_spoof.py`:
  - 224×224 input, with upstream's exact crop and normalisation;
  - runs on 3–5 of the sharpest, most frontal *settle* frames only, because frontal frames are what it was trained on;
  - latency budget < 250 ms on the VPS CPU; measure it.
- Combination rule when `on`: reject if **either** model's median is below its own threshold. Always log both scores.
- Decide the default from the data: if the pair adds more than 2 points of honest rejections without catching more attacks from the kit, keep MiniFASNet alone.
- User cost: zero, plus under 0.25 s of waiting.

### Phase 4 — Soft colour glow against screen replays (1–2 sessions; engine + SDK + page)
Flag: `CHALLENGE_POLICY=single-turn-glow-v1`.
Placement is what makes this a UX feature, not a cost:
- The glow **is** the hold-phase visual. It replaces the settle fill bar, so it adds no time.
- To the person it reads as "scanning".
- It is also a signal that the check has started, which people currently miss (audit Bug C2).

How it works:
- **Engine**:
  - issues 3–4 colours from a soft palette (blue, green, magenta, amber; no saturated red) with timings, bound to the challenge;
  - new `engine/app/flash.py`: on the cheek and forehead areas, measures the colour shift per frame against the pre-glow baseline, then correlates it with the issued sequence;
  - passes if correlation ≥ `FLASH_MIN` (starting guess 0.5) and the lag is ≤ 1 frame;
  - uses the same off/log/on modes as the other checks.
- **Page**:
  - the area around the oval fades between the colours: 400 ms fades, at most 2 changes per second (WCAG 2.3.1), about 2 s in total;
  - one line before the first use only: "The screen will glow softly while we check".
- **SDK** (protected procedure): each frame needs the colour shown at its capture time. This is the only SDK change in the whole plan.
- **Fair fallbacks**:
  - bright sun or an unmeasurable reflection → `inconclusive`, never a failure on its own; the turn, nose and model checks still apply;
  - `prefers-reduced-motion` → one slow single-colour change instead of the sequence.
- Tests: synthetic frames with and without the reflected colour, and with too much ambient light.
- Limit: a live deepfake fed through a virtual camera is out of scope for this demo. Say so if asked.

### Phase 5 — Enrolment polish (about ½ session; page + small engine)
- Enrolment uses the same capture as verification.
- The enrolment photo is the sharpest, most frontal settle frame, chosen by the engine. There is no separate photo step.
- Enrolment gets a **stricter quality bar** than verification:
  - a fake face let in at enrolment unlocks the account for good, whereas a failed verification only costs a retry;
  - so enrolment requires the nose check to be conclusive and the glow to be conclusive or the light to be poor. It does not accept the "inconclusive" shortcut that verification allows.
- Success screen: "Your face is saved" → **Go to workspace** (already signed in after step 2).
- This phase comes last because it touches the flow that just had the navigation bugs fixed. Rerun the Phase 0 navigation checks after it.

---

## 6. Rules that apply in every phase

### 6.1 One source of truth
- The engine decides everything; the page only shows state.
- No page timer ever moves the check forward. Lines change on SDK events only.

### 6.2 Flags, in one table
| Flag | Values | Default | Phase |
|---|---|---|---|
| `CHALLENGE_POLICY` | `head-sequence`, `single-turn-v1`, `single-turn-glow-v1` | `head-sequence` | 1, 4 |
| `PAD_GEOMETRY` | `off`, `log`, `on` | `off` | 2 |
| `PAD_ENSEMBLE` | `minifas`, `minifas+aenet:log`, `minifas+aenet` | `minifas` | 3 |
| `FLASH_CHECK` | `off`, `log`, `on` | `off` | 4 |
Rollback: unset the flag and restart the engine.

### 6.3 Failure messages: helpful for honest people, useless to attackers
Where the cause is something the person can fix, say exactly what to change.
Where the cause is a security check, give **one shared, calm line** that does not reveal which check tripped. The exact reason stays in the trace for the team.

| Cause (reason code) | Line shown |
|---|---|
| moved during the hold | "You moved before the turn. Hold still until the arrow appears." |
| turned too late or too little | "Turn a little sooner, and a little further." |
| turned too far / face lost | "That turn was too big. A small turn is enough; keep both eyes on the screen." |
| blur | "Hold the phone steady." |
| dark or backlit | "Find more light on your face." |
| slow camera | "Your camera was too slow. Close other apps and try again." |
| any spoof check (`pad_*`, `pad_flat_surface`, flash, AENet) | "We couldn't confirm a live face. Try again in even light, holding the phone yourself." |
| no match (verification) | "This face doesn't match the account." |

After 3 failures in a row, show "Let's take a break. Try again in a minute." (the existing Retry-After).
The engine limit stays as it is, so repeated attempts at a spoof are slowed down.

### 6.4 Enrolment vs verification
- Same screen, same words.
- Different quality bar (Phase 5) and a different result screen.

### 6.5 Never on the person's screen
Scores, model names, "PAD", "liveness", "not checked", request internals. The request reference stays, in small print.

---

## 7. Budget and order at a glance

| Phase | Work | Size | Who |
|---|---|---|---|
| 0 | Push steps 1–8; phone retest; attack kit; baseline | — | you |
| 1 + 2 (log) | Single turn, 500 ms, pre-checks, auto-start, meter; nose check logging | about 1½ sessions | Claude |
| test | Team round with flag on; pick `GEOM_MIN` | — | you |
| 2 (on) + 3 | Nose check rejecting; AENet in log mode | about 1 session | Claude |
| test | Kit + team round; decide the ensemble | — | you |
| 4 | Glow | 1–2 sessions | Claude |
| 5 | Enrolment polish | about ½ session | Claude |

## 8. Measurement (the same kit every time)
- **Attack kit:**
  - 20 printed photos: matte, glossy, one bent;
  - 20 photos on a phone or laptop screen: two brightness levels;
  - 20 replayed videos of real left and right turns;
  - all against 3 enrolled team accounts.
- **Honest set:** at least 10 new people, 3 phones (one low-end Android), indoor light and a window or backlight; 3 tries each.
- Record per try: trace ID, first-try yes/no, time to result, reason code, and each check's score (from the shadow logs).
- Write it in `docs/PAD_RESULTS.md`, one table per phase, next to the baseline.

## 9. Risks
- 500 ms cadence plus PAD was never tested together. The Phase 1 gate catches this, and the fallback is Option B (1400 ms; longer, but no worse than today).
- Left/right sign on phones: measured in Phase 1, and a one-line fix if inverted.
- The light and blur pre-checks could block people in poor light forever. Cap them: after 10 s of the same hint, allow the start anyway.
- AENet licence: demo only.
- Glow and photosensitivity: soft fades only, ≤ 2 changes per second, a reduced-motion variant, and no red.
