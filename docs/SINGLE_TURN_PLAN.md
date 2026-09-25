# Single random head turn: plan

Date: 23 Sep 2026. Builds on `docs/UX_AUDIT_AND_FIX_PLAN.md` (steps 1–8 done, see
`docs/UX_FIX_LOG.md`; page suites at 45 passing). Nothing here is implemented yet.

Three asks:

1. Replace the two-direction head sequence with **one small turn to a random side**, and make
   the demo, steps and stage guidance describe exactly that.
2. When the screen says "turn left", show a **countdown of a few seconds** for the hold.
3. Say **how far to turn**, and cope with people who turn 90 degrees.

This is an engine policy change first and a page change second. Everything under `engine/`
and `packages/face-sdk/` is frozen by `HARDENING_PLAN.md`, so the engine parts ship behind an
opt-in flag with the protected-file procedure, and the live VPS is untouched until the engine
owners flip the flag in a release window.

**Added 23 Sep 2026:** Part 7 folds in the eight "adopt with a change" items from the test-case
review in `docs/TEST_CASE_COVERAGE.md` (R6, R7, R9, L5, L7, L8, S5, plus the L2/L3/S1–S4
measurement runs), and Part 8 is the evidence protocol those items and Phase 0 share. Part 7
items are written so an implementer can work from this file alone: exact files, functions,
copy, tests and the gate each must pass.

---

## Part 0. What already exists (facts from the code)

| Layer | Fact | Where |
|---|---|---|
| Engine | `LOOK_LEFT` / `LOOK_RIGHT` actions exist with a working verifier, but only `HEAD_SEQUENCE` is ever issued | `engine/app/challenge.py:29-35`, `engine/app/liveness.py:391-432` |
| Engine | Secure endpoints hard-reject any action other than `HEAD_SEQUENCE` ("current head sequence required") | `engine/app/main.py:461-466` |
| Engine | The "capture before issue" replay check runs only for `HEAD_SEQUENCE` | `engine/app/challenge.py:252` |
| Engine | Single-action params drawn per challenge: `settle_ms` random 900–2400, `target` random 0.150–0.250 (floor 0.15 + half of the 0.20 band) | `engine/app/challenge.py:11-21, 84-99` |
| Engine | Single-action scoring: ≥4 usable frames, ≥2 settle frames, ≥2 approach frames; score = min(settle stillness, turn toward target, no turn away). Overall liveness = **min** of all voting signals, pass at ≥0.5 | `engine/app/liveness.py:450-559, 147-172` |
| Engine | Non-sequence timing signal expects 500 ms cadence | `engine/app/liveness.py:74, 327-360` |
| Engine | PAD policy `pad-sequence-v2-slow`: 12 frames, ≥9 usable, ≥2 per third, first usable ≤1, last ≥10, no gap >3800 ms, all frames live by ensemble, pairwise continuity ≥0.55. Cadence "1400 ms intended" is stated in the policy text, not enforced | `engine/models/anti_spoof/POLICY.md`, `engine/app/anti_spoof.py:20-22, 217-223` |
| SDK | A non-sequence branch already exists: 12 frames at 500 ms (5.5 s span), emits `instruction {action, settleMs, target}`, `phase hold→move`, `frame index/total`, and a 10 Hz `guide` tick. It emits **no cue sentences** for non-sequence actions | `packages/face-sdk/src/workflow/session.ts:111-150`, `constants.ts` |
| SDK | The non-sequence `guide.progress` is computed from face **height** (move-closer semantics); it is meaningless for a turn but harmless (`phase` only ever becomes hold/move) | `packages/face-sdk/src/challenge/guide.ts:53-63` |
| Page | `cues.js` maps only the three HEAD_SEQUENCE sentences to poses; `flow.js` has `SDK_CUE` / `SDK_FRAME`; `view.js` stage lines; frame ring; small head on stage | `apps/verify/cues.js`, `flow.js:128-132`, `view.js:29-46` |
| Page | The on-page guide already computes a live yaw from MediaPipe keypoints with the **same formula as the engine head gate** (4 × nose offset / interocular) and calls ≤0.30 "frontal" | `apps/verify/framing.js:36-47` |
| Page | The 3D demo head turns ±0.42 rad (**24°**), roughly three times the turn the engine needs. This is one reason testers over-turn | `apps/verify/head-guide.js:5` |
| Page | The page gives the SDK no `faceProbe`, so the SDK's own FACE_LOST abort never fires; face loss is handled by the page's detector only | `apps/verify/bridge.js`, `main.js` (no `faceProbe`) |

Consequence: the SDK can stay frozen for the first version. The engine needs a small, flagged
change. The page needs new event handling and copy.

---

## Part 1. How far to turn (ask 3)

### 1.1 The engine's units, in degrees

The single-action verifier measures `ln(nose-to-left-eye ÷ nose-to-right-eye)` on the
5-point detector. For a head yawed by θ with nose depth d and half-interocular e
(k = d/e ≈ 1.1 for an adult, range 0.9–1.3), the value is `ln((1+k·tanθ)/(1−k·tanθ))`, so
θ = atan(tanh(value/2) / k).

