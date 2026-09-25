# Facetech hardening plan and session handoff

Updated: 20 September 2026. Status: M0-M3 complete; M4 locally and staging verified on the
owned loopback stack. M6 in progress. No live changes made by this track. An AmFatec
preview is deployed with self-registration open and does NOT yet carry the M4 controls;
closing that gap needs an approved gateway-and-engine release (see M4 evidence).
The parallel biometric track in docs/hardening/BIOMETRIC_TRACK.md is ADOPTED as of
20 September 2026 and is governed by the protected-file change procedure below.
Owner of implementation: next working session, one agent. Team owns physical testing.

## Read this first

This is the authoritative forward-work plan. It supersedes older NEXT WORK lists,
deployment suggestions and readiness plans. Historical reports remain evidence of
their own release, not instructions to redeploy it. Read README.md for the document
map, then pad-evidence/consent-v6/REVIEW.md for the source-based findings.

Objective: reach a defensible engineering readiness assessment of at least 9/10 in
architecture, backend, security, SDK, QA, privacy and deployment. A completed checklist
does not automatically confer that score. Recognition, liveness and overall biometric
QA also require independent physical evidence collected by the team. No certification
or production accuracy claim is implied.

## Non-negotiable working boundaries

- Use E:\Factech explicitly as the working directory. E:\Facetech is the original;
  never edit it or stop its laptop port-8000 service. Do not assume Git exists.
- One agent. Preserve existing changes. Inspect applicable AGENTS.md before editing.
- The team is actively testing the VPS. Freeze its capture UX, SDK capture timing,
  model files, preprocessing, matching, PAD, challenge policy and thresholds.
- Do not restart/recreate the live engine, gateway or Caddy, run migrations against
  live storage, change live authentication/routes/retention, or deploy hardening
  changes while testing continues. Complete local work and isolated staging first.
  Live integration is milestone M8 and requires an explicitly coordinated test pause
  and deployment window. This is an implementation of the user's live-testing
  constraint, not a restriction on independent local progress.
- Do not run load tests, adversarial requests or synthetic enrollments against the
  team environment. Staging must have separate ports, storage, credentials and
  resource limits; no shared capture mounts or live database connections. Prefer
  local/CI staging. Do not provision staging on the active VPS without an agreed
  resource budget; CPU/memory contention can invalidate team results.
- Use SSH keys with BatchMode=yes and StrictHostKeyChecking=yes for permitted VPS
  reads. Never print secrets, passwords, tokens, private keys or .env contents.
- Preserve Basic Auth, captures, labels, seven-day retention and rollback releases.
  Do not delete/relabel live captures or enable the disabled exporter.
- A prior automatic approval review blocked VPS biometric frame/JPEG transfer to
  the laptop. Do not retry via another tool. Read-only metadata/score review is
  allowed. No verified local capture backup exists. This plan does not authorize
  raw-data downloads or override that block.
- Use synthetic nonbiometric fixtures for new persistence, encryption, deletion,
  export-failure and recovery tests. No real capture transfers are needed.
- Check weekly usage before implementation, after local changes/tests and before
  any future deployment; report percentage-point change. Avoid rerunning unchanged
  suites unless a change or failure warrants it.

## Last verified baseline (not a fresh live inventory)

Source: pad-evidence/consent-v6/REVIEW.md, 19 September 2026.

| Component | Baseline |
|---|---|
| Team page | https://facetech.31.97.186.120.sslip.io/?v=ui-v6 |
| Gateway | eval-pad-0e45ad88290f, UI ui-v6 |
| Engine | eval-pad-0d87e7b9a807, engineVersion 0.2.0 |
| Policy | pad-sequence-v2-slow, 12 frames at 1400 ms, approximately 16 seconds |
| Thresholds | match 0.55; heuristic liveness 0.50; mandatory learned PAD |
| Services | facetech-demo-engine-1 / gateway-1 / caddy-1 |
| Capture storage | /var/lib/facetech/eval-d36fa4946ddf mounted at /captures |
| Retention | seven days; exporter disabled |
| Enrollment | in memory; restart loses templates and nonces |
| Consent | explicit remembered grant per tester/browser, storage-consent-v1 |

Last review found 11 retained captures, IDs 5–15; that count changes with team
activity and expiry. Captures 13/14/15: enrollment accepted, verification rejected
by initial head stillness, verification matched. All three were saved with
diagnostics; zero reported storage failures at that snapshot. This is one tester,
not a population estimate. Current-policy physical attack efficacy was unmeasured.
76 UI tests passed for ui-v6; real Samsung remembered-consent confirmation was pending.

Do not tune the head gate on capture 14 or replace the models as part of this plan.
Historical gateway rollback instructions are in consent-v6/REVIEW.md; they are not
a rollback plan for future database/authentication changes.

## Execution and status rules

Select the earliest ready milestone. Finish implementation, focused tests, evidence
and handoff before marking it complete. Continue independent work if a later live
gate is unavailable. Use statuses: TODO, IN_PROGRESS, LOCAL_VERIFIED, STAGING_VERIFIED,
COMPLETE, BLOCKED. Local completion is never described as live deployment.

| ID | Milestone | Depends on | Status | Evidence |
|---|---|---|---|---|
| M0 | Architecture decisions and acceptance contract | — | COMPLETE | [M0 evidence](docs/hardening/evidence/M0.md) — design/local checks only |
| M1 | Identity and authorization boundaries | M0 | COMPLETE | [M1 evidence](docs/hardening/evidence/M1.md) — durable PostgreSQL authorization and real Keycloak/HTTPS synthetic staging verified; no live integration |
| M2 | Durable templates and atomic challenge state | M0, M1 contracts | COMPLETE | [M2 evidence](docs/hardening/evidence/M2.md) — frozen pipeline behind the Analyzer port, model-bound PostgreSQL templates, idempotent operations, capacity, migration and restore drill verified with synthetic data; no live integration |
| M3 | Consent, privacy and protected data lifecycle | M1, M2 | COMPLETE | [M3 evidence](docs/hardening/evidence/M3.md) — self-consent, recording/withdrawal, envelope keys, purge/retry and fresh-ledger quarantine verified with synthetic data; M7 operational dependencies remain |
| M4 | Resource protection and security hardening | M1, M2 | LOCAL_VERIFIED, STAGING_VERIFIED | [M4 evidence](docs/hardening/evidence/M4.md) — streaming bounds measured, principal/source limits, admission and Retry-After, fail-closed startup and credential rotation, browser/log hardening, dependency and image disposition; S06 written, S07 partly closed; no live change |
| M5 | Backend and SDK contract correctness | M1–M4 contracts | TODO | — |
| M6 | CI, failure-path and device QA | Starts M0; final gate M1–M5 | IN_PROGRESS | [M6 evidence](docs/hardening/evidence/M6.md) — 666 tests, consent/privacy/keys/purge/recovery plus durable authorization/real OIDC staging and frozen hashes; whole-system/physical gates open |
| M7 | Reproducible deployment and recovery | M2–M6 | TODO | — |
| M8 | Coordinated integration and independent review | M1–M7 plus test window | TODO | — |

