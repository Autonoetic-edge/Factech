# B2c — a relaxed head-gate policy version: PLAN ONLY

Date: 21 September 2026. Status: **PROPOSAL. Nothing was applied.** No protected
file was edited, no row was added to the plan table, the VPS was not contacted,
no Docker, no Git, no pip or npm download. Baseline check at the time of writing:

    manifest source-sha256.v4.json: 100 protected, 0 mismatched, 0 missing

**Head-gate replay never touches PAD.** Every number below is the head gate
alone, replayed from recorded yaw pairs, timestamps and challenge parameters.
No frame was opened, no model was run, PAD was not re-measured. The PAD column
in the table is the verdict recorded at capture time, copied, nothing more.

## 1. The short answer

| | genuine, replayable (14) | 2 phone attacks accepted | wrong-direction replays accepted (16) |
|---|---|---|---|
| `pad-sequence-v2-slow` (frozen, live) | 6 of 14 | 0 of 2 | 0 of 16 |
| `pad-sequence-v3-guided` (frozen, dev) | 10 of 14 | 0 of 2 | 0 of 16 |
| **candidate `pad-sequence-v4-relaxed`** | **11 of 14** | **0 of 2** | **0 of 16** |

**The 90% target is MISSED.** 11 of 14 is 79%. No setting of the three permitted
knobs reaches 90% on this data: the most any combination reaches is 12 of 14
(86%), and only with a stillness tolerance of 0.60, three times the turn target,
which this plan does not recommend (§4). The two captures nothing can pass
(scan022, amf1) are people who did not perform the challenge. A gate that keeps
the one bit must refuse them. They are page-guidance failures, not gate failures.

This is development data: 14 replayable captures, about 4 people, one lighting
condition, and the candidate's one new rule was chosen while looking at them.
**No rate here may be quoted as a measured FRR**, and 0 of 2 attacks says only
that these two were refused, not that phone attacks are refused.

## 2. The denominator is 14, not 19

19 genuine captures went through a head gate: 6 passed, 13 failed (7
`not_frontal_and_still`, 5 `action_order_or_rotation`, 1
`opposite_action_in_window`). That count is confirmed against disk.

Five of the 19 (scan008–012) are `pad-sequence-v1`: a span of about 8.4 s with a
1400 ms settle. Under the 12 frames / 1400 ms cadence, which is not changeable,
every current policy refuses them with `timing` before looking at the head. They
cannot be measured under v2-slow, v3-guided or the candidate, and
BIOMETRIC_TRACK.md forbids pooling policy generations. They are listed in the
table and excluded from every rate.

Replay self-check: for all 16 v2-slow-generation traces the replayed `validate()`
verdict and reason equal the recorded ones (16 of 16), including the six AmFatec
lines whose timestamps had to be rebuilt (§7).

## 3. Per-scan table

`continuity_min` is the recorded PAD face-continuity minimum, carried through as
a metric only. It plays no part in the head gate.

| id | generation | label | PAD (recorded, NOT re-run) | continuity_min | v2-slow | v3-guided | candidate |
|---|---|---|---|---|---|---|---|
| scan008 | v1 | genuine | live | 0.94185 | timing | timing | timing |
| scan009 | v1 | genuine | insufficient_evidence | 0.35938 | timing | timing | timing |
| scan010 | v1 | genuine | insufficient_evidence | 0.41147 | timing | timing | timing |
| scan011 | v1 | genuine | spoof | 0.61025 | timing | timing | timing |
| scan012 | v1 | genuine | spoof | 0.61050 | timing | timing | timing |
| scan013 | v2-slow | genuine | live | 0.77949 | PASS | PASS | PASS |
| scan014 | v2-slow | genuine | live | 0.80373 | not_frontal_and_still | action_order_or_rotation | **PASS** |
| scan015 | v2-slow | genuine | live | 0.73359 | PASS | PASS | PASS |
| scan016 | v2-slow | genuine | live | 0.75849 | not_frontal_and_still | PASS | PASS |
| scan017 | v2-slow | genuine | live | 0.69272 | PASS | PASS | PASS |
| scan018 | v2-slow | genuine | live | 0.65285 | not_frontal_and_still | PASS | PASS |
| scan019 | v2-slow | genuine | live | 0.73328 | not_frontal_and_still | PASS | PASS |
| scan020 | v2-slow | genuine | live | 0.74531 | opposite_action_in_window | PASS | PASS |
| scan021 | v2-slow | genuine | live | 0.73876 | PASS | PASS | PASS |
| scan022 | v2-slow | genuine | spoof | 0.77581 | action_order_or_rotation | action_order_or_rotation | action_order_or_rotation |
| amf1 12:23 | v2-slow | genuine | live | 0.78804 | action_order_or_rotation | opposite_action_in_window | opposite_action_in_window |
| amf2 12:24 | v2-slow | genuine | live | 0.56181 | not_frontal_and_still | not_frontal_and_still | not_frontal_and_still |
| amf3 12:48 | v2-slow | genuine | live | 0.77335 | PASS | PASS | PASS |
| amf4 12:49 | v2-slow | genuine | live | 0.75984 | PASS | PASS | PASS |
| amf5 12:51 | v2-slow | **phone attack** | spoof | 0.84645 | not_frontal_and_still | action_order_or_rotation | action_order_or_rotation |
| amf6 12:52 | v2-slow | **phone attack** | spoof | 0.85181 | action_order_or_rotation | action_order_or_rotation | action_order_or_rotation |