| Engine value | Meaning in the verifier | Degrees (k = 1.1) | Range (k 0.9–1.3) |
|---|---|---|---|
| 0.05 | settle spread giving score 0.5 (must hold this still before the turn) | 1.3° | 1.1–1.6° |
| 0.10 | settle spread ceiling; also max "away" movement for score 0.5 | 2.6° | 2.2–3.1° |
| 0.15 | lowest issued target | 3.9° | 3.3–4.8° |
| 0.20 | "away" ceiling (score 0) | 5.2° | 4.4–6.3° |
| 0.25 | highest issued target | 6.5° | 5.5–7.9° |
| 0.25–0.35 | target + 0.10: the turn that gives score 0.5 (the pass line) | 6.5–8.9° | 5.5–10.8° |
| 0.35–0.45 | target + 0.20: full score | 8.9–11.4° | 7.6–13.8° |

The two-turn head gate today asks for 0.18 in its own units (4 × nose offset / interocular =
2k·tanθ), which is about **4.7°**, and calls ≤0.30 (**7.8°**) frontal. So today's check also
wants a small turn; the page never said how small.

### 1.2 The number to design for

- **Ask for about 10–15°.** That clears the pass line for every issued target with margin and is
  far from the point where the detector loses the far eye.
- **The tell-tale for users:** "Turn until you could just see your shoulder from the corner of
  your eye. Your ear should not come into view." A 90° turn shows the whole ear and hides one eye.
- **Too far starts at about 25°.** Beyond that the 5-point landmarks get unreliable, PAD crops
  degrade, and identity continuity (pairwise ≥0.55) starts to fail.

### 1.3 What happens at 90° today, and why the message is confusing

1. The far eye leaves the image. The detector either finds no face or returns a bad landmark;
   the engine's yaw becomes `None`, so those frames are unusable.
2. PAD then has fewer than 9 usable frames, or a gap >3800 ms, or continuity <0.55 between the
   frontal and profile embeddings → `insufficient_evidence` → `LOW_QUALITY` → the page says
   "We need a clearer view", which is true but does not say "you turned too far".
3. If some profile frames do get through, MiniFASNet may class them as attack → `LIVENESS_FAIL`
   "PAD spoof", the worst message for an honest user.
4. If the page's own detector loses the face for long enough it stops the capture as camera
   lost, again without naming the cause.

### 1.4 Design response (page)

- **Live turn meter** during the move phase, from `framing.js` yaw (page units, 2k·tanθ):
  baseline = median yaw over the settle window; delta = (yaw − baseline) × direction.

  | Page units (delta) | Approx. degrees | Meter | Line |
  |---|---|---|---|
  | < 0.35 | < 9° | first third | "A little more." |
  | 0.35–0.80 | 9–20° | green band | "Good. Hold it there." |
  | > 0.80 | > 20° | red end | "Too far. Come back a little." |
  | face lost or far eye gone | ≳ 45° | red, flashing oval | "Too far. Turn back until both eyes show." |

  The meter is guidance only. It never cancels the scan and never changes the SDK cue; the
  engine still decides. The page-to-engine unit mapping is calibrated in Phase 0 (below).
- **Demo head shows the real amount.** `head-guide.js` ANGLE ±0.42 rad → ±0.20 rad (≈11°), and
  the stage head follows the same value. A "too far" ghost pose (0.8 rad) is shown once in the
  demo to make the contrast visible.
- **Direction arrow on the oval** on the side the participant should turn toward. The preview
  is mirrored, so participant-left is screen-left; this is re-verified in Phase 0 with a real
  capture, not assumed.
- **Copy** (see Part 4): "one small turn", "keep both eyes on screen", "don't show your ear".
- **Result copy** for the over-turn cases (see Part 4, outcome table): face lost during the turn,
  continuity break, PAD on profile frames → all say "the turn was too big" first.

---

## Part 2. The countdown (ask 2)

### 2.1 Timeline of a single-turn capture (500 ms cadence, SDK non-sequence path)

```
0 s          settle_ms (0.9–2.4 s)                         5.5 s        +0.7 s landing
|-- HOLD STILL, look straight ------|-- TURN LEFT and hold --------------|-- sending --|
frames 1..(2–5)                     frames (3–6)..12
```

The hold-and-turn phase is therefore **3.1–4.6 s**, which is exactly the 3–5 s countdown asked
for. Nothing on the page invents that number: it is `(total − index) × cadence` from the SDK's
own frame events.

### 2.2 Rules (unchanged from the UX plan)

- No page timer advances a cue or a phase. The line changes only on the SDK's `phase` event;
  the count only on `frame` events.
- The countdown is **display only**: `secondsLeft = ceil((total − index) × 0.5)` recomputed on
  every `SDK_FRAME`. Between frame events the ring segment animates (CSS), but the digit only
  changes on an event. If frames stop arriving the digit freezes, which is honest.