For each milestone create docs/hardening/evidence/Mx.md with: findings, files changed,
decisions, exact commands/environment, test results, limitations, rollback, and next
action. Do not include secrets or biometric payloads. Maintain this status table.
Record failed checks and fixes; do not record planned tests as passing.

## Biometric track B1-B6

docs/hardening/BIOMETRIC_TRACK.md is a real track of this plan, not a proposal.
It owns genuine-user completion, recognition accuracy and physical spoof
resistance. It supplies the evidence M6 needs for those scores and it never
substitutes for M8. Its evidence files are docs/hardening/evidence/Bx.md and use
the same headings and the same statuses as the milestone table.

| ID | Stage | Depends on | Status | Evidence |
|---|---|---|---|---|
| B1 | Diagnose genuine rejection | — | COMPLETE | [B1 evidence](docs/hardening/evidence/B1.md) — numbers-only replay fixture; 9 of 9 pad-sequence-v2-slow traces reproduce their recorded verdict; 4 pass / 5 fail is the comparison scoreboard |
| B2 | Stable candidate policy, development only | B1 | COMPLETE | [B2 evidence](docs/hardening/evidence/B2.md) — pad-sequence-v3-guided, development only, off by default; 8 of 9 traces pass against the B1 scoreboard of 4 |
| B3 | Freeze the candidate | B2 | COMPLETE | [B3 evidence](docs/hardening/evidence/B3.md) — pad-sequence-v3-guided frozen: recorded definition, manifest source-sha256.v4.json, fixture and replay hashes, 8 of 9 scoreboard re-verified at freeze |
| B4 | Held-out recognition evaluation | B3 | TODO | — |
| B5 | Physical PAD and injection evaluation | B3 | TODO | — |
| B6 | Independent final review | B4, B5 | TODO | — |

The B1 scoreboard is the fixed comparison for B2. Any candidate is reported
against it as an old-versus-new table over the same nine traces. Nine
development traces from four or five people are development data, not
validation; they establish neither recognition accuracy nor spoof resistance.

## Protected-file change procedure

Adopted 20 September 2026 on reviewer acceptance of B1. Before this the 100
protected hashes had no change path of any kind, which blocked the B2 work this
plan now adopts; M4 declined a correct one-line test fix for that reason and it
is still unfixed. This procedure is the only way a protected file changes.

Baselines accumulate and are never rewritten or deleted.
`docs/hardening/evidence/baseline/source-sha256.json` is retained unchanged as
evidence of the M0-M6 freeze. The ACTIVE manifest is
`docs/hardening/evidence/baseline/source-sha256.v4.json`. `source-sha256.v2.json`
is retained as the adoption-time baseline and `source-sha256.v3.json` as the B2
baseline. Verify the active one with

    .venv-pad\Scripts\python.exe tools\hardening_verify_baseline.py

**What may change.** Only a file named in the table below, and only in the way
the row allows. A row marked `pending` is a proposal and authorizes nothing; no
protected file may be edited against it until the user has approved that row and
the Approved column records who approved it and when.

