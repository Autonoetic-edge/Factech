# Parallel biometric validation and improvement track

Status: ACTIVE. Created 20 September 2026; adopted into HARDENING_PLAN.md on
20 September 2026 by reviewer acceptance of B1. This track runs alongside the
engineering-hardening plan and is governed by that plan's protected-file change
procedure. It does not authorize a live policy change, capture transfer,
threshold change, deployment, or use of synthetic results as biometric efficacy
evidence. Local source may now change only within the file table in that
procedure; unfreezing local source is not deploy authority and M8 still governs
every live change.

## Purpose

Improve genuine-user completion, measure recognition accuracy, and establish
physical spoof resistance without weakening the frozen live system or mixing
incompatible policy generations.

Engineering milestones M4-M8 remain authoritative for security, API/SDK correctness,
QA infrastructure, deployment and live integration. This track supplies the physical
and statistical evidence needed for Face recognition / ML and Liveness / spoof
resistance scores.

## Current evidence baseline

The read-only VPS snapshot is stored in
`evidence/BIOMETRIC_METADATA_SNAPSHOT_2026-09-20.csv`. It contains metadata and
sanitized decisions only. No frames, scans, embeddings or images were accessed or
copied.

The 17 retained records span three incompatible generations and must not be pooled:

- Three heuristic-build records, including one accepted phone-screen photo.
- Five `pad-sequence-v1` genuine enrollments, all rejected.
- Nine `pad-sequence-v2-slow` genuine-labelled attempts in daylight with glasses.

For `pad-sequence-v2-slow`, enrollment accepted 2/3 and verification accepted 2/6.
Learned PAD classified all nine as live, while the head-sequence gate rejected five
attempts. There are no current-policy impostor or physical attack records. Recognition
was reached on only two verification attempts, with similarities 0.98256 and 0.89619.

## Boundaries

- Keep live models, thresholds, preprocessing, capture timing and policy unchanged
  until a separately versioned candidate passes this track and M8 authorizes rollout.
- Use self-service enrollment and template management only.
- The team collects physical biometric data under recorded consent. The engineering
  agent may consume permitted metadata but must not download frames from the VPS.
- Keep development, calibration and final held-out participants/attempts separate.
- Preserve first attempts, retries, cancellations, missing receipts and failures.
- Never combine results from different engine/model/policy versions.

## B1 - Diagnose genuine rejection

1. Record failure stage for every attempt: camera/readiness, face detection, quality,
   PAD, continuity, challenge, recognition, transport or recording.
2. Verify participant-relative left/right on Samsung A13 and at least one additional
   supported phone, including mirrored preview behavior.
3. Measure initial-pose landmark noise while the participant is genuinely still.
4. Confirm whether failures arise from the three-frame baseline, reaction timing,
   instruction ambiguity, landmark noise or genuine incorrect movement.

Exit: failure causes and denominators are known; no threshold or policy is changed.

## B2 - Develop a stable candidate policy

Create a new development-only policy version. Candidate changes may include:

- A camera-readiness gate requiring centered, sufficiently large, frontal and stable
  face evidence before challenge issuance/capture.
- A visible countdown after readiness.
- A robust initial pose estimate using more observations and median/MAD-style
  outlier handling instead of a three-sample peak-to-peak veto.
- Typed participant-relative direction events shared by server, SDK and UI.
- One randomized small turn, or turn-and-return, rather than two long turns.
- Dense-landmark/3D-pose evaluation as an alternative to the five-point yaw proxy.

Do not use blink or smile alone as proof of liveness. Do not lower the recognition
or PAD threshold to repair challenge failures. Any tolerance change requires replay
and cue-synchronized-video evaluation.

Development exit target: at least 90% first-attempt completion in the scoped device
and participant set, with no observed regression in the development attack set.
This is a tuning gate, not final validation.

## B3 - Freeze the candidate

Freeze and record exact source/artifact identity, models, hashes, preprocessing,
thresholds, challenge logic, timing, SDK/UI versions and supported devices. Lock the
final evaluation protocol before collecting held-out results. Changes after freeze
create a new version and restart held-out evaluation.

## B4 - Held-out recognition evaluation

Use participants and attempts not used to tune B2. Include repeat sessions, supported
phones, lighting, glasses/no-glasses and representative demographic coverage.

Report separately:

- Genuine enrollment failure and genuine verification FNMR.
- Impostor FMR, with impostor pairs constructed independently of tuning.
- First attempts and retries.
- Per-device/condition/subgroup results and confidence intervals.
- Cases prevented from reaching matching, rather than counting them as recognition
  scores of zero.
- ROC/DET analysis from development data and one fixed operating threshold evaluated
  on held-out data.

## B5 - Physical PAD and injection evaluation

Test genuine presentations and, separately, prints, phone/laptop photos, ordinary
videos, cue-synchronized videos, moved/tilted displays and other agreed presentation
attack instruments. Test prerecorded-stream/virtual-camera injection separately
because browser camera input is not a trusted origin.

For each attack class report PAD outcome, challenge outcome and final system outcome.
An attack rejected by the challenge is not automatically a PAD detection. Report
APCER/IAPAR and BPCER with denominators and confidence intervals.

## B6 - Independent final review

An evaluator who did not implement or tune the candidate repeats the frozen protocol
and reviews missing data, exclusions, labels and calculations. Resolve critical
capture-injection limitations or document a compensating architecture such as a
native trusted capture component or passkey-based routine authentication.

## Score gates

- 6/10 recognition: stable first-attempt behavior plus initial held-out, fixed-threshold
  genuine and impostor evidence.
- 7/10 recognition: credible multi-person/device/condition evaluation with uncertainty
  and subgroup reporting.
- 8/10 recognition: strong independent evidence, drift monitoring and resolved
  provenance/licensing.
- 9/10 recognition: statistically strong representative external validation and
  production monitoring with no unresolved high-risk accuracy gap.
- 5-6/10 liveness: stable genuine completion plus current-policy physical attack data.
- 7/10 liveness: broad held-out attack coverage across supported devices.
- 8/10 liveness: injection defenses and independent adversarial evaluation.
- 9/10 liveness: external standards-aligned evaluation, trusted capture or equivalent
  compensating controls, operational monitoring and no unresolved major attack class.

## Immediate next actions

1. Keep M4 progressing independently.
2. Create the B1 metadata schema and team test worksheet without collecting new data.
3. Have the team execute a small diagnosis round on the frozen current policy.
4. Design candidate policy B2 in isolated development; do not deploy it.
5. Pre-register held-out B4/B5 protocols before the candidate freeze.