- The settle phase gets its own bar: length = `settleMs` from the SDK `instruction` event
  (not the CSS constant used today for the 3.2 s sequence settle).
- The last second shows "Keep holding…" instead of "0", so nobody relaxes before frame 12.

### 2.3 If the engine owners choose 1400 ms cadence instead

Then the move phase is ~13 s and a "3–5 s countdown" is impossible without shrinking the frame
count, which is a PAD policy change. The countdown design is the same code, only slower. This
is the main reason to prefer the 500 ms option in Part 3.

---

## Part 3. Engine change (protected files, opt-in flag, owner sign-off)

### 3.1 Policy decision needed first: capture cadence

| Option | Capture length | Engine/SDK impact | Risk to validate |
|---|---|---|---|
| **A (recommended)** 500 ms × 12 = 5.5 s, the SDK's existing non-sequence path | ~6 s + landing | SDK untouched; PAD policy text says "1400 ms intended" and must be amended for the new policy name | At 500 ms a still head can yield near-duplicate frames (phash duplicates signal, ≤3 identical) and low motion; measure on real captures |
| B keep 1400 ms | ~17 s | SDK change (`intervalMs` for non-sequence) + `SETTLE_MS_MIN` ≥ 1500 so ≥2 settle frames exist | Same UX length as today; countdown becomes 13 s |

Security note for the owners: against a prepared replay video both designs depend on PAD.
The two-turn sequence adds one timing constraint (the switch at 9.0–9.4 s) that a prepared
clip can still satisfy after guessing the first direction; the single turn removes that
constraint and keeps the 50 % direction guess and the stillness/away rules. The real
defence in both is PAD plus the gateway's attempt rate limit. Against a static photo both
designs are equally strong: a flat picture cannot change its nose-to-eye ratio.

### 3.2 Changes (all behind `CHALLENGE_POLICY=single-turn-v1`, default = today)

1. `engine/app/challenge.py`
   - `issuable_actions()` returns `(LOOK_LEFT, LOOK_RIGHT)` under the flag, `(HEAD_SEQUENCE,)`
     otherwise. `secrets.choice` already gives the random side.
   - `_draw_params` for LOOK: narrow `settle_ms` to 1200–2000 (≥3 settle frames at 500 ms,
     predictable hold), keep the target band; both to be confirmed by Phase 0 data.
   - `consume_from_scan`: run the `capture_before_issue` check for every action, not only
     `HEAD_SEQUENCE`.
2. `engine/app/main.py` `_secure_analysis`: accept `verdict.action in SECURE_ACTIONS` where
   `SECURE_ACTIONS` follows the flag. Everything else (PAD, continuity, liveness) unchanged.
3. `engine/app/liveness.py` (single-action verifier)
   - Add a `hint` to the challenge signal detail, mirroring the head gate's v3 codes:
     `hold_still` (settle spread), `turn_more` (toward < target), `wrong_way` (away >
     ceiling), `face_lost` (usable < 4 or approach < 2). Carry it into the LIVENESS_FAIL
     message so the page can say the right thing without exposing scores.
   - Review `YAW_SETTLE_CEILING` 0.10: score 0.5 needs a spread ≤0.05 (≈1.3°), which is
     within landmark noise for a still head on a phone. Expect to raise it after Phase 0.
   - Confirm the timing constants for the 500 ms path (`MIN_FRAME_GAP_MS`, `MAX_FRAME_GAP_MS`,
     `MAX_SCAN_SPAN_MS`) accept a real phone's jitter.
4. `engine/models/anti_spoof/POLICY.md`: new section "single-turn-v1" stating the cadence,
   the action set and the measured numbers from Phase 0. The v2-slow section stays as is.
5. Tests: `engine/tests` gain a synthetic LOOK_LEFT scan (frontal settle then a 10° turn) that
   passes, a 90° over-turn that fails with `face_lost`, a wrong-way turn, a still-only scan,
   and the flag-off path still requiring HEAD_SEQUENCE.
6. Rollback: unset the flag. Old SDK bundles are never in play because the panel serves the
   bundle with the page.

### 3.3 Sign convention (must be measured, not assumed)

The engine's LOOK_LEFT means "the ratio rises", with landmark 0 being the **image-left** eye.
Whether that is the participant's left depends on the camera's mirroring on the phone and on
the JPEG the SDK encodes (unmirrored). Phase 0 records five people turning to their own left
and checks the sign; if it is inverted, the fix is one line in the page's action→pose map, not
in the engine.

---

## Part 4. Page change (`apps/verify`, no SDK change)

### 4.1 Events

| SDK event (already emitted) | New flow event | State |
|---|---|---|
| `instruction {action, settleMs}` | `SDK_INSTRUCTION` | `challenge: {pose: 'left' or 'right', settleMs}` |
| `phase 'hold'` / `'move'` | `SDK_PHASE` | `phase` |
| `frame {index,total}` | `SDK_FRAME` (exists) | `frame` |
| page detector tick (framing.js) | `TURN_SAMPLE {delta}` | `turn: {delta, zone}` (display only) |