| File | Allowed change | Approved |
|---|---|---|
| engine/app/head_sequence.py | Add a NEW named policy version beside pad-sequence-v2-slow. The v2-slow path stays byte-for-byte identical in behaviour and stays the default. | user, 20 Sep 2026 |
| engine/app/challenge.py | Select the new policy version by configuration only. No change to EXPIRY_MS or to any v2-slow parameter. | user, 20 Sep 2026 (not used in B2; the selector lives in head_sequence.py). CORRECTION 24 Sep 2026: the file was NOT left unchanged — the switch trial edited `_draw_params()` HEAD_SEQUENCE params (`switch_ms` 9000-9400 ms jitter, `settle_ms` 3200, `target` 0.18); see the "switch trial" row below |
| engine/app/liveness.py | Route to the selected policy version. No change to the scoring rule or to any threshold. | user, 20 Sep 2026 |
| engine/app/main.py | Carry a coarse retry hint, only if the gateway contract allows one. | pending — explicitly NOT approved on 20 Sep 2026; blocked on the C++ gateway team's answer, see docs/hardening/evidence/B2.md |
| packages/face-sdk/src/workflow/session.ts, types.ts, index.ts | Additive public surface only: a prepare-camera step and typed participant-relative direction events. Existing callers keep working. | pending — explicitly NOT approved on 20 Sep 2026; deferred to Phase 2 |
| apps/tests/face-guide.test.js | Update the stale `?v=ui-v6` script-tag expectation at line 614 to match apps/integration-demo/index.html, which is correct and is NOT changed. Test expectation only; no page, no behaviour. | user, 20 Sep 2026 — applied 21 Sep 2026 in B3; manifest source-sha256.v4.json |
| engine/app/head_sequence.py (second row) | In the appended development block only, make `validate_for()` stamp the head gate's own policy name into the result it returns for BOTH policies, so a decision record says which head-gate version judged it. One field, `policy`, one of the two existing name constants. No scoring change, no threshold, no wire-format change, and `validate()` itself is not edited. | user, 24 Sep 2026 — approved for the policy stamp only (live on the VPS, decision records 24 Sep 07:11/07:12 IST). The deployed code stamps the literal `amfatec-switch-trial-20260922`, not one of the two name constants, and goes beyond this row; see the "switch trial" row below |
| engine/app/head_sequence.py + engine/app/challenge.py (switch trial, recorded after the fact 24 Sep 2026) | Already applied and deployed as build `amfatec-switch-trial-20260922` WITHOUT a prior approved row. The default v2-slow `validate()` path now uses a timed left→right switch (`switch_ms`), frontal limit 0.30 (frozen 0.25), stillness 0.20 (frozen 0.15), target 0.18 (frozen 0.20), plus a one-frame switch grace. These touch the "not changeable by any milestone" list below. | owner, 25 Sep 2026 — accepted (D2); the frozen list below is amended to the deployed values (frontal 0.30, stillness 0.20, target 0.18, one-frame switch grace) |
| engine/app/challenge.py (Phase 1) | `CHALLENGE_POLICY=single-turn-v1` makes `issue()` hand out LOOK_LEFT/LOOK_RIGHT (settle 1200-2000 ms); any other value keeps HEAD_SEQUENCE exactly. The capture-before-issue binding applies to every action. No change to EXPIRY_MS, HEAD_SEQUENCE params or any v2-slow parameter. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 1) — applied 24 Sep 2026; NOT rebaselined, see docs/UX_FIX_LOG.md "Liveness Phase 1" |
| engine/app/anti_spoof.py (Phase 1) | Add the opt-in PAD policy name `pad-single-v1` beside v2-slow: same rules, gap limit 1400 ms, used only for LOOK_LEFT/LOOK_RIGHT scans. v2-slow unchanged and default. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 1) — applied 24 Sep 2026; NOT rebaselined, see docs/UX_FIX_LOG.md "Liveness Phase 1" |
| engine/app/main.py (Phase 1) | `_secure_analysis` accepts the actions the flag issues and passes the matching PAD policy name; responses report that name. Nothing else. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 1) — applied 24 Sep 2026; NOT rebaselined, see docs/UX_FIX_LOG.md "Liveness Phase 1" |
| engine/models/anti_spoof/POLICY.md (Phase 1) | Append a "pad-single-v1" section; the v2-slow text is not edited. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 1) — applied 24 Sep 2026; NOT rebaselined, see docs/UX_FIX_LOG.md "Liveness Phase 1" |
| engine/app/geometry.py (Phase 2, new file) | New pure function: nose homography residual over the 5-point keypoints, fitted across all settle frames, scored per turned frame with its yaw. Log only: it returns a record and never a verdict. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 2, log mode only) — see docs/UX_FIX_LOG.md "Liveness Phase 2"; NOT rebaselined |
| engine/app/main.py (Phase 2) | `PAD_GEOMETRY=off|log` (default off; `on` behaves as `log`, it never rejects in this phase): after the challenge and liveness step and before matching, run geometry.py and put its record in the trace. Pass the PAD policy name to `check_liveness`. Record the `X-Facetech-Precheck` request header in the trace. No decision, threshold, response body or wire-format change. | user, 24 Sep 2026 (in-session instruction, Phase 2) — see docs/UX_FIX_LOG.md "Liveness Phase 2"; NOT rebaselined |
| engine/app/trace.py (Phase 2) | Two new trace fields, `geometry` and `precheck`, written only when set (flag off and no header = today's line byte for byte). | user, 24 Sep 2026 (in-session instruction, Phase 2) — see docs/UX_FIX_LOG.md "Liveness Phase 2"; NOT rebaselined |
| engine/app/liveness.py (Phase 2) | Under PAD policy `pad-single-v1` only (a new optional argument; absent = today): the duplicate signal allows the frames of the longest settle hold (5 at 500 ms) instead of 3, and the motion signal compares frames 3 apart (≈1500 ms, the v2-slow time scale) instead of neighbours. Thresholds 0.50 and MOTION_FLOOR/LIVE unchanged; every other policy byte for byte unchanged. | user, 24 Sep 2026 (in-session instruction, Phase 2) — see docs/UX_FIX_LOG.md "Liveness Phase 2"; NOT rebaselined |
| engine/app/aenet.py (Phase 3, new file) | New module: `PAD_ENSEMBLE` flag parsing and `AENetPAD`, the CelebA-Spoof AENet (NON-COMMERCIAL, demo only) over 3-5 sharp, frontal settle frames. Loads from its own `models/anti_spoof/aenet/` manifest, never from the MiniFASNet `manifest.json`. Log only: returns a record, never a verdict. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 3 behind its flag, default off, log only) — see docs/UX_FIX_LOG.md "Liveness Phase 3"; NOT rebaselined |
| engine/app/main.py (Phase 3) | `PAD_ENSEMBLE=minifas|minifas+aenet:log|minifas+aenet` (default `minifas` = today, AENet not loaded or run; both other values behave as log, nothing rejects in this phase): after the PAD, liveness and geometry steps and before the refusals and matching, run AENet and put its record, beside the MiniFASNet median, in the trace. No decision, threshold, response body or wire-format change; `warm_models` unchanged. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 3 behind its flag, default off, log only) — see docs/UX_FIX_LOG.md "Liveness Phase 3"; NOT rebaselined |
| engine/app/trace.py (Phase 3) | One new trace field, `ensemble`, written only when set (flag `minifas` = today's line byte for byte). | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 3 behind its flag, default off, log only) — see docs/UX_FIX_LOG.md "Liveness Phase 3"; NOT rebaselined |
| engine/models/anti_spoof/POLICY.md (Phase 3) | Append an "AENet (PAD_ENSEMBLE, log only, NON-COMMERCIAL demo only)" section; existing text not edited. `manifest.json` and the MiniFASNet models are NOT edited. | user, 24 Sep 2026 (in-session instruction to implement LIVENESS_UPGRADE_PLAN Phase 3 behind its flag, default off, log only) — see docs/UX_FIX_LOG.md "Liveness Phase 3"; NOT rebaselined |
| engine/app/challenge.py (Phase 4) | `CHALLENGE_POLICY=single-turn-glow-v1` issues the same LOOK_LEFT/LOOK_RIGHT challenge as single-turn-v1 plus a `glow` schedule (3-4 soft palette colours: blue, green, magenta, amber; no saturated red; 400 ms fades, at most 2 changes per second, randomised start and step times), stored with the nonce and returned from `issue()` as an extra `glow` key only under that policy; the verdict carries it. Any other policy value: `issue()` output, params and verdicts byte for byte unchanged. No change to EXPIRY_MS, HEAD_SEQUENCE params, `_params_match` or any v2-slow parameter. | user, 24 Sep 2026 (in-session instruction: "Implement LIVENESS_UPGRADE_PLAN.md Phase 4 (soft colour glow) — ENGINE PART ONLY this session"; FLASH_CHECK off/log/on, default off; SDK and page NOT touched) — see docs/UX_FIX_LOG.md "Liveness Phase 4"; NOT rebaselined |
| engine/app/flash.py (Phase 4, new file) | New module: `FLASH_CHECK` flag parsing and the glow check: per-frame colour shift of the forehead and both cheeks against the pre-glow baseline, correlated with the issued schedule at lags -2..+2 frames; pass if correlation >= FLASH_MIN (0.5, starting guess) at a lag of at most 1 frame. Too bright or unmeasurable = `inconclusive`, never a failure. | user, 24 Sep 2026 (in-session instruction: "Implement LIVENESS_UPGRADE_PLAN.md Phase 4 (soft colour glow) — ENGINE PART ONLY this session"; FLASH_CHECK off/log/on, default off; SDK and page NOT touched) — see docs/UX_FIX_LOG.md "Liveness Phase 4"; NOT rebaselined |
| engine/app/main.py (Phase 4) | `FLASH_CHECK=off|log|on` (default off = today, the check is never run). `log`: after the PAD, liveness, geometry and ensemble steps and before the refusals and matching, run flash.py and put its record in the trace; never rejects. `on`: the same record, and only an outcome `fail` on a glow challenge is refused (LIVENESS_FAIL, after the existing PAD and liveness refusals, before matching); `inconclusive` never refuses. No threshold, model, response body (other than that refusal) or wire-format change. | user, 24 Sep 2026 (in-session instruction: "Implement LIVENESS_UPGRADE_PLAN.md Phase 4 (soft colour glow) — ENGINE PART ONLY this session"; FLASH_CHECK off/log/on, default off; SDK and page NOT touched) — see docs/UX_FIX_LOG.md "Liveness Phase 4"; NOT rebaselined |
| engine/app/trace.py (Phase 4) | One new trace field, `flash`, written only when set (FLASH_CHECK off = today's line byte for byte). | user, 24 Sep 2026 (in-session instruction: "Implement LIVENESS_UPGRADE_PLAN.md Phase 4 (soft colour glow) — ENGINE PART ONLY this session"; FLASH_CHECK off/log/on, default off; SDK and page NOT touched) — see docs/UX_FIX_LOG.md "Liveness Phase 4"; NOT rebaselined |
| engine/app/enrolment.py (Phase 5, new file) | New module: (1) the enrolment photo: among the 3 most frontal single-face settle frames (`aenet.frontal_pool`), the sharpest (`aenet.crop` + Laplacian variance), used only under a single-turn `CHALLENGE_POLICY`; (2) the stricter enrolment bar: the nose check (`PAD_GEOMETRY`) must be conclusive (outcome `scored`) and the glow (`FLASH_CHECK`) must be conclusive (`pass`) or poor light (`inconclusive`/`too_bright`); `inconclusive` is not accepted. A pure record; main.py decides. | user, 24 Sep 2026 (in-session instruction: "Implement the rest of LIVENESS_UPGRADE_PLAN.md: Part A = Phase 4 page ..., then Part B = Phase 5 enrolment polish"; stricter bar enforced only when that check's flag is on, off/log only log, all flags off = today) — see docs/UX_FIX_LOG.md "Liveness Phase 5"; NOT rebaselined |
| engine/app/main.py (Phase 5) | Enrolment only. Under `CHALLENGE_POLICY` single-turn-v1/single-turn-glow-v1 the template is the embedding of the enrolment photo instead of the mean (fallback to the mean when no photo can be embedded). The bar: each part is enforced (LIVENESS_FAIL "liveness check failed: pad_enrol_geometry" / "pad_enrol_flash", after all existing refusals) only when its own flag is `on`; with the flag `log`, or `off` while another check flag is set, the decision is only written to the trace. `_log_geometry` returns its record. All flags off (head-sequence, `PAD_GEOMETRY` off, `FLASH_CHECK` off) = today byte for byte; verify and liveness unchanged. | user, 24 Sep 2026 (in-session instruction: "Implement the rest of LIVENESS_UPGRADE_PLAN.md: Part A = Phase 4 page ..., then Part B = Phase 5 enrolment polish"; stricter bar enforced only when that check's flag is on, off/log only log, all flags off = today) — see docs/UX_FIX_LOG.md "Liveness Phase 5"; NOT rebaselined |
| engine/app/trace.py (Phase 5) | One new trace field, `enrolment`, written only when set (all flags off = today's line byte for byte). | user, 24 Sep 2026 (in-session instruction: "Implement the rest of LIVENESS_UPGRADE_PLAN.md: Part A = Phase 4 page ..., then Part B = Phase 5 enrolment polish"; stricter bar enforced only when that check's flag is on, off/log only log, all flags off = today) — see docs/UX_FIX_LOG.md "Liveness Phase 5"; NOT rebaselined |
| mock-gateway/ (whole directory), docker-compose.demo.yml, Caddyfile.demo, apps/console, apps/integration-demo, their e2e scripts and tests | Deleted outright: the legacy dev proxy, its demo compose stack, and the two pages it exclusively served. Not a frozen-file edit — nothing here is `engine/` or `packages/face-sdk`, and none of it is on the "not changeable" list below. `packages/face-auth` (via `deploy/amfatec`) is the only gateway left; `apps/verify` is unaffected. | user, 25 Sep 2026 (in-session instruction to remove mock-gateway and everything that existed only for it) — applied 25 Sep 2026 on `claude/remove-mock-gateway` |

| packages/face-sdk/src/workflow/session.ts (Phase 0 item 0.2) | Fix only: `runScan` clears the session-level `stopReason`/`stopCode` for the new run immediately after `ctl.start()` synchronously cancels the superseded run, so the superseded run's own `catch` later reads `stopReason` as already cleared and reports `reason: 'cancelled'` instead of `'superseded'` (contradicts the comment at line 63 and the test at `tests/session.test.ts:318-325`). Scope: make the cancelled run capture its own reason at cancellation time (e.g. read it inside `ctl.guard`'s cancel handling, or snapshot it before clearing) instead of relying on the shared mutable `stopReason`/`stopCode` variables being read late. No other behavior, no public API or type change. | owner, 25 Sep 2026 |

**Rows proposed for FIX_PLAN.md Phases 2A, 1, 2B and 4 (25 Sep 2026).** None applied yet. Phases 3 and 5
touch no `engine/app/*` or `packages/face-sdk/src/workflow/*` file. `packages/face-sdk/src/constants.ts`
(new), `src/challenge/guide.ts` and `src/camera/camera.ts` are treated as NOT protected here — they sit
outside `workflow/` and are not one of the three individually-named `src/*.ts` files — flag if that reading
is wrong.

*Phase 2A — shared constants, codes and flags (item 2A.1-2A.4):*

| File | Allowed change | Approved |
|---|---|---|
| engine/app/constants.py (new file) | Add `FRAME_COUNT`, `FRAME_INTERVAL_MS`, `CAPTURE_SPAN_MS`, `MAX_SCAN_BYTES`, `MAX_B64_CHARS`, `MAX_GAP_MS`, `SINGLE_MAX_GAP_MS`, `MATCH_THRESHOLD`, `LIVENESS_THRESHOLD`, `CHALLENGE_EXPIRY_MS`. Values only restate the existing frozen numbers; no threshold changes. | pending |
| engine/app/facescan.py (2A.1) | Replace the literal at line 8 with the import from `constants.py`. No behavior change. | pending |
| engine/app/anti_spoof.py (2A.1) | Replace the `len(frames) != 12` check and the gap-limit literals with the constants import. No behavior change. | pending |
| engine/app/challenge.py (2A.1) | Replace the `EXPIRY_MS` literal with the constants import, kept as an alias so existing importers of `challenge.EXPIRY_MS` keep working. No behavior change. | pending |
| engine/app/main.py (2A.1) | Replace `PLACEHOLDER_THRESHOLD` with the constants import, kept as an alias. No behavior change. | pending |
| engine/tests/test_app_encoder.py (2A.1) | Line 81: replace the literal with the constants import. Golden file and test logic otherwise untouched; `test_app_encoder.py` must still pass unedited in substance. | pending |
| engine/app/flags.py (new file) | Add `choice(name, allowed, default)` and `enabled(name, default)`, one shared "off" vocabulary. Invalid values log once and fall back to the default (not raise, not silently accept a bad value). | pending |
| engine/app/liveness.py (2A.4) | Replace the env-flag parsing at lines 92-96 with a call to `flags.py`. Same log-and-fall-back behavior on an invalid value. No threshold or signal change. | pending |
| engine/app/main.py (2A.4) | Replace the env-flag parsing at line 172 with a call to `flags.py`. Same log-and-fall-back behavior. No response or decision change. | pending |
| engine/app/flash.py (2A.4) | Replace the env-flag parsing at lines 73-75 with a call to `flags.py`. No behavior change. | pending |
| engine/app/geometry.py (2A.4) | Replace the env-flag parsing at lines 31-33 with a call to `flags.py`. No behavior change. | pending |
| engine/app/enrolment.py (2A.4) | Replace the env-flag parsing at lines 57-61 with a call to `flags.py`, and fix the real bug: `enrolment.py` reads `PAD_GEOMETRY` with a different meaning than `geometry.py` does. Give the enrolment use its own flag name (or make both agree), with a test for it. No change to what the flag values already do beyond that name fix. | pending |
| engine/app/aenet.py (2A.4) | Replace the env-flag parsing at lines 63-65 with a call to `flags.py`. No behavior change. | pending |
| engine/app/challenge.py (2A.4) | Replace the env-flag parsing at lines 74-76 with a call to `flags.py`. Separate change from the 2A.1 constants row above. No behavior change. | pending |
| engine/app/head_sequence.py (2A.4) | Replace the env-flag parsing at lines 143-146 with a call to `flags.py`. Separate change from the two existing head_sequence.py rows above. No scoring or threshold change. | pending |
| packages/face-sdk/src/workflow/session.ts (2A.2) | Replace the literal `15400` (line 113) and `1400` (line 129) with imports from the new `src/constants.ts`. No behavior change. Separate change from the item 0.2 row above. | pending |
| packages/face-sdk/src/index.ts (2A.3) | Re-export the new `ERROR_CODES` map and default user text from `src/errors.ts` (new file). Additive export only. | pending |
| packages/face-sdk/src/workflow/session.ts (2A.3) | Update the retry-code sets to read from the shared `ERROR_CODES` map instead of their own copy. No change to which codes are retryable today. Separate change from the other session.ts rows here. | pending |

*Phase 1 — anti-spoof and replay (items 1.1-1.9; "Protected: challenge.py, main.py, liveness.py. Rows are needed from 0.1" per FIX_PLAN.md item 1.1):*

| File | Allowed change | Approved |
|---|---|---|
| engine/app/challenge.py (1.1) | Server-measured timing (D3): reject when the server-measured window (nonce spend time minus issue time, or standalone-engine receipt time) is shorter than the action's minimum real capture time; enforce per-gap and per-span plausibility bounds on client `ts_ms`; bind the head-sequence switch to the server-measured window. Bug fix: `consume_from_scan` takes one snapshot of `_issued[nonce]` under the lock instead of re-reading it after checks (avoids a `KeyError` on an evicted nonce), and `check()` reads `record.get("binding")` inside the lock. No change to `MATCH_THRESHOLD`, `LIVENESS_THRESHOLD`, frame count/interval, or the wire format. | pending |
| engine/app/main.py (1.1) | Record the request receipt time before queueing and pass it into `_secure_analysis`, so nonce/window checks judge against real receipt time, not the time the queued scan happens to be processed. No response or decision-shape change. | pending |
| engine/app/liveness.py (1.1) | `_timing_signal`, `_challenge_signal` and `_settle_split` take the server-measured window instead of trusting client `ts_ms` alone for the pass/fail boundary. No threshold change. | pending |
| engine/app/challenge.py (1.2) | Behind `CHALLENGE_VARIANTS=wide` (default off = today's 6 variants): widen `_draw_params`'s `switch_ms` to 8400-10000 ms at 200 ms steps, `settle_ms` jitter to ±400 ms, `first_sign` to ±1. `target` stays 0.18 (D2). `head_parameters_match` accepts the wider ranges. Separate change from the 1.1 row above. | pending |
| packages/face-sdk/src/workflow/session.ts (1.2) | The HEAD_SEQUENCE guide-cue timing (currently hardcoded fallbacks `switch_ms ?? 9000`, `settle_ms ?? 3200` at lines ~134-136) reads only from the issued challenge's `params`, never a hardcoded fallback, so a widened server-side draw is followed correctly. No other behavior change. Separate change from the 0.2, 2A.2 and 2A.3 session.ts rows above. | pending |
| engine/app/main.py (1.3) | Add `FLASH_CHECK=strict`: an `inconclusive` glow result (no_baseline, too_bright, too_few_frames) under a glow challenge refuses with a retryable `LOW_QUALITY` ("too bright / hold still"). `on`, `log` and `off` keep today's behavior; `no_glow_issued` is never refused. Separate change from the other main.py rows here. | pending |
| engine/app/main.py (1.4) | Set `tracked[i] = None` for every frame PAD marked `invalid_crop` before liveness, `head_sequence` and geometry see the frame list, instead of only `anti_spoof.py` skipping PAD on it while `main.py`'s `tracked` list still counts the frame. Separate change from the other main.py rows here. | pending |
| engine/app/head_sequence.py (1.4, conditional) | Only if `validate_for`/`validate` do not already treat a `None` frame as missing while still enforcing the challenge's minimum usable-frame count: make them do so. No scoring or threshold change. If the existing code already handles `None` correctly, this row is not used. | pending |
| engine/app/store.py (1.5) | `store.enroll` (lines 13-24): if the subject already has templates, the new embedding must reach `MATCH_THRESHOLD` against at least one existing template, or the enroll is refused with a new `ENROLL_MISMATCH` (409, not retryable). The first enrollment for a subject is unchanged. | pending |
| engine/app/main.py (1.6a) | Behind `RESPONSE_DETAIL=minimal` (default after staging): `/v1/verify` and `/v1/liveness` responses, and `_liveness_summary` (line 414), keep `match`/`live` and a coarse `reason` (`no_match`, `not_live`, `retry`) only; per-signal scores and thresholds go to the trace/decision record only, not the response body. `full` keeps today's response shape. | pending |
| engine/app/liveness.py (1.7) | Compute `DEPTH_*`/`MOIRE_*` signals on every scan (both currently unset/inert) and write them to the trace/decision record under the existing `PAD_*` logging style. Neither signal votes on the verdict; no scoring or threshold change; enforcing them is a separately approved future change. | pending |
| packages/face-sdk/src/types.ts (1.8) | Remove the public `env` field from `FaceSessionOptions` (line ~126). Add two narrow public options instead: `transport: { fetch }` and `camera: { stream: MediaStream \| () => Promise<MediaStream> }`. | pending |
| packages/face-sdk/src/workflow/session.ts (1.8) | `resolveEnvironment` (lines 269-296) is driven by the new `transport`/`camera` options instead of the public `env`; a supplied camera stream still goes through the SDK's own frame grabber and readiness checks. Move the fixture-frame injection that `apps/e2e_sdk_session.mjs` uses behind the internal entry (`dist/internal.js`) only. Separate change from the other session.ts rows here. | pending |
| packages/face-sdk/src/index.ts (1.8, conditional) | Only if the public build re-exports anything `env`-related today, or an internal-only entry point needs to be added for the fixture-frame injection moved out of the public API: make that one export change. Additive/internal only, no other public surface change. | pending |
| engine/app/head_sequence.py (1.9) | Rewrite the comment at lines ~105-124 (it claims 0.25/0.15/0.20 are "identical" to the code's 0.30/0.20/0.18). Name the constants `FRONTAL_LIMIT_SWITCH = 0.30`, `STILL_TOLERANCE_SWITCH = 0.20`, `SWITCH_GRACE_FRAMES = 1`. Replace the stamped literal `amfatec-switch-trial-20260922` with a named policy constant `pad-sequence-v2-switch` (old string stays readable in decision records). Separate change from the two existing head_sequence.py rows and the 1.4/2A.4 head_sequence.py rows above. No scoring or threshold change — this documents the values D2 already accepted. | pending |
| engine/tests/test_head_switch_trial.py (1.9) | Pin `FRONTAL_LIMIT_SWITCH`, `STILL_TOLERANCE_SWITCH` and `SWITCH_GRACE_FRAMES`'s values in the test, matching the frozen-list amendment above. | pending |

*Phase 2B — remaining cleanup (item 2):*

| File | Allowed change | Approved |
|---|---|---|
| packages/face-sdk/src/workflow/run.ts (2B.2) | Remove the unused `RUN_CANCELLED` export (confirm with a grep over `apps/` and `packages/` first, per FIX_PLAN.md item 2B.2, that nothing imports it). No other change to `run.ts`. | pending |
| packages/face-sdk/src/types.ts (2B.2) | Remove the unused `CHALLENGE_ACTIONS` export (confirmed unused first). Separate change from the 1.8 types.ts row above. | pending |
| packages/face-sdk/src/index.ts (2B.2) | Remove the re-exports of `OVAL_START`, `CHALLENGE_ACTIONS`, `RUN_CANCELLED` and `FACE_FAR` (each confirmed unused first). Separate change from the 2A.3 and 1.8 index.ts rows above. | pending |

*Phase 4 — UX (item 7, conditional only):*

| File | Allowed change | Approved |
|---|---|---|
| engine/app/head_sequence.py or engine/app/challenge.py (4.7, conditional) | Only if checking the LOOK_LEFT/`first_sign` arrow and prompt against a mirrored preview finds the engine's sign convention itself disagrees with what the user sees (FIX_PLAN.md item 4.7): the smallest fix to make them agree. If the check finds the SDK/page side (`apps/verify/cues.js`) is the mismatch instead, this row is not used — that file is not protected. | pending |

No Phase 3 or Phase 5 item touches a protected file.

Not changeable under this procedure by any milestone: model files and model
hashes, preprocessing, the match threshold 0.55, the heuristic liveness
threshold 0.50, the PAD decision rule, the all-frames-live rule, 12 frames at
1400 ms, and the FaceScan wire format. As of 25 Sep 2026 (D2, switch-trial row
above accepted), the frozen head-turn limits for v2-slow's default `validate()`
path are frontal limit 0.30, stillness tolerance 0.20, target 0.18, plus a
one-frame switch grace — these replace the earlier 0.25/0.15/0.20 values, which
are no longer in force. engine/tests/test_app_encoder.py and its golden file
pass unedited or the change is wrong.

**Who approves.** The user approves a row before the edit and the row records
that approval. The reviewing judge accepts or rejects the result. An agent never
adds or widens a row itself; it proposes the row and stops.

**Recording.** Each accepted change writes a new manifest with
`tools/hardening_verify_baseline.py --rebaseline <name>`, which refuses to
overwrite an existing file. The phase's evidence file lists every path whose
hash moved with its old and new hash, the manifest names either side, and the
row the change rests on.

**No deploy authority.** This procedure unfreezes local source for local work
and isolated staging only. The VPS stays untouched; live capture UX, timing,
policy and thresholds stay as they are; M8 still governs every live change and
is not shortened by anything here.

## M0 — Design and acceptance contract

Deliver docs/hardening/ARCHITECTURE.md and ACCEPTANCE.md before cross-cutting changes.

- [x] Inventory current engine/gateway/SDK APIs, storage, trust boundaries and callers.
- [x] Define identity concepts: authenticated actor, organization/team if applicable,
  test participant, claimed enrollment identity, reviewer and administrator.
- [x] Write a role/action/resource matrix including list, receipt, image, diagnostics,
  enrollment, adding templates, verification, revocation and administrative routes.
- [x] Select an established authentication integration, durable datastore and key
  management approach. Document rationale, deployment cost and failure behavior.
  Do not invent credentials or require a paid provider without a concrete decision.
- [x] Define consent scopes, retention classes and whether any raw-frame backup is
  needed. Prefer minimizing data; do not assume every data type needs a backup.
- [x] Define API decision schema, error taxonomy, versioning and migration strategy.
- [x] Propose and distinguish agreed versus pending availability, peak load, latency,
  recovery-time and acceptable-data-loss targets. Avoid arbitrary readiness claims.
- [x] Document model/asset license and provenance checks needed for intended use.
- [x] Map each known finding in consent-v6/REVIEW.md to a milestone and acceptance test.

Exit: decisions are concrete enough to implement; unanswered business inputs are
listed with their impact. Keep progressing on independent code while seeking only
necessary inputs. Do not silently select a weaker security requirement.

## M1 — Identity and authorization

Primary areas: mock-gateway/app.py, engine/app/auth.py, engine/app/main.py and new
authentication/authorization modules. Freeze browser capture behavior.

- [x] Establish authenticated principals and session/token validation with safe expiry.
- [x] Enforce ownership/role checks server-side on every resource access, including
  receipts, review image endpoints, metadata, template addition and deletion.
- [x] Prevent caller-controlled user IDs or forwarded headers from overriding identity.
- [x] Define how the engine receives and verifies trusted authorization context;
  an internal shared engine key alone must not authorize arbitrary target identities.
- [x] Add attributable access/security audit events without raw frames or embeddings.
- [x] Implement revocation/logout behavior and deny-by-default route registration.
- [x] Provide a staged migration of existing identities without guessing ownership.

Exit tests: unauthenticated, wrong-owner, wrong-role, revoked and expired credentials
fail across the full route matrix; forged context cannot bypass engine boundaries;
authorized workflows succeed. Shared live Basic Auth remains intact until M8.

## M2 — Durable templates and challenge state

Primary areas: engine/app/store.py, challenge.py, main.py; repository interfaces,
schema/migrations and synthetic persistence tests.

- [x] Persist normalized templates with owner, subject, model version, template ID,
  creation/revocation timestamps and explicit capacity rules.
- [x] Replace silent oldest-user eviction with documented, testable capacity errors.
- [x] Preserve existing inference calculations and template comparison semantics.
- [x] Define transaction boundaries for enrollment, template addition and revocation.
- [x] Store challenge state with TTL and atomically consume an identity/operation-bound
  nonce; retries and simultaneous requests must not reuse it across instances.
- [x] Design request idempotency without reauthorizing expired/replayed challenges.
- [x] Handle incompatible model/template versions explicitly; no silent reinterpretation.
- [x] Add forward migration, compatibility and recovery procedures using synthetic data.
- [x] Plan live volatile-template transition: first inspect export/support feasibility
  without reading embeddings. If safe migration is unavailable, schedule explicit
  fresh enrollment in a new round; never restart assuming memory will survive.
  (Code inspection found no template export route; M1_RUNBOOK.md schedules a fresh
  self-enrollment round for the M8 window.)

Exit tests: restart preserves synthetic enrollments; replicas see consistent state;
concurrent revocation/enrollment obey documented semantics; only one replay race can
consume a nonce; capacity and datastore outages fail safely; recovery is demonstrated.

## M3 — Consent and privacy lifecycle

Primary areas: mock-gateway/capture_store.py, evaluation.py, app.py; new consent,
audit and key-management interfaces. Server contract first; live UI remains unchanged.

- [x] Create authenticated consent records: participant, acting operator, scope,
  text version, grant/withdrawal timestamps and recorded authority to act.
- [x] Enforce consent at the server when recording; browser localStorage is a UI
  preference, not proof. Define withdrawal races and cancel new recording accordingly.
- [x] Separate optional evaluation frames from templates required for authentication.
- [x] Specify retention for scans, diagnostics, templates, audit data and backups.
- [x] Add storage encryption with separate key access, rotation and recovery procedures.
- [x] Restrict review access and audit reads/changes without duplicating biometric data.
- [x] Implement idempotent expiry/deletion jobs and observable partial-failure handling.
- [x] Test that recovery cannot resurrect data beyond its retention/deletion policy.
- [x] Keep export disabled. If future export support is in scope, repair file/manifest
  failure handling using synthetic fixtures only; enabling it is a separate decision.

Exit tests: missing/wrong-scope/withdrawn consent blocks recording; legitimate grant
works; unauthorized review fails; expiry and deletion cover linked diagnostics;
key loss/rotation and purge failure have explicit outcomes and alerts. Do not perform
live deletion, relabeling, re-encryption or raw-frame backup in this milestone.

## M4 — Security and abuse resistance

Status: LOCAL_VERIFIED and STAGING_VERIFIED, 20 September 2026.
Evidence: [M4](docs/hardening/evidence/M4.md). 741 tests passed, 0 skipped;
21 of 21 staging checks over real HTTPS; 100 of 100 protected hashes unchanged.
Follow-up the same day (docs/hardening/evidence/M4-FOLLOWUP.md): the per-source
limit's proxy lockout closed, AMFATEC_TRUSTED_PROXY required, scan admission
measured down from 5 to 3; 772 passed / 0 skipped, 23 of 23 plus 6 of 6 staging
checks, 100 of 100 hashes unchanged.

- [x] Enforce streaming request-size limits at gateway and engine before unbounded
  buffering; preserve edge limits as another control.
- [x] Add per-principal and per-source limits, inference admission control, bounded
  queues and consistent retry/deadline behavior.
- [x] Harden chosen session model, cookie attributes, CSRF, CORS and browser headers.
- [~] Keep the engine private; constrain trusted proxies, service identity and egress.
  Trusted-proxy resolution and service-key rotation are implemented and tested; the
  deployment's actual proxy address and network isolation remain M7/M8 inputs.
- [x] Support credential rotation and safe startup when required secrets are missing.
- [~] Pin/scan dependencies and images; review privileges and writable mounts.
  Application dependencies and the base image are pinned; mounts and privileges are
  reviewed. NOT done: vulnerability scan (no database or network here) and digest
  pins for the postgres/keycloak images (needs a registry fetch). Both need approval.
- [x] Redact logs/errors and avoid leaking another subject's existence where relevant.
- [x] Add malformed input, bypass, replay and overload tests in isolated environments.

Exit: no unrestricted expensive inference through unauthenticated requests; bounded
memory under oversized/slow requests; correct ownership under concurrency; credentials
rotate successfully; logs contain no secrets or biometric payloads. Browser camera
origin remains untrusted; record this limitation rather than claiming attestation.

Measured bound: peak application heap while refusing an oversized chunked upload is
about 620 KB, 1.29x the 481,964-byte cap, and flat from 1 MB to 64 MB of client data.
The camera-origin limitation is written down in
[THREAT_REPLAY_INJECTION.md](docs/hardening/THREAT_REPLAY_INJECTION.md): the nonce
tests are not camera attestation and not measured PAD efficacy.

## M5 — Backend maintainability and SDK correctness

Primary areas: engine/app/main.py and helpers; gateway repository boundary;
packages/face-sdk transport and workflow modules; tests-contract.

Participant UX follow-up requested 2026-09-19: see
[guided experience proposal](docs/hardening/GUIDED_EXPERIENCE.md) and its offline
interactive preview. Use a step-by-step self-service journey with clear privacy
choices, one capture instruction at a time and honest result/retry states. This is
design only; integrate with M3/M5 contracts and validate under M6 before M8 rollout.
M2/M3 now provide durable operations, server consent and recording status. M5/M6 own UI integration. Preserve
the frozen live capture sequence, wording and timing during the current test round.

- [ ] Separate validation, authorization, inference, decision and persistence stages.
- [ ] Remove legacy helpers only after checking callers and affected regression tests.
- [ ] Centralize validated configuration; preserve frozen biometric values exactly.
- [ ] Replace access to storage internals with supported repository interfaces.
- [ ] Standardize typed responses, error codes and end-to-end request identifiers.
- [ ] Distinguish transport completion, recording receipt and biometric acceptance.
- [ ] Reject contradictory success responses; required PAD/liveness cannot be unknown,
  missing, failed or not-enforced when reporting authentication acceptance.
- [ ] Require positive match for verification acceptance; enrollment/liveness have
  their own explicit predicates. HTTP 200 and SDK ok alone are insufficient.
- [ ] Define bounded safe retries, cancellation and uncertain server-commit outcomes.
- [ ] Test supported API/SDK version combinations and document migration behavior.

Exit: contract tests reject each contradictory response and preserve valid responses;
duplicate requests do not add unintended templates; cancellation cleans resources.
SDK changes are local/staged, never bundled into the live team page during this round.

## M6 — QA engineering and team evidence integration

Build tests throughout M1–M5, then run the integrated gate here.

- [ ] Create requirement-to-test mapping for critical acceptance criteria.
- [ ] Cover enrollment, verification, consent, ownership, review and revocation end-to-end.
- [ ] Inject network loss, timeout, model error, datastore failure, full disk, expired
  credentials, purge failure and response loss after server commit.
- [ ] Test concurrency, saturation and recovery with synthetic data in isolation.
- [ ] CI builds exact artifacts, checks contracts/types/lint and runs relevant suites.
- [ ] Retain release-linked evidence; block release on failed critical checks.
- [ ] Establish a real-device checklist: Samsung A13 first; permissions, backgrounding,
  orientation, slow networks, repeat attempts and tester changes.
- [ ] Ingest permitted team metadata separately by engine/model/policy version;
  never merge old fast-policy results with the current slow policy.
- [ ] Preserve first attempts/retries, failures/missing evidence and denominators.

Exit: deterministic critical checks pass; full traceability exists from finding to
test; flaky checks are repaired rather than ignored. Real-device checks that require
a new UI/auth flow wait for the coordinated round. Engineering QA completion does
not establish recognition FAR/FRR or attack resistance.

## M7 — Deployment and operations

Primary areas: deploy, Compose definitions, CI and new operational runbooks.

- [ ] Build immutable versioned artifacts; pin dependencies and verify model hashes.
- [ ] Separate staging/production configuration, databases, identities and secrets.
- [ ] Add preflight, readiness, migrations and smoke gates to release automation.
- [ ] Design rollback for application and schema compatibility; do not assume an old
  image can safely use a new database. Prefer compatible staged schema expansion.
- [ ] Monitor latency, queue pressure, model/store/purge failures and receipt failures.
- [ ] Add actionable alerts with ownership and incident instructions.
- [ ] Implement approved backup scope and encrypted restore procedures using synthetic
  state first. Raw evaluation frames may be deliberately excluded.
- [ ] Demonstrate restart, rollback, restore, key rotation and dependency outage drills.
- [ ] Measure supported load against M0 targets; assess single-host failure explicitly.

Exit: a clean environment reproduces the release; recovery meets agreed RTO/RPO;
alerts fire during injected failures; rollback preserves schema/data integrity.
If targets remain unagreed, report measured capability without calling the gate passed.

## M8 — Coordinated live integration and readiness review

Prepare everything reviewable before asking for a deployment window.

- [ ] Present exact release, changed contracts, test evidence, data migration impact,
  enrollment transition, downtime estimate and executable rollback/recovery procedure.
- [ ] Coordinate a team test pause and freeze the outgoing round's metadata summary.
  Do not suspend automatic retention or export frames to preserve the round.
- [ ] Recheck current live versions/state read-only; baseline above may have advanced.
- [ ] Preserve model hashes, thresholds, preprocessing and capture timing across the
  hardening cutover. Any unavoidable behavioral difference is disclosed and versioned.
- [ ] Deploy only in the agreed window; verify authorization, recording receipts,
  consent, health and rollback readiness without contaminating genuine test identities.
- [ ] Confirm team access and begin a clearly identified new infrastructure round.
- [ ] Obtain independent security review and resolve critical/high findings.
- [ ] Rescore each aspect against evidence, marking unmeasured items explicitly.

Exit: agreed operational checks and review pass; outstanding limitations have owners.
Do not award 9/10 if critical authorization, data loss, recovery or biometric gaps
remain. Numeric scores are engineering judgments, not guarantees.

## Deferred work: UX and models

Capture redesign, shorter timing, threshold calibration, model replacement or
training and new attack defenses are outside this implementation track. Record
observations in a backlog; do not fix them on the VPS during team testing.

Head-sequence tuning is no longer deferred. It moved into the adopted biometric
track at B2 on 20 September 2026 and is done there as a new development-only
policy version under the protected-file change procedure, never as an in-place
edit of pad-sequence-v2-slow. That track carries the separately frozen
development and held-out rounds, explicit device/participant/attack coverage and
uncertainty reporting this work requires.

## Next session instructions

1. Read this plan, consent-v6/REVIEW.md and applicable local instructions.
2. Check usage and local changes; do not assume Git or clean files.
3. M0-M4 are complete for the isolated synthetic profile. Read docs/hardening/ARCHITECTURE.md,
   ACCEPTANCE.md, M1_RUNBOOK.md, M3_RUNBOOK.md, THREAT_REPLAY_INJECTION.md and
   evidence/M1.md, M2.md, M3.md, M4.md, M6.md. Schema head is 0004_recovery_gate (both
   0003 and 0004 belong to M3; M4 added no migration). M4 adds streaming body bounds with
   a deadline, per-principal and per-source limits, gateway scan admission, Retry-After on
   every refusal, HSTS, service-key and token-key rotation overlap, and a dependency/image
   disposition. The final suite passed 741 tests with zero skips; lint/format and all 100
   protected hashes passed; 21 of 21 staging checks passed on the owned loopback stack.
   Owned test processes are stopped and four ports are free. Still open from M4: a
   vulnerability scan and digest pins for the postgres/keycloak images, both needing
   network approval; cap_drop for those two services, needing a restart test. The live
   AmFatec preview does NOT yet carry any M4 control. Next: either the approved
   gateway-and-engine release in the M4 evidence, or M5. Preserve self-service-only
   authority, frozen inference/head-turn policy, SDK/UI and the unchanged live
   environment. Synthetic results do not establish biometric efficacy.
4. Progress through ready milestones with focused tests; no live mutation required.
5. Update status and evidence at each handoff. List exact next action and blockers.
6. Before any live work, apply M8; this plan is not blanket authorization to interrupt
   the team or override the biometric-transfer restriction.

Handoff template: milestone/status; changes/files; decisions; commands/results;
remaining risks; live changes (normally none); usage delta; next concrete action.
