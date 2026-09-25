# B1b - PAD continuity margin across all 17 records

Date: 21 September 2026. Metadata only. No frames were opened, no code was
changed, no protected file was edited, `engine/app/anti_spoof.py` was not
touched. B4 and Phase 2 were not started.

Scope: this is a measurement addendum to B1, not a new milestone. B1 looked
only at the nine `pad-sequence-v2-slow` head-gate traces. This measures the PAD
continuity signal across every record in the read-only snapshot at
`E:\facetech-vps-data\2026-09-20`, including the five `pad-sequence-v1`
records B1 never scored.

## 1. Correction carried forward from the item 7 answer

The reviewer is right and the earlier wording was wrong on two counts.

- "0.94 or better for every earlier frame" is false. Scan 9 runs
  `1.000 0.940 0.958 0.957 0.951 0.641 0.681 0.606 0.423 0.371 0.359 0.532`.
  The 0.94-plus band covers frames 0-4 only.
- "collapsed on the last four frames only" understates it. The decline starts
  at frame 5 and continues; it crosses 0.55 only at frame 8. It is a step at
  frame 5 followed by a steady decay, not a late collapse.

The blur/occlusion hedge is softened accordingly, and section 4 gives the
measurement that replaces it: across all 14 records with PAD data, the per-scan
continuity minimum tracks the per-scan head-pose excursion at r = -0.970. A blur
or occlusion event would be a one-frame cliff. This is a smooth decay that
tracks rotation.

Item 7 was answered in prose only and was never written into `B3.md` - that
file has no continuity text in its section 7 and no mention of 0.55 as a
continuity value, so nothing in `B3.md` needed editing and nothing in it was
edited. This document is the record of the corrected statement.

## 2. What the rule is, verified against the file

`engine/app/anti_spoof.py:217`:

    if any(f.get("continuity_min", 1) < 0.55 for f in result["frames"]):
        return finish("insufficient_evidence", "face_discontinuity")

Verified independently of the reviewer's report:

- **No policy branch.** The line is inside the single PAD scoring function.
  `pad-sequence-v1`, `pad-sequence-v2-slow` and `pad-sequence-v3-guided` all
  run it. It is live today and the B2 candidate did not touch it.
- **Hard-coded literal.** `0.55` at `anti_spoof.py:217` is a literal, not a
  reference. `PLACEHOLDER_THRESHOLD = 0.55` at `main.py:32` is a separate
  literal. `grep -rn "0\.55" engine/app/*.py` returns exactly those two lines.
  Two unlinked copies of one magic number, one used as the identity-match
  threshold and one as a continuity threshold, with no comment linking them.
- **It fires early.** Order inside the function is: continuity (line 217), then
  the all-frames-live check (`spoof` / `non_live_frame`), then temporal
  coverage, then the frame-gap check. A capture that fails continuity is
  rejected as `LOW_QUALITY` before any liveness verdict is formed, which is why
  scans 9 and 10 read `LOW_QUALITY` and not `LIVENESS_FAIL`.
- **`continuity_min` is a prefix minimum, not a neighbour distance.** Per
  frame it is `min(dot(vector, other) for other in vectors)` over every
  previously accepted frame, so the value at the last frame is the similarity
  to the *most dissimilar earlier pose* in the capture. Over 12 frames this is
  the all-pairs minimum. The two extreme poses of the challenge - the two ends
  of the turn the engine itself demanded - are what set the margin.

## 3. Item 1 - continuity minimum per scan, all 17 records

`n` is frames carrying a `continuity_min`. Margin is minimum minus 0.55.