`cues.js`: add `poseOfAction(action)`; keep the sentence map so the page still works while the
engine flag is off (the sequence path). `stageText()` chooses: no challenge → 'starting';
phase hold → 'forward'; phase move → the pose, plus countdown and meter line.

### 4.2 Stage (full-screen camera)

- Hold phase: "Checking has started. Hold still and look straight." + settle bar sized from
  `settleMs`.
- Move phase: **"Turn a little to your LEFT and hold."**, a large arrow on the oval's left
  side, the digit **4 · 3 · 2 · 1** from frame events, then "Keep holding…", plus the meter line
  from Part 1.4. Small stage head at the real ±0.20 rad.
- The ring keeps one segment per frame (already built).

### 4.3 Before the check (demo, steps, time note)

- Steps strip: 01 Place your face · 02 Hold still · 03 One small turn · 04 Done.
- Time note: "About 6 seconds" (Option A), read from constants, not typed.
- Demo panel: "You'll be asked for **one** small turn, left or right. The screen tells you
  which when the check starts." Practice buttons: Look straight · A little left · A little right ·
  Too far (ghost pose, shown crossed out). Under the head: "About this much. Keep both eyes
  on the screen; if you can see your ear, it's too far."
- Help dialog: replace the two-turn description with the one-turn one.

### 4.4 Result copy (outcome.js + view.js)

| Engine reason / hint | Line the user sees |
|---|---|
| `hold_still` | "You moved before the turn. Next time keep still until the screen says Turn." |
| `turn_more` | "The turn was too small. Turn until you could just glimpse your shoulder, then hold." |
| `wrong_way` | "You turned the other way. Follow the arrow on the screen." |
| `face_lost`, LOW_QUALITY `face_discontinuity`, PAD failure with profile frames | "The turn was too big and we lost part of your face. A small turn is enough; keep both eyes on the screen." |
| SDK `CHALLENGE_EXPIRED` | unchanged |

The existing per-signal mapping from UX step 6 stays for the other signals.

### 4.5 Tests

- `verify-cues.test.js`: `poseOfAction` for both actions; unknown action → null.
- `verify-flow.test.js`: instruction → hold → move → 12 frames; `stageText` gives the arrow
  side, the digits 4/3/2/1 at the right frame indices, and "Keep holding…" on the last frame;
  a page timer cannot change `phase`.
- `verify-bridge.test.js` (real SDK bundle): a mock challenge `LOOK_RIGHT` flows to 12 frames at
  ≈500 ms and an upload; the settle watch stops at `phase 'move'`.
- `verify-preparation.test.mjs`: meter zones from synthetic yaw samples; the meter never
  dispatches a cancel.
- `tests-panel`: the new SVG arrow and copy pass the inline-code and listed-files checks.

---

## Part 5. Order of work and gates

| Phase | Work | Gate to pass | Owner |
|---|---|---|---|
| 0. Measure (2–3 days) | Dev engine with the flag on a local stack; 10 people × 3 captures each at 500 ms, plus 5 people turning ~90°; log both the page yaw and the engine value per frame (page passes samples in `captureMeta`, dev only). Decide cadence (Part 3.1), sign convention (3.3), settle ceiling, meter zones (1.4). **Run the Part 8 matrix in the same sessions** so the light/blur numbers (7.2, 7.3), impostor and look-alike scores (7.4) and the attack set (7.7) come from one dataset | First-attempt pass ≥90 % on honest captures; 0 % pass on the print, screen-photo and screen-video set used for v2-slow; numbers written into POLICY.md; Part 8 report filed under `docs/hardening/evidence/B4.md` | Engine owner + one frontend |
| 1. Engine behind flag | Part 3.2 + tests, protected-file procedure, lint | Engine suite green with flag on and off; no change in behaviour with the flag off | Engine owner |
| 2. Page adapts to the advertised action | Part 4, **plus 7.1 (two-faces result line), 7.6 (hint + retry wiring)**; ship first, safe because the page still renders HEAD_SEQUENCE exactly as today | Page suites green; 390×844 and desktop screenshots for hold / move / too far / result | Frontend |
| 2b. Page hints gated on Phase 0 numbers | **7.2 (light hint) and 7.3 (blur hint)**, only if Phase 0 shows dim/blurred honest captures failing at the engine | The measured `LUMA_MIN` / `SHARP_MIN` values are in the change log with the attempt counts behind them; S4 (dim, genuine) still passes with the hint on | Frontend |
| 2c. Gateway cool-down | **7.5** in `packages/face-auth` (no engine change, no schema change unless operations rows expire sooner than the window) | Gateway suite green; the three-strike test passes with a driven clock; enrol and liveness operations unaffected | Gateway owner |
| 3. Staging soak | Flag on in staging; 20 more real captures incl. glasses, low light, one child-sized face; **one deliberate three-miss run to see the cool-down screen** | Same targets as Phase 0; no `hold_still` rate above 10 %; cool-down clears on time | Both |
| 4. Release | Flag on in the VPS release window; rollback = flag off (engine) and `VERIFY_COOLDOWN_STRIKES=0` (gateway) | Watch LIVENESS_FAIL reasons and `VERIFY_COOLDOWN` counts for 48 h | Engine owner |