The three genuine captures the candidate still refuses, in plain words:

- **scan022** — no first turn at all. Window-1 readings 0.04, 0.07, 0.03 against
  a target of 0.20. Only lowering the target would pass it. Not touched.
- **amf1** — turned the first way and never came back: window 2 reads about
  −1.0 on all four frames. That is `opposite_action_in_window`. Not touched.
- **amf2** — was already turning during settle: the three settle frames sit 0.00,
  0.76 and 0.60 from their median and the baseline is −0.48. See §4.

## 4. The three knobs, measured

**1. `FRONTAL_LIMIT` 0.25 → 0.35.** Gain on this data: **0 captures.** Reason to
do it anyway: scan013, a clean pass, has a baseline of 0.245, which is 0.005
from being refused for sitting slightly off-axis. Cost to the one bit: none
measured (0 of 16 wrong-direction replays accepted at 0.25, 0.35 and 0.50).

**2. `STILL_TOLERANCE` 0.15 → 0.20.** Gain on this data: **0 captures.** v3-guided's
"two of three settle frames agree" rule already absorbed every wobble failure
(scan016, 018, 019; settle outliers 0.12–0.18). Honest statement: there is no
evidence here that this knob needs to move. 0.20 is offered because it was asked
for and costs nothing measured; leaving it at 0.15 changes no number in this
document. The only capture it could help is amf2, and that needs 0.60 together
with a frontal limit of 0.50. **Not recommended:** a tolerance three times the
turn target stops meaning "still", and amf2 is a person who moved before the
settle ended, which the working agreement says to fix on the page, not by
loosening the engine. Note the plan lists "the 0.15 stillness limit as v2-slow
applies it" as not changeable. v2-slow's use of it is not touched; this is a
different named policy. The user should accept that reading knowingly.

**3. The hold rule — this one is not free.** Options replayed at frontal 0.35,
still 0.20:

| hold rule | genuine | attacks | what it lets through (synthetic, numbers only) |
|---|---|---|---|
| A. two adjacent frames ≥ 0.20 (today) | 10/14 | 0/2 | nothing below |
| B. any two frames ≥ 0.20, not adjacent | 10/14 | 0/2 | nothing below — **and gains nothing** |
| C. one frame ≥ 0.20 with a neighbour ≥ 0.10 | 10/14 | 0/2 | a 0.30 spike riding on 0.12 drift |
| **D. A, or one frame ≥ 0.50 (proposed)** | **11/14** | **0/2** | one spike ≥ 0.50 per window |
| E. any single frame ≥ 0.20 | 11/14 | 0/2 | **one 0.30 spike per window passes** |

The only capture the hold rule was blocking is scan014: a late switch, three
frames still on the first side, then one frame at 1.39 on the correct side. B and
C do not help it. E helps it but is exactly the risk named in the task: both
phone attacks contain a lone frame over 0.20 in one window (amf5 0.215, amf6
0.273). They are still refused under E only because their *other* window has
none. That is luck, not design.

**The middle option is D: keep "two adjacent at 0.20", and also accept a single
frame only if it is at least 0.50 in both ratios.** Target 0.20 is not lowered;
the single-frame path is stricter than the target, not looser. Its price, in
measured numbers:

- largest both-ratio reading when nobody is turning, 13 genuine settle windows: **0.071**
- largest both-ratio excursion anywhere in the two phone attacks: **0.357**
- smallest peak turn reading in any passing genuine window: **0.57**

0.50 sits between 0.357 and 0.57. That gap was found on 2 attacks and 10 passes,
so it is a development choice, not a validated threshold. What D gives up: an
attacker who can force one ≥ 0.50 both-ratio reading in the right direction in
each window, in the right order, no longer has to sustain it for 2.8 s. A
wrong-direction replay is still refused (0 of 16), the both-ratios rule and
`opposite_action_in_window` are untouched. Gain: one capture of 14.