| Scan | Label | Endpoint | PAD policy | Engine | Outcome | n | Min | At frame | Margin |
|---|---|---|---|---|---|---|---|---|---|
| 005_T01 | bona_fide | enroll | (none) | 0.1.0 | 200 | 0 | - | - | - |
| 006_T01 | bona_fide | verify | (none) | 0.1.0 | 200 | 0 | - | - | - |
| 007_T02 | screen_phone | verify | (none) | 0.1.0 | 200 | 0 | - | - | - |
| 008_P01 | bona_fide | enroll | pad-sequence-v1 | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.94185 | 4 | +0.3918 |
| 009_P01 | bona_fide | enroll | pad-sequence-v1 | 0.2.0 | **422 LOW_QUALITY** | 12 | **0.35938** | 10 | **-0.1906** |
| 010_P01 | bona_fide | enroll | pad-sequence-v1 | 0.2.0 | **422 LOW_QUALITY** | 12 | **0.41147** | 9 | **-0.1385** |
| 011_P01 | bona_fide | enroll | pad-sequence-v1 | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.61025 | 11 | +0.0602 |
| 012_P01 | bona_fide | enroll | pad-sequence-v1 | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.61050 | 10 | +0.0605 |
| 013_T072C65047 | bona_fide | enroll | pad-sequence-v2-slow | 0.2.0 | 200 | 12 | 0.77949 | 9 | +0.2295 |
| 014_T072C65047 | bona_fide | verify | pad-sequence-v2-slow | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.80373 | 11 | +0.2537 |
| 015_T072C65047 | bona_fide | verify | pad-sequence-v2-slow | 0.2.0 | 200 | 12 | 0.73359 | 6 | +0.1836 |
| 016_T074F47994 | bona_fide | enroll | pad-sequence-v2-slow | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.75849 | 9 | +0.2085 |
| 017_T074F47994 | bona_fide | enroll | pad-sequence-v2-slow | 0.2.0 | 200 | 12 | 0.69272 | 9 | +0.1427 |
| 018_T074F47994 | bona_fide | verify | pad-sequence-v2-slow | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.65285 | 5 | +0.1028 |
| 019_T074F47994 | bona_fide | verify | pad-sequence-v2-slow | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.73328 | 8 | +0.1833 |
| 020_T074F47994 | bona_fide | verify | pad-sequence-v2-slow | 0.2.0 | 422 LIVENESS_FAIL | 12 | 0.74531 | 11 | +0.1953 |
| 021_T074F47994 | bona_fide | verify | pad-sequence-v2-slow | 0.2.0 | 200 | 12 | 0.73876 | 11 | +0.1888 |

The three engine 0.1.0 heuristic records carry no `pad` block at all, so they
have no continuity data. They are listed for completeness of the 17 and are
excluded from every statistic below. That leaves **14 records with continuity
data**, matching the 14 the reviewer counted under engine 0.2.0 / HEAD_SEQUENCE.

Full per-frame sequences for the two rejections:

    009_P01  1.000 0.940 0.958 0.957 0.951 0.641 0.681 0.606 0.423 0.371 0.359 0.532
    010_P01  1.000 0.965 0.966 0.951 0.947 0.750 0.643 0.600 0.546 0.411 0.462 0.482

Both are a step at frame 5 then a decay. Neither has a single-frame outlier.

## 4. Item 2 - separated by policy version

| Group | n | Worst | Median | Best | Below 0.55 |
|---|---|---|---|---|---|
| pad-sequence-v1 | 5 | 0.35938 | 0.61025 | 0.94185 | 2 |
| pad-sequence-v2-slow | 9 | 0.65285 | 0.73876 | 0.80373 | 0 |

**The four passing v2-slow scans specifically** - the reviewer's question:

| Scan | Min | Margin to 0.55 |
|---|---|---|
| 013 enroll | 0.77949 | +0.2295 |
| 015 verify | 0.73359 | +0.1836 |
| 017 enroll | 0.69272 | +0.1427 |
| 021 verify | 0.73876 | +0.1888 |

The closest any *passing* v2-slow scan came to the threshold is **0.69272,
margin +0.143** (scan 017). The closest any v2-slow scan of any outcome came is
**0.65285, margin +0.103** (scan 018, which was rejected by the head gate, not
by continuity). No v2-slow scan came within 0.10 of 0.55.

That is the reassuring reading. The unreassuring reading is in the shape. Every
v2-slow scan follows the same profile: frames 0-3 at 0.90-1.00, then a step at
frame 4 or 5 into a plateau, and that plateau sits between 0.65 and 0.85 for
all nine. The turn itself costs 0.20-0.30 of similarity in every single genuine
capture. What is left over is the 0.10-0.25 margin in the table. A non-turning
capture keeps 0.39 of margin (scan 008, which barely moved: pose excursion
0.064 against a group range of 1.4-10.9). So on the current live policy the
continuity rule is already spending most of its headroom on the movement the
challenge demands, every time, on every genuine user.

