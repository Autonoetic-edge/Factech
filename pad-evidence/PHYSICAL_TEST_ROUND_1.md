# Physical phone round 1 — policy frozen

> HISTORICAL: this document describes pad-sequence-v1 and its original pending
> round. Do not use its timing, build or awaiting-tester status as current guidance.
> Follow ../TEAM_TEST_PROTOCOL.md (from repository root: TEAM_TEST_PROTOCOL.md)
> and HARDENING_PLAN.md instead. The original record below is retained as evidence.

Site: https://facetech.31.97.186.120.sslip.io/
Review: https://facetech.31.97.186.120.sslip.io/review
Build: **eval-pad-80a3dfa1dfe9**. Policy: **pad-sequence-v1**.
Use existing Basic Auth credentials. Refresh the page; confirm it says the policy
above. The public `/health` endpoint shows the build. No credentials need to be
sent to the assistant. Engine restart cleared prototype enrollment memory; make
a fresh genuine enrollment first. Old stored captures remain available.

## Setup for every attempt

Use the Samsung A13 as the capture phone, front camera, upright. Keep the capture
phone still, face centered, and rotate the head only slightly in the prompted
direction. Hold each pose until the next instruction. Left/right mean the
subject's own left/right. Recording lasts about eight seconds.

Check recording consent only if everyone/person-image shown consents. Select the
person code, test case and lighting **before** pressing Enroll/Verify. For glasses,
check the glasses field. Keep nonconsenting people out of the full camera frame.
Check the result/log for request ID and “Recording saved: capture …; diagnostics
saved”. A cancellation or missing receipt is **not** a recorded rejection; report
it separately. Never relabel a scan after seeing the result.

Use participant **P01** for the first genuine person and their displayed images;
**P02** is a different consenting genuine person. These are example anonymous
codes; keep the same mapping for the round. Target `padr1-p01` always refers to
P01's genuine template. Do not enroll P02 into it. Phone-screen presentations
use the second phone, with an image/video of P01. Do not use a laptop screen.

## Predetermined first block

| # | Test | Target user ID | Person / case | Action |
|---|---|---|---|---|
| 1 | Genuine enrollment | `padr1-p01` | P01 / Genuine person — self | Enroll once in daylight; follow prompts |
| 2 | Genuine verification | `padr1-p01` | P01 / Genuine person — self | Verify three separate attempts; fresh challenge each time |
| 3 | Phone photo enrollment | `padr1-p01-photo` | P01 / Photo on a screen | Enroll three attempts presenting a still photo on the second phone |
| 4 | Phone photo verification | `padr1-p01` | P01 / Photo on a screen | Verify three attempts presenting that photo against the genuine enrollment |
| 5 | Phone video enrollment | `padr1-p01-video` | P01 / Video on a screen | Enroll three attempts using a pre-recorded video of P01 turning both ways |
| 6 | Phone video verification | `padr1-p01` | P01 / Video on a screen | Verify three attempts with the video against the genuine enrollment |
| 7 | Different person | `padr1-p01` | P02 / Genuine person — wrong identity | P02 verifies three times; never enroll P02 into P01's ID |
| 8 | Glasses | `padr1-p01` | P01 / Genuine person — self, glasses checked | Verify three times in daylight |
| 9 | Ordinary room light | `padr1-p01` | P01 / Genuine person — self, Room light | Verify three times; glasses field reflects reality |

Start with **#1 and one attempt from #2**, then send their request IDs (or capture
numbers), or say “first two tests done”. I will read metadata/scores in place and
check coverage, head-turn interpretation and decision reasons before guiding the
rest. This inspection does not change policy or tune on these attempts. If genuine
enrollment is rejected, retain the first failure and report it; do not start the
verification block without a valid fresh genuine template. Count any retry.

For attacks, keep the display reasonably face-sized and framed; do not switch to
the genuine person's face halfway through. Moving/tilting a photo display to try
to satisfy the prompts is an attack variation and should be reported as such.
For video, use a recording rather than a live call. Report if playback was restarted,
paused or synchronized to a cue. A fixed recording may fail the random action
order; that is an overall rejection, but not evidence that PAD itself rejected it.

If any attack enrollment succeeds, its template is isolated under the -photo/-video
ID. Report it immediately; do not use that template to claim identity accuracy.
No capture/template deletion or relabeling is implied by this protocol.

## Round review and denominators

No fresh physical attempts have been performed by the assistant. Round status:
**awaiting tester**. Planned sample size is not an observed denominator.

For each submitted/consented attempt record: capture/request ID, build/policy,
operation, person, case, lighting/glasses, HTTP outcome, rejection reason,
per-frame/aggregate PAD scores, usable indices/third coverage, continuity minimum,
head-sequence order/timing outcome, heuristic liveness, identity similarity if
computed, and latency. “Not computed” is never treated as zero.

Report separately:

- Screen-photo enrollment accepts / completed screen-photo enrollments.
- Screen-photo verification accepts / completed screen-photo verifications.
- Screen-video enrollment accepts / completed screen-video enrollments.
- Screen-video verification accepts / completed screen-video verifications.
- Wrong-person matches / completed wrong-person verifications.
- Genuine enrollment rejects / completed genuine enrollments.
- Genuine verification rejects / completed genuine verifications, with glasses
  and lighting subgroups retained.
- Cancellations, infrastructure failures, missing evidence/receipts and retries,
  separately from biometric outcomes. Do not silently drop inconvenient trials.

An attack can fail the overall system while PAD calls it live. Report both the
overall acceptance rate and the PAD-specific outcomes. Review each round in place;
do not transfer VPS JPEGs/captures locally. Existing seven-day retention continues.

Any integration/policy change gets a new version and a fresh round. The attempts
that informed a change are development evidence, not independent validation. If
screen attacks still pass, verify crop/scaling/channel/class/ensemble/continuity
integration first. If correct, report a MiniFASNet domain limitation and assess a
stronger model or provider with documented mobile-screen evaluation and licensing;
do not manipulate the existing thresholds or select favorable frames.

This small round is preliminary evidence. RGB PAD, a small head-turn challenge
space and a browser camera path cannot certify spoof resistance or establish
trusted camera origin.