Do not start Phase 1 before Phase 0 has answered the cadence and sign questions; both change
constants that the tests pin. Phases 2, 2b and 2c are independent of each other and of Phase 1;
2b alone waits for Phase 0 numbers.

---

## Part 6. Risks and what is deliberately left out

- **Settle stillness is strict** (≈1.3° for score 0.5). If Phase 0 shows honest users failing
  on `hold_still`, the fix is in the engine constant, not in asking people to be statues.
- **500 ms cadence and PAD** were not validated together. Phase 0 must include the attack set.
- **Blink and move-closer** stay out (see the earlier analysis: no eyelid landmarks; depth
  thresholds uncalibrated).
- **SDK stays frozen in v1.** A later v2 could add yaw to `FaceProbe` so the SDK's own guide
  progress means "turn progress" and it can emit `remainingMs`; not needed for the asks above.
- **No live deployment, no realm changes** from this work; the flag is flipped by the owner.

---

## Part 7. Adopted from the test-case review (`docs/TEST_CASE_COVERAGE.md`)

Ground rules for every item here:

- Same constraints as the rest of this plan: work only in `E:\Factech`; `engine/` and
  `packages/face-sdk/` stay frozen (nothing in Part 7 touches them); no live deployment; no
  realm changes; never print or commit secrets.