**Is it sliding toward 0.55?** On this data, not within the v2-slow group -
there is no trend across attempts and no v2-slow scan is near the line. But the
group is nine attempts from **two subjects** (T072C65047, T074F47994) in one
lighting condition, and the whole v1 group is a **third, single subject**
(P01). Subject is fully confounded with policy here, so the gap between the two
groups cannot be attributed to the cadence change. What can be said is that one
of the three subjects in the snapshot produces continuity minima in the
0.36-0.61 band while turning, and that subject was never recorded on v2-slow.
**Unverified:** whether P01 on v2-slow would stay above 0.55. That is one
recording, and it is exactly the kind of case B4 should carry.

Treat this as a latent failure on the current policy with an unmeasured rate,
not as a v1 artefact. The rule is identical in both policies; only the subjects
differ.

## 5. Why it is rotation, not image quality

Per scan, head-pose excursion is the peak-to-peak of the mean of the two yaw
ratio components the shipped `yaw()` returns, taken from the `head_sequence`
block of the same decision record. Against the continuity minimum:

| Scan | Pose excursion | Continuity min |
|---|---|---|
| 008_P01 | 0.064 | 0.94185 |
| 021 | 1.445 | 0.73876 |
| 020 | 2.329 | 0.74531 |
| 016 | 2.614 | 0.75849 |
| 014 | 2.645 | 0.80373 |
| 013 | 2.972 | 0.77949 |
| 015 | 3.420 | 0.73359 |
| 019 | 3.611 | 0.73328 |
| 018 | 4.105 | 0.65285 |
| 017 | 4.248 | 0.69272 |
| 011 | 4.737 | 0.61025 |
| 012 | 5.451 | 0.61050 |
| 010 | 9.655 | 0.41147 |
| 009 | 10.890 | 0.35938 |

Pearson r = **-0.970** over all 14; r = -0.989 within v1 (n=5) and r = -0.605
within v2-slow (n=9, a narrow excursion range). The ordering is almost perfectly
monotone: the more the participant turned, the lower the floor, across two
policies and three subjects. Scan 008 sits at the top of both columns - the
participant who did not turn kept all of the margin and was rejected by the head
gate instead.

So the earlier hedge is withdrawn to a footnote. The two `LOW_QUALITY`
rejections were caused by head rotation large enough that the recognition
embedding of the turned face stopped matching the frontal frames at 0.55 - the
identity threshold reused as a continuity threshold, against a capture that
demands the rotation. Per-frame PAD agreed the face was live in every frame of
both scans (`live_frames: 12`, `minimum_live_score` 0.841162 on scan 9).
**Unverified, and now weakly held:** frames were not opened, so blur or partial
occlusion at the pose extremes cannot be excluded as a contributing factor. It
cannot be the main factor, because the effect is smooth, monotone in rotation,
and present in every capture that turned.

## 6. Item 3 - what v3-guided does to this

**Unchanged mechanically. Worse in share.**

- Mechanically unchanged. `anti_spoof.py:217` has no policy branch, v3-guided
  did not touch the file (its hash in the v4 manifest is unmoved,
  `707f0299...5802`), and continuity runs before the head gate's verdict is
  consumed. v3 asks for the same 0.20 target with the same 12 frames and the
  same `FRONTAL_LIMIT`, so the pose excursion a participant produces is
  unchanged. The reviewer's expectation is what the data supports.
- Worse in share, not in rate. v3-guided exists to convert head-gate rejections
  into completions - 4 of 9 to 8 of 9 on the replay traces. Every capture it
  rescues still has to clear continuity, and continuity is the gate immediately
  in front of it. As head-gate rejections fall, continuity becomes the leading
  cause of genuine rejection by default, on an unmeasured rate.