## 5. What would be built, if approved

Exactly as v3-guided was added. `validate()` and `validate_guided()` are not
edited; the duplication is deliberate because both are frozen.

- `engine/app/head_sequence.py`, appended below the v3 block: constant
  `POLICY_V4_RELAXED = "pad-sequence-v4-relaxed"`, its three numbers
  (`0.35`, `0.20`, `STRONG_SINGLE = 0.50`), and `validate_relaxed()`, a copy of
  `validate_guided()` differing only in those three places. `selected_policy()`
  and `validate_for()` gain one branch each. Any value of `HEAD_GATE_POLICY`
  other than the two opt-in names still means v2-slow.
- `engine/app/liveness.py`: no change expected; it already calls `validate_for()`.
- New, unprotected: `tests-hardening/test_head_gate_v4_replay.py` and the v2-slow
  generation traces from the 21 September pull added to the numbers-only fixture
  by `tools/hardening_head_gate_fixture.py` (yaw, relative times, params,
  verdicts; S-aliases, no frames). It asserts the table in §3, the 0-of-16
  wrong-direction result, the five synthetic rows in §4, and that v2-slow and
  v3-guided verdicts are unchanged for every trace.
- New manifest `source-sha256.v5.json` by `--rebaseline`; evidence file lists
  the one hash that moves (`head_sequence.py`, now `62108a7f…78053`).
- Not touched: `anti_spoof.py`, `challenge.py`, `main.py`, `detect.py`, target
  0.20, the both-ratios rule, `opposite_action_in_window`, 12 frames / 1400 ms,
  the FaceScan wire format, every model and threshold, the VPS.

## 6. Proposed protected-file row (NOT added to the plan — for the user to approve)

> | engine/app/head_sequence.py (third row) | Append a THIRD named, development-only policy, `pad-sequence-v4-relaxed`, below the v3-guided block, selected only by `HEAD_GATE_POLICY`. It is a copy of `validate_guided()` differing in exactly three places: frontal limit 0.35, settle stillness tolerance 0.20, and a turn window is also satisfied by one frame at ≥ 0.50 in both ratios. `selected_policy()` and `validate_for()` gain one branch each. `validate()` and `validate_guided()` are not edited and return identical results for every fixture trace; v2-slow stays the default. No change to target 0.20, the both-ratios rule, `opposite_action_in_window`, timing, cadence or wire format. Not deploy authority. | pending — proposed 21 Sep 2026 |

This row is independent of the pending "second row" (policy name stamp). If both
are approved they should be applied as separate, separately hashed steps.

## 7. Unverified

- **Timestamps of the six AmFatec lines.** The decision line keeps span and
  min/median/max gap, not per-frame times. They were rebuilt as evenly spaced.
  Recorded gaps are 1392–1412 ms, the nearest phase edge is 211 ms away, the
  rebuilt phases equal the recorded `[3, 3, 4]`, and replayed v2-slow verdicts
  equal the recorded ones 6 of 6. Exact times remain unverified.
- **That the 12:51 and 12:52 captures were phone attacks** rests on the user's
  statement; the log carries no label.
- **Which captures were first attempts.** Nothing in the data marks attempt
  order per person, so "first-attempt pass rate" cannot be computed. The figures
  are per capture.
- **Subject count** ("about 4") for the AmFatec lines: subject ids were not read.
- **PAD under the candidate**: not measured and not claimed. scan022 is a
  genuine capture PAD called spoof; that is outside this plan.
- **Live behaviour.** No policy here has judged a live capture. Replay uses the
  recorded five-point yaw pairs; `yaw()` and SCRFD were not re-run.
- **Overfitting.** The 0.50 bar was chosen on the same 16 traces it is scored on.
  It needs captures it has not seen before anything is claimed for it.

## 8. Reproduce

Scripts (numbers only, outside the workspace, not yet tests):
`%TEMP%\claude\e--Facetech\ce126773-66fa-4d90-bd6f-923c44b2cade\scratchpad\head-gate-v4\`

    cd <that folder>
    E:\Facetech\.venv\Scripts\python.exe final.py     # §1 and §3
    E:\Facetech\.venv\Scripts\python.exe sweep.py     # §4
    cd E:\Factech; .venv-pad\Scripts\python.exe tools\hardening_verify_baseline.py

`replay.py` asserts that its knobbed copy at v3 defaults equals the real
`validate_guided()` on every trace, so the candidate column is a true delta from
the frozen v3 code, not from a re-implementation.

**Stopped here. Nothing is applied until the row in §6 is approved.**