- Before each item: baseline the suites it names, back the files up under
  `C:\Users\hp\AppData\Local\Temp\claude\factech-backup\<item>\`, then edit, then re-run.
- After each item: add an entry to `docs/UX_FIX_LOG.md` (same format as steps 1–8) with what
  changed, the test counts before and after, and anything left unverified.
- Copy rules from `docs/UX_AUDIT_AND_FIX_PLAN.md` apply: one idea per line, say what happened
  and what to do next, never a number or a code the user cannot act on.

### 7.1 R6 — a second face gets its own result line (Phase 2, page only)

**Today:** the guide already says "Only one face, please." before Start, but if a second face
appears during capture the engine refuses with `MULTI_FACE` ("exactly one face required
throughout capture", `engine/app/main.py:490-497`) and the page maps that to the generic
`quality` outcome, whose text is "We need a clearer view. Face the light…"
(`apps/verify/outcome.js:89`, `apps/verify/view.js` `OUTCOMES.quality`). The user is told to
fix the light when the problem was the person behind them.

**Change:**

1. `apps/verify/outcome.js`: in `BY_CODE`, `MULTI_FACE: 'multiface'`. Leave `NO_FACE` and
   `LOW_QUALITY` on `quality`.
2. `apps/verify/view.js`:
   - `OUTCOMES.multiface = ['Only one person, please.', 'Someone else was in the frame during the check, so it could not be judged. Make sure nobody is behind you, then try again. Nothing was saved.', 'Try again']`.
   - Add `'multiface'` to `REFUSED` (`view.js:82`) so it renders as "03 / Not passed" like the
     other refusals.
3. `apps/verify/flow.js`: no state change needed; the outcome string flows through
   `RESULT`. Confirm with `grep -n "'nomatch'" apps/verify/*.js` that no other list
   enumerates refusals; if one does, add `multiface` there too.

**Tests:** `apps/tests/verify-outcome.test.js`: a 200 reply with `accepted:false` and
`decision.error:'MULTI_FACE'` → `outcome 'multiface'`; a non-200 with `error:'MULTI_FACE'` →
same. `apps/tests/verify-flow.test.js` (or the view test that renders outcomes): the title
and lead above appear, eyebrow reads "Not passed", the primary is "Try again" and restarts in
place.

**Gate:** page suites green (45 → 47 or more). No engine or gateway change.

### 7.2 R7 — light hint before Start, measured first (Phase 2b, page only)

**Today:** `apps/shared/face-guide.js` measures mean luma of the face crop every tick
(`meanLuma`, `face-detector.js:51`) and would emit `dark` / `bright` cues, but
`LUMA_MIN = LUMA_MAX = null` (`face-guide.js:31`) so they never fire, and even if they did,
`apps/verify/framing.js:12` maps both to the `hold` line. No light check exists on the
server either. A dark capture fails later as `LOW_QUALITY` or `LIVENESS_FAIL`.

**Why a hint and not a block:** the limits are null because nobody has measured them. A
threshold picked by eye will refuse darker skin and evening rooms first. The guide's own
header says it is "a guide, never a judge"; keep it that way.

**Step A, measurement (Phase 0, no code):** the guide already writes `luma` and `sharp` into
the per-attempt safe record (`FACE_SAFE_KEYS`, `face-guide.js:309-312`), and the guided
evaluation page labels every attempt with `daylight / room / dim / backlight`
(`apps/integration-demo/index.html:126`). From the Phase 0 / Part 8 captures, tabulate per
lighting label: attempts, engine outcome, minimum `luma`, `sharpLow`. Decide:

- If dim genuine captures pass at the engine (S4 green), **do not add a light hint at all**;
  close 7.2 with that finding.
- Otherwise set `LUMA_MIN` to the highest luma at which the engine still refused ≥ 50 % of
  genuine dim attempts, and `LUMA_MAX` from the backlight attempts the same way. Write both
  numbers and the attempt counts into the log entry.

**Step B, code (only if Step A says so):**

1. `apps/shared/face-guide.js`: add `lightMode` to `limits` (default `'block'`, which is
   today's behaviour when limits are set). In `guideTick`, when `lightMode === 'hint'`, do
   **not** replace the cue: set `G.light = lightOf(G)` and leave `cue` as it was, so `armed`
   and Start are unaffected. Add `light` to the state created by `createFaceGuide`. Other
   apps keep `LUMA_MIN = null`, so nothing changes for them.
2. `apps/shared/face-detector.js:31`: `createFaceGuide(limits)` where `limits` is a new
   optional field of the `createFaceDetector({...})` options (default `{}`).
3. `apps/verify/main.js:151`: pass `limits: { lumaMin: <measured>, lumaMax: <measured>, lightMode: 'hint' }`
   from a small `LIGHT` constant at the top of the file, with the measurement date in a
   comment.
4. `apps/verify/framing.js`: expose `guide.light` to the stage as a **sub-line**, not a
   line: `framingStep` returns the line as today; add `lightHint(guide)` returning
   `'dark' | 'bright' | null`.
5. `apps/verify/view.js` `stageText`: when the line is `hold` or `good` and `lightHint` is
   set, add `sub`: dark → "Find a little more light. A window or a lamp in front of you helps.";
   bright → "The light is behind you. Turn so it falls on your face.". The `stage-cue`
   element already renders `text.sub` as `<small>` (`view.js:227`).

**Tests:** `apps/tests/face-guide.test.js`: with `lightMode:'hint'` and `luma` below
`lumaMin`, `cue` stays `good`, `armed` still becomes true after `GOOD_HOLD_MS`, and
`light === 'dark'`; with `lightMode` unset the old `dark` cue behaviour is unchanged.
`apps/tests/verify-flow.test.js`: `stageText` adds the sub-line and never changes the main
line or withholds Start.

**Gate:** S4 (genuine, dim) pass rate at the engine is not lower with the hint on than off,
because the hint cannot block. Screenshot at 390×844 with the sub-line visible.

### 7.3 R9 — blur hint, measured first (Phase 2b, page only)

**Today:** motion is handled (`SHAKE_STEP` / `SHAKE_TICKS` → `shaky` / `hold`). Sharpness is
computed as the Laplacian variance of a 64×64 face crop (`laplacianVariance`,
`face-detector.js:51`) but `SHARP_MIN = null` (`face-guide.js:25`), so `steadiness()` never
returns `'blur'` and the user is never told to steady the phone.

**Step A, measurement (Phase 0):** from the same captures as 7.2, for attempts the engine
refused with `LOW_QUALITY` or `NO_FACE`, compare `sharpLow` against accepted attempts on the
**same device model** (the variance scales with camera resolution, so one number for all
phones is wrong). If refused attempts are not separable by `sharpLow`, close 7.3 with that
finding and no code.

**Step B, code (only if separable):** set `sharpMin` through the same `limits` object as
7.2 (per-device is out of scope; use the most conservative value across the supported
devices listed in Part 8, and say so in the log). `steadiness()` already returns `'blur'`
when `sharp < sharpMin`, which yields cue `hold`; add to `stageText` the sub-line
"Hold the phone steady, or rest your elbows on something." when `guide.unsteady === 'blur'`.
This is a hint: cue `hold` already allows `good` to follow once the frame is sharp.

**Tests:** `face-guide.test.js`: `sharp` below `sharpMin` → `unsteady 'blur'`, cue `hold`,
then `good` when sharp again. `verify-flow.test.js`: the sub-line appears for `blur` and not
for plain `hold`.

**Gate:** as 7.2. Rollback for 7.2 and 7.3 is setting the limit back to `null` in
`apps/verify/main.js`.

### 7.4 L5, L7, L2, L3 — impostor, look-alike and robustness runs (Phase 0, evidence only)

No code changes the decision. Two small additions make the runs recordable:

1. `apps/integration-demo/index.html:120`: add an option
   `<option value="sibling">Genuine person — sibling or look-alike</option>` next to
   `different_person`. `apps/integration-demo/evaluation-form.js` must accept it wherever
   the case list is validated (search for `different_person`). Test in
   `apps/tests/evaluation-form.test.js`: the new case round-trips into `captureMeta.case`.
2. The score is already returned on every verify (`decision.score`, `decision.threshold`,
   `packages/face-auth/src/facetech_auth/inference.py:192-203`) and stored in the gateway's
   `operations.result` JSON for 24 h (`OPERATION_TTL`, `operations.py:31`). Add a read-only
   SQL snippet to `docs/hardening/evidence/B4.md` that lists, per subject and case label,
   every verify's `match` and `score`. No new endpoint.

**What to run** is in Part 8. **What not to do:** change `PLACEHOLDER_THRESHOLD` (0.55). The
biometric track requires a held-out ROC before any threshold moves. Report the score
distributions for `self`, `different_person` and `sibling` side by side; if they overlap at
0.55 that is the finding to escalate, not a value to edit.

### 7.5 L8 — cool-down after consecutive no-match (Phase 2c, gateway + page)

**Today:** limits are rate windows only: 10 scans/min and 120 requests/min per principal,
3 concurrent scans, `BUSY` with `Retry-After` (`packages/face-auth/src/facetech_auth/limits.py`).
Nothing counts consecutive `no_match`. A person holding the account password can run about
600 face attempts an hour against a placeholder threshold. The password step already happened,
so "fall back to password" is meaningless here; the honest fallback is to pause and point to
the administrator.

**Design:** three consecutive `no_match` verifies on one subject → the fourth verify is refused
for 15 minutes with `VERIFY_COOLDOWN` (HTTP 429, `Retry-After`). A `match` resets the count.
Enrol and liveness operations are not counted and not blocked. The check runs **before**
admission and before the nonce is spent, so it costs no engine time and does not burn the
challenge.

**Gateway change (`packages/face-auth/src/facetech_auth/`):**

1. `config.py` `Config`: two fields, `verify_cooldown_strikes: int = 3` and
   `verify_cooldown_seconds: int = 900`. `serve.py` reads them from
   `AMFATEC_VERIFY_COOLDOWN_STRIKES` and `AMFATEC_VERIFY_COOLDOWN_SECONDS` with the same
   `os.environ.get` pattern used for the engine key (`serve.py:43-102`); `0` strikes disables
   the feature (rollback switch).
2. `operations.py`, new pure helper `cooldown_remaining(connection, tenant, subject_id, now, strikes, seconds) -> int`:
   select from `s.operations` where `tenant`, `subject_id`, `operation='verify'`,
   `state='completed'`, `created_at > now - seconds`, ordered by `created_at desc`; walk the
   rows and count while `result.match is False`; stop at the first `result.match is True`.
   If the count ≥ `strikes`, return `created_at_of_the_newest + seconds - now`, else `0`. No
   schema change: `operations` rows live 24 h (`OPERATION_TTL`), longer than the window.
3. Call it in `operate()` right after the `USER_NOT_FOUND` check (`operations.py:534`) and
   before the challenge is spent (`:573`), only when `operation == 'verify'` and
   `strikes > 0`: `raise Denied("VERIFY_COOLDOWN", 429, retry_after=remaining)`. Also call
   it where the gateway **issues** a challenge for a verify operation, so the page learns
   before the camera opens (find the issue path with `grep -n "challenges" operations.py`
   and `http.py`; the refusal there uses the same code).
4. Audit: `self.audit(connection, access, outcome="cooldown")` on refusal, same shape as the
   other outcomes at `operations.py:149`.
5. `http.py`: nothing new; `Denied.retry_after` already becomes the `Retry-After` header
   (`http.py:195`).

**Page change (`apps/verify/`):**

1. `outcome.js` `BY_CODE`: `VERIFY_COOLDOWN: 'cooldown'`. `classifyReply` already carries
   `retryAfter` from 429 replies (`outcome.js:24`).
2. `view.js`: `OUTCOMES.cooldown = ['Let’s pause<br><em>for a moment.</em>', 'Three face checks in a row did not match, so checks are paused for a short while to protect your account. If this keeps happening, contact your administrator.', 'Try again']`.
   The retry button already sleeps until `RETRY_READY` when `retryAfter` is set
   (`flow.js:145-146`). **Fix the override at `view.js:160`**: the line that replaces `lead`
   with "The service is busy right now…" must apply only when `state.outcome === 'busy'`,
   otherwise the cool-down text is overwritten.
3. If the challenge request is the one refused (before the camera), the page is in the
   waiting state; route the reply through the same `classifyReply` so it lands on the
   `cooldown` result screen rather than a generic camera notice (check `bridge.js` for where
   the challenge reply is handled).

**Tests:**

- Gateway suite (locate it with `find packages/face-auth -name "test_*.py"`; `contracts.py`
  names it `tests-hardening`): with a driven clock, three `no_match` verifies then a fourth
  → `VERIFY_COOLDOWN`, `Retry-After` ≈ 900; a `match` between them resets; after 900 s the
  verify is admitted; `strikes=0` never refuses; an `enroll` after three misses is admitted;
  the refusal spends no nonce (the same nonce still works after the window).
- `apps/tests/verify-outcome.test.js`: 429 with `error:'VERIFY_COOLDOWN'` and `Retry-After`
  → `outcome 'cooldown'`, `retryAfter` set. View test: the cool-down lead is shown, not the
  busy lead; the button wakes after `RETRY_READY`.

**Gate:** Phase 3 staging: one deliberate three-miss run shows the screen and the button
wakes at the right time. Rollback: `AMFATEC_VERIFY_COOLDOWN_STRIKES=0`.

**Not in scope:** account lockout in Keycloak (the password side already has brute-force
settings in the realm; see `docs/UX_FIX_LOG.md` step 8) and any per-IP rule beyond the
existing source window.

### 7.6 S5 — clear hint plus one-tap retry, not an in-capture repeat (Phase 2, page)

The proposal wants "hint to repeat, not a hard fail". An in-capture repeat is impossible:
the challenge nonce is single-use and expires 30 s after issue (`challenge.py:8, 226-233`),
so a second try is always a new capture. What can be delivered is: the failure names the one
thing to change, and one tap starts again in place.

Both are already in this plan; this item pins the wiring so it is not forgotten:

1. Engine hint codes (`hold_still`, `turn_more`, `wrong_way`, `face_lost`) travel in the
   `LIVENESS_FAIL` message (Part 3.2). `apps/shared/messages.js` `failedSignals` must
   recognise them so `outcome.js` `whyNotLive` (`outcome.js:64`) sets `scores.reason` to the
   hint code; today it only knows the signal names.
2. `view.js` `WHY_NOT_LIVE` gets the four lines in Part 4.4 keyed by hint code; the existing
   signal-keyed lines stay for the flag-off path.
3. The primary on every refusal is "Try again" and calls `restartScan` (UX step 4); the
   stage returns to the framing screen with the camera still open, so the retry is one tap.
   Test: `verify-flow.test.js` already covers restart in place; add the assertion that the
   hint line is the first sentence of the lead.

Until the engine flag exists, the page falls back to the signal-keyed lines, so this ships
with Phase 2 and simply starts showing the finer hints when Phase 1 lands.

### 7.7 S1, S2, S3, S4 — attack set and dim-light run (Phase 0, evidence only)

No code. These are the runs Part 8 specifies, recorded with the guided page's existing
`print`, `screen_photo`, `screen_video` and lighting labels. Reporting rule from
`docs/hardening/BIOMETRIC_TRACK.md:115-116`: for each attack attempt record the PAD
outcome, the challenge/liveness reason and the final outcome separately. An attack stopped
by the head challenge is not a PAD detection, and the single-turn challenge has only two
sides and a random target, so a replay of a matching clip will get through the challenge;
the PAD line is the one that matters for S3.

### 7.8 Decided against (do not implement)

- **R10 cross-account duplicate face**: no 1:N search. Privacy scope, placeholder threshold,
  small evaluation tenant. Recorded in `docs/TEST_CASE_COVERAGE.md`.
- **L6 "No account found, register?"**: account enumeration. Keycloak's generic message
  stays.

---

## Part 8. Evidence protocol (shared by Phase 0 and Part 7)

**Where:** the guided evaluation page (`apps/integration-demo/`) on a local stack with the dev
engine, consent ticked, one tester code per person, `case`, `lighting` and `glasses` set per
attempt. Captures land in the mock gateway's store and export with
`mock-gateway/export_captures.py`; decisions are in the gateway's `operations` table.

**Devices:** at least two phones (one iPhone, one Android) and one laptop webcam. Write the
exact models into the report; the biometric track wants the supported list locked.

**Matrix (minimum):**

| Case label | Who | Attempts | Purpose |
|---|---|---|---|
| `self`, daylight, glasses on and off | 10 people | 3 each per lighting | L1–L3 pass rate, page yaw vs engine value, settle ceiling |
| `self`, room, dim, backlight | same 10 | 2 each per lighting | L2, S4, and the 7.2 / 7.3 luma and sharpness numbers |
| `self`, deliberate ~90° turn | 5 people | 2 each | over-turn copy (Part 1.3) |
| `different_person` | 10 people against 3 other accounts | 30 total | L5 impostor scores |
| `sibling` | every available pair | all | L7 look-alike scores |
| `print` | 2 printed photos × 2 devices | 10 each | S1 |
| `screen_photo` | phone-screen photo, flat and tilted | 10 each | S2 |
| `screen_video` | laptop replay of a genuine clip with the right side | 10 | S3 |
| virtual camera | injected genuine clip | 10 | the injection case POLICY.md separates from S3 |

**Per attempt, record:** device, case, lighting, glasses, PAD outcome and reason, liveness
reason or hint code, challenge action and target, match score and threshold (verify only),
page `luma` and `sharpLow` from the safe record, and the final page outcome.

**Report:** `docs/hardening/evidence/B4.md`, one table per case label, with denominators.
Pass criteria: Phase 0 gate in Part 5 (≥90 % honest first-attempt pass, 0 % attack pass),
plus: S4 dim genuine pass rate within 10 points of daylight; impostor and self score
distributions do not overlap at 0.55 (if they do, escalate; do not edit the threshold).