- One second-order effect worth stating: v3 rewards a *decisive* turn. Its
  per-frame stillness rule and its leading-run rule both favour a participant
  who settles and then commits to the movement. A more decisive turn is a larger
  pose excursion, and section 5 says pose excursion is what drives the floor
  down. So if v3 changes behaviour at all, it changes it in the direction that
  costs continuity margin. **Unverified:** no capture has ever been recorded
  under v3-guided. Every v3 number in B2 and B3 came from replaying head-gate
  traces, which never touch PAD. This effect can only be measured in the next
  recording round.

## 7. Item 4 - proposed row, not applied

`engine/app/anti_spoof.py` is on the plan's never-changeable list twice over:
explicitly as "the PAD decision rule", and implicitly as "the match threshold
0.55", which is the same literal in a second role. **The plan table was not
edited.** Adding this file to the changeable table, even as `pending`, would
contradict the never-changeable list, and that is the reviewer's decision to
make, not an agent's. The row text below is ready to paste if and only if the
reviewer decides it belongs there.

Row as proposed:

> **`engine/app/anti_spoof.py`** - naming and record only. Give the continuity
> threshold its own module constant, `CONTINUITY_MIN = 0.55`, and use it at
> line 217 in place of the literal. Emit it in the decision record as
> `pad.continuity_threshold`. The value stays 0.55, the comparison stays `<`,
> the rule stays unbranched by policy, the pairing rule is untouched, PAD
> scoring is untouched and the wire format is untouched. No decision changes on
> any of the 17 records.

What the row buys, and nothing more: the two copies of 0.55 stop being
accidentally identical, a decision record states which continuity threshold
judged it, and a future PAD policy version has something to branch on. It does
not change a single outcome.

Three things the reviewer should weigh:

- **This is probably the wrong instrument.** If the intent is to fix genuine
  rejection, the fix is not a rename - it is a different pairing rule or a
  different threshold, and neither belongs in this row. Either is a **new PAD
  policy version** with its own recorded definition and its own `pad.policy`
  string, the same treatment `pad-sequence-v3-guided` got, not an edit to a
  frozen rule. Under BIOMETRIC_TRACK.md that also restarts held-out evaluation.
- **Continuity is a security control, not a quality filter.** The all-pairs
  rule is what stops one face being swapped for another mid-capture. Loosening
  it - a higher tolerance, or neighbour-only pairing - trades genuine completion
  directly against that attack class, and B5 has no data on it. Any such change
  needs the attack set before the tolerance, not after.
- **The measurement does not yet justify any change.** Zero of nine v2-slow
  captures failed continuity and the worst margin was +0.103. The case for
  acting now rests on section 4's headroom argument and section 6's share
  argument, both of which are about an unmeasured rate. The cheap, honest next
  step is to carry `continuity_min` per scan into the B4 round as a recorded
  metric and let the new subjects decide, rather than to move a threshold on
  three subjects.

## 8. Commands

    .venv-pad\Scripts\python.exe  <scratch>\cont.py     per-scan continuity minima
    .venv-pad\Scripts\python.exe  <scratch>\c2.py       pose excursion and correlation
    grep -rn "0\.55" engine/app/*.py                    two literals, no others
    sed -n '150,245p' engine/app/anti_spoof.py          rule order and definition

No test suite was run: no source file was changed. The v4 baseline was clean
before this document and is clean after it (`100 protected, 0 mismatched,
0 missing`); this file is new and unprotected.

## 9. Unverified

- No frames were opened. Blur and occlusion at the pose extremes are not
  excluded as a contributing factor to the scan 9 and 10 minima, only demoted.
- Subject is confounded with policy across the two groups. The v1-versus-v2-slow
  gap is not attributable to the cadence change.
- No capture has ever been scored under `pad-sequence-v3-guided`. Section 6 is a
  mechanism argument from source plus the v1/v2-slow data, not a measurement.
- Two subjects and nine attempts in one lighting condition is development data.
  No continuity failure rate can be quoted from it.
- Device class per record was not established from this metadata.

## 10. Next action

Stop. B4 is the reviewer's to schedule, Phase 2 is unapproved, the
`anti_spoof.py` row is unapplied and the plan table is unedited.
