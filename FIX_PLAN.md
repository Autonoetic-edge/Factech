# Fix plan (rewritten 25 Sep 2026, from the full codebase audit)

This replaces the first FIX_PLAN. It fixes the wrong or stale items in it, adds what the audit
found, and records the owner's decisions. Each phase is sized to run in its own session. Each
item says what is wrong, where, what to do, and how to prove it is done.

## Decisions (made by the owner, 25 Sep 2026)

| # | Question | Decision |
|---|---|---|
| D1 | mock-gateway | **Remove it completely.** A separate session is doing this on branch `claude/remove-mock-gateway`. `packages/face-auth` becomes the only gateway. Every mock-gateway item in the old plan is dropped. |
| D2 | Loosened head-turn limits (frontal 0.30, stillness 0.20, target 0.18, one-frame switch grace) | **Keep them.** Record the approval in HARDENING_PLAN, amend the frozen list, and fix the wrong comment (item 1.9). |
| D3 | Anti-replay depth (old items 1.1 and 1.2) | **Server-side checks only.** No frame signing with a browser key. No mid-capture pose reveal. No wire-protocol change. |
| D4 | `pad-evidence/` | **Archive it.** Keep it on an archive tag or branch, then remove it from the working tree. |
| D5 | Order | **Phase 0 first**, then 2A (shared constants and codes), then 1, the rest of 2, 3, 4 and 5. |

## Ground rules for every session

1. **Branching.** Branch from the latest `main`. Merge `claude/remove-mock-gateway` first if it
   has landed. Use one branch per phase, named `claude/fix-phase-<n>`. Commit in small logical
   steps. Push with `git push -u origin <branch>`. Do not open a PR unless asked.
2. **Protected files.** Most of `engine/app/*`, `packages/face-sdk/src/workflow/*` and some
   tests are protected by HARDENING_PLAN.md, section "Protected-file change procedure". Before
   editing one, the file must have an **approved** row in that table. Item 0.1 adds all the rows
   this plan needs in one go, for the owner to approve once. If a session needs a change that
   has no approved row, it stops and asks.
3. **Flags.** Anything that can change a live verdict or the live UX ships behind a flag. The
   flag defaults to today's behaviour. Turning it on for the VPS is a separate, explicit step.
4. **Two challenge paths, one seam.** face-auth issues challenges itself (`operations.py`, about
   line 266) and spends nonces in Postgres (`spend_nonce`, line 111). It then **seeds** the
   engine's in-memory `challenge._issued` with a server-measured `age_ms` and calls
   `main._secure_analysis` directly (`inference.py`, about lines 180-197). An engine fix inside
   `_secure_analysis` or `consume_from_scan` therefore covers production. An issuing-side fix
   must land in `operations.py` too, or better, `operations.py` should call the engine's draw
   (item 1.2).
5. **Proof.** Every fix gets a test that fails before it and passes after. Run the full suite list
   below before pushing. Record anything skipped.
6. **Commit trailer.** Keep the `Claude-Session` trailer. Never put a model name in code or
   commit text.

### Test commands (the baseline on 25 Sep 2026 is in brackets)

```
cd engine && ../.venv/bin/python -m pytest tests -q        # [478 passed]
.venv/bin/python -m pytest tests-hardening -q               # [738 passed, 100 skipped: need PG/Keycloak]
.venv/bin/python -m pytest tests-contract -q                # [14 passed]
.venv/bin/python -m pytest tests-panel -q                   # [12 passed, 1 env fail: MediaPipe download]
node --test apps/tests/*.test.js apps/tests/*.test.mjs      # [all pass]
cd packages/face-sdk && npm test                            # [213 passed, 1 fail -> item 0.2]
ruff check .                                                # ruff 0.14.14 [150, all but 4 in vendor/pad-evidence]
```

### Environment setup (not committed)

- Python 3.12: `uv venv -p 3.12 .venv`. Install `engine/requirements.txt`, then
  `pytest httpx onnx ruff==0.14.14`. `onnx` is needed by the tests but is missing from the
  requirements; item 0.5 adds it.
- Models: `python engine/download_models.py`. The PAD ONNX model is gitignored. Rebuild it with
  the PAD conversion script in a second venv that has torch from PyPI (download.pytorch.org is
  blocked). Afterwards restore `pad-evidence/parity.json`, or `engine/…/parity.json` once 0.4 has
  run, with `git checkout`.
- SDK: `cd packages/face-sdk && npm ci && npm run build`.
- Delete any `*.egg-info` that `pip install -e` creates. Do not commit it.

---

## Phase 0 — Groundwork (small, do first)

**0.1 Approve the rows.** Add one `pending` row to the HARDENING_PLAN protected-file table for
each protected file this plan changes. Copy the wording from the items below. The owner
approves them once, then the Approved column is filled in. Include:
- **D2 approval.** Change the "switch trial" row from `pending` to accepted and amend the
  "not changeable by any milestone" list to frontal 0.30, stillness 0.20, target 0.18 and
  one-frame switch grace. Also add a correction note to the head_sequence second row: the
  stamped policy literal is `amfatec-switch-trial-20260922`.
- **D1.** A row recording that mock-gateway was removed.

Rebaseline (`tools/hardening_verify_baseline.py`, new manifest `source-sha256.v5.json`) only
at the end of each phase, never in the middle of one.

**0.2 Fix the stale SDK test.** `packages/face-sdk/tests/…session.test.ts:318` still expects
`reason: 'superseded'`, but `session.ts:200/203/215` now gives `'cancelled'`. Decide what
is intended:
- If a second `start()` should report `superseded`, fix `session.ts`. The comment at
  session.ts:63 says it should.
- Otherwise fix the test and the comment.

The old plan listed this as done, but it was only half done. Done when `npm test` is fully
green.

**0.3 Ruff scope.**
- In `ruff.toml`, add `vendor`, `apps/vendor`, `pad-evidence` and
  `docs/hardening/evidence` to `extend-exclude`.
- Remove `mock-gateway` from `src` if the removal branch has not already done it.
- Fix the 4 real errors in `engine/tests/test_head_switch_trial.py`.

Done when `ruff check .` shows 0 errors.

**0.4 Archive pad-evidence (D4).**
- `git tag archive/pad-evidence-2026-09` on the current `main` and push the tag.
- Move anything still used at runtime or in tests out of `pad-evidence/` first. Check
  `grep -rn "pad-evidence" --exclude-dir=pad-evidence .`: the PAD conversion writes
  `parity.json` there, and `deploy/*pad*` scripts and docs refer to it.
- Then `git rm -r pad-evidence`.
- Point the conversion script at a gitignored output path.
- Add `pad-evidence/` to `.gitignore` and `.dockerignore`.

Done when the tree is about 49 MB smaller, all suites are unchanged, and the tag is on the
remote.

**0.5 Pytest hygiene.**
- Add `onnx` to engine test dependencies. Use a `engine/requirements-dev.txt` with pytest,
  httpx, onnx and ruff pinned.
- Give duplicate test basenames distinct names or packages, so one pytest run can collect
  every suite.
- Add `testpaths`/`norecursedirs` (vendor, pad-evidence, node_modules, .venv*) in a root
  `pytest.ini`/`pyproject`.

---

## Phase 2A — Shared constants and codes (before Phase 1, so security fixes land once)

**2A.1 One engine constants module.** Create `engine/app/constants.py` with:
- `FRAME_COUNT = 12`
- `FRAME_INTERVAL_MS = 1400`
- `CAPTURE_SPAN_MS = 15400`
- `MAX_SCAN_BYTES = 350 * 1024` and `MAX_B64_CHARS`
- `MAX_GAP_MS = 3800` and `SINGLE_MAX_GAP_MS = 1400`
- `MATCH_THRESHOLD = 0.55` and `LIVENESS_THRESHOLD = 0.50`
- `CHALLENGE_EXPIRY_MS = 30_000`

Replace the literals in these places:
- `facescan.py:8`
- `anti_spoof.py`: the `len(frames) != 12`, gap limits
- `challenge.py`: `EXPIRY_MS`
- `main.py`: `PLACEHOLDER_THRESHOLD`
- `packages/face-auth/.../http.py:~582`: 350*1024
- `operations.py`: the `30000` literals at the challenge insert
- `engine/tests/test_app_encoder.py:81`

face-auth already imports `app.*`, so import it from there. Keep the old names as aliases where
other code imports them, for example `main.PLACEHOLDER_THRESHOLD`.

**2A.2 One JS constants source.**
- `packages/face-sdk/src/constants.ts` is the source.
- Replace `session.ts:113` (15400) and `session.ts:129` (1400) with constants.
- Move `LUMA_MIN = 55` and `SHARP_MIN = 12` from `apps/verify/framing.js:21,23` into the SDK
  constants and export them. `apps/shared/face-guide.js` currently has them as null; wire it to
  the same values or delete the dead fields.
- Move the camera constraints, currently copied in 4 places, into one exported
  `CAMERA_CONSTRAINTS`.

Add a contract test in `tests-contract` that reads both `constants.ts` and
`engine/app/constants.py` and asserts that the shared values are equal.

**2A.3 One error-code map.**
- Export `ERROR_CODES` (code → {origin, http, retry, stage}) and the default user text from the
  SDK (`src/errors.ts`, re-exported from `index.ts`).
- Replace these copies:
  - `apps/shared/messages.js` ENGINE_TEXT/SDK_TEXT
  - `apps/verify/outcome.js` BY_CODE
  - `apps/verify/view.js` OUTCOMES/CAMERA_NOTICE (codes only; wording can stay page-specific,
    keyed by the shared code)
  - the console STAGE_OF, if the console survives D1
  - the retry sets in `session.ts`

Resolve the current disagreements explicitly and write them in the README table: LOW_QUALITY,
CHALLENGE_FAIL, PAYLOAD_TOO_LARGE, MODEL_UNAVAILABLE, UNAUTHORIZED, ENGINE_UNREACHABLE and
CHALLENGE_INVALID.

`tests-contract/test_error_codes.py` must check the SDK map against the engine and face-auth
emitters. The removal branch reworks its gateway half; build on that.

**2A.4 One env-flag helper.** Create `engine/app/flags.py` with
`choice(name, allowed, default)` and `enabled(name, default)`, plus one shared "off" vocabulary.
It replaces the parsers in:
- `liveness.py:92-96`
- `main.py:172`
- `flash.py:73-75`
- `geometry.py:31-33`
- `enrolment.py:57-61`
- `aenet.py:63-65`
- `challenge.py:74-76`
- `head_sequence.py:143-146`
- `tests/model_guard.py:10`

**Real bug:** `enrolment.py` reads `PAD_GEOMETRY` with different meanings from `geometry.py`.
Give the enrolment use its own flag name, or make both agree, and add a test for it. Invalid
values must log once and fall back to the default. Today some parsers raise and some silently
accept. Pick the log-and-fall-back behaviour and test it.

---

## Phase 1 — Anti-spoof and replay (largest; engine and face-auth)

All of it runs through `_secure_analysis`, so production is covered unless an item says
otherwise.

**1.1 Stop trusting client timestamps (D3: server-side).** Today `_timing_signal`,
`_challenge_signal`, `_settle_split` and `consume_from_scan` all use the client's `ts_ms`. The
only server-side cross-check is `age + 250 < span`, which catches a capture that started
before the nonce was issued. An attacker who replays a recording can claim any `ts_ms`.

Fix:
- **(a) A server-measured window.** The nonce spend time minus its issue time is the upper
  bound on real capture time. In face-auth that is `age_ms` (operations.py, about line 595). In
  the standalone engine it is the request receipt time. Record the receipt time *before*
  queueing and pass it into `_secure_analysis` (this also fixes 3.5). Reject when the
  server-measured window is shorter than the minimum real capture time for the action: for
  HEAD_SEQUENCE, `(FRAME_COUNT-1)*FRAME_INTERVAL_MS` minus a tolerance. For the other policies,
  their span. The error is `CHALLENGE_FAIL`. This makes an upload that arrives "too fast" to
  have been captured live fail.
- **(b) Plausibility bounds on `ts_ms`.** Each gap must be within
  `[FRAME_INTERVAL_MS - jitter, MAX_GAP_MS]`. The span must be `≤ window + skew` (already) and
  `≥ the action's minimum span`. The first frame must be `≥ settle_ms` after the challenge
  was shown.
- **(c) Bind the sequence to server time.** Pass the server-measured window to the head
  sequence. The engine must not accept a switch at the client's `switch_ms` unless it falls
  inside the server window.
- **(d) Bug fix.** `consume_from_scan` re-reads `_issued[nonce]` after checks and can
  `KeyError` if the nonce was evicted. `check()` reads `record.get("binding")` outside the lock.
  Take one snapshot under the lock.

Tests:
- A scan with a valid nonce uploaded 2 s after issue is rejected.
- A scan with rewritten `ts_ms` spacing (all 1400 ms) captured in 5 s is rejected.
- A normal fixture scan still passes.
- A concurrent spend or evict race doesn't raise.
- Add face-auth tests in `tests-hardening` using a fake clock.

Protected: challenge.py, main.py, liveness.py. Rows are needed from 0.1.

**1.2 More challenge variants, one issuer (D3: no protocol change).**
- `operations.py` copies the engine's HEAD_SEQUENCE draw (`settle_ms 3200`, `switch_ms 9000 +
  randbelow(3)*200`, `target 0.18`). Replace it with a call to the engine's
  `challenge._draw_params`, so both paths share one issuer.
- Behind `CHALLENGE_VARIANTS=wide` (default `off` = today's 6 variants), widen the draw:
  - `switch_ms` in 8400-10000 ms at 200 ms steps
  - `settle_ms` jitter ±400 ms
  - `first_sign` ±1

  Keep `target` at 0.18 (D2). That gives about 9 × 5 × 2 = 90 variants. Make sure the SDK and
  cues read the timing from params, not constants. grep `9000`, `3200` and `switch_ms` in
  `apps/` and `packages/face-sdk`.
- `head_parameters_match` in operations.py must accept the wider ranges.

Tests:
- The draw distribution covers the full range.
- A scan built for one variant fails under another.
- face-auth issues through the engine draw.

**1.3 Glow check fails closed (flag).** `flash.check()` returns `inconclusive` for no_frames,
no_baseline, too_bright and too_few_frames (flash.py:245-268). `main.py` (about lines 508/556)
refuses only on `fail`. Add `FLASH_CHECK=strict`: an inconclusive result under a glow challenge
refuses with a retryable code, `LOW_QUALITY`, whose hint is "too bright / hold still". `on`
keeps today's behaviour. `log` and `off` don't change. `no_glow_issued` is never refused. The
first plan's line refs main.py:611-625 are stale.

Tests: each inconclusive reason under `strict` refuses, and under `on` passes.

**1.4 invalid_crop frames must not count.** `anti_spoof.py:191-196` skips PAD on
`invalid_crop` frames. But `main.py` builds `tracked = [d[0] if len(d) == 1 else None …]`
without looking at PAD, so those frames still count toward liveness and the head sequence.
Fix: set `tracked[i] = None` for every frame PAD marked `invalid_crop`, before liveness,
`head_sequence` and geometry. Then check that `head_sequence.validate` treats `None` as
missing and still enforces its minimum usable count.

Test: a scan where the turned frames are invalid_crop fails the challenge.

**1.5 Enroll must match existing templates.**
- **Engine:** `store.enroll` (store.py:13-24) appends blindly.
- **face-auth:** `inference.py` enroll ignores `vectors`, even though the templates are
  passed in.
- **Fix, in both places:** if the subject already has templates, the new embedding must reach
  `MATCH_THRESHOLD` against at least one of them, or the enroll is refused with a new
  `ENROLL_MISMATCH` (409, not retryable). Add it to the code map from 2A.3.
- The first enrolment is unchanged.
- Re-enrolling a *different* face needs the existing admin or recovery path (face-auth
  `recovery.py` / `admin.py`). Document which one.

Tests: a same-person second enroll succeeds; a different-person second enroll is refused and
the template count is unchanged.

**1.6 Remove the score oracle and add lockout.**
- **(a) Scores in responses.** Browser responses currently carry `score`, `threshold`, every
  liveness signal score, and error messages naming the failed signals:
  - engine `/v1/verify` and `/v1/liveness`
  - `_liveness_summary`
  - face-auth `inference.py:206-230` (`quality.score`, `score`, `threshold`,
    `live.as_dict()`)
  - `apps/verify/bridge.js:175`

  Behind `RESPONSE_DETAIL=minimal`, which becomes the default after staging: the response keeps
  `match`/`live` and a coarse `reason` (`no_match`, `not_live`, `retry`), and all numbers go to
  the trace or decision record only. The `apps/verify` result screen shows scores today
  (`outcome.js:34-47`, `flow.js:39` `scores`). Show them only when the flag is `full`
  (staff/test) and hide the rows otherwise. `view.js` `WHY_NOT_LIVE[state.scores.reason]` must
  switch to the coarse reason.
- **(b) Lockout.** face-auth `limits.py` has rate windows (scan 10/min, per actor and per
  source) but no failure lockout. Add a Postgres-backed counter per subject: after
  N consecutive failed verify or liveness results (start at 5), block the subject for an
  exponential backoff (1, 5, 15, 60 min). Return `RATE_LIMITED` with `Retry-After`. Reset it
  on success. Do the same per source IP with the existing `Window` (in memory is fine).
  Record audit events for it.

Tests:
- Responses contain no numeric scores under `minimal`.
- The sixth failure is locked.
- A success resets the counter.
- An admin unlock works.

**1.7 Depth and moiré: shadow only.** `DEPTH_*` and `MOIRE_*` are `None`, so neither votes.
Depth also needs a MOVE_CLOSER scale gain, and HEAD_SEQUENCE never asks for one. No
calibration data exists, so **enforcing is out of scope now**. Do this:
- Compute both signals on every scan.
- Write them to the trace or decision record, with the verdict, under the existing `PAD_*`
  logging style.
- Write `docs/CALIBRATION.md`: the data to collect under TEAM_TEST_PROTOCOL.md (live vs photo
  vs screen, at least 50 each), and the rule for picking FLOOR/LIVE. Enforcing becomes a later,
  separately approved change.

Test: the trace carries both values, and verdicts don't change.

**1.8 Close the SDK's `env` injection hole.** `FaceSessionOptions.env` (`types.ts:126`,
`resolveEnvironment` in `session.ts:269-296`) lets any caller replace the camera frames.
Production `apps/verify/main.js:~254` needs it only for `fetch`, which bridges /v1 to /v2 with
CSRF, and for `getUserMedia`, which is the camera hand-off.
- Replace it with two narrow public options:
  - `transport: { fetch }`
  - `camera: { stream: MediaStream | () => Promise<MediaStream> }`

  A stream supplied that way still goes through the SDK's own frame grabber and readiness
  checks.
- Move the fixture-frame injection that `apps/e2e_sdk_session.mjs` uses behind the internal
  entry (`dist/internal.js`) only.
- Remove `env` from the public types.

This is a public API change, and the session.ts/types.ts/index.ts row is still `pending` in
HARDENING_PLAN; 0.1 must cover it.

Tests:
- The public build has no `env`.
- verify still works through `transport` and `camera`.
- The e2e test uses the internal entry.

**1.9 Record and document the thresholds (D2 = keep).**
- Rewrite the `head_sequence.py` comment at about lines 105-124. It claims 0.25/0.15/0.20 are
  "identical" while the code at 79-80 uses 0.30/0.20, with a 0.18 target from the challenge.
- Name the constants: `FRONTAL_LIMIT_SWITCH = 0.30`, `STILL_TOLERANCE_SWITCH = 0.20`,
  `SWITCH_GRACE_FRAMES = 1`.
- Replace the stamped literal `amfatec-switch-trial-20260922` with a named policy constant,
  `pad-sequence-v2-switch`. Keep the old string readable in the decision records.
- Pin the values in `test_head_switch_trial.py`.

The HARDENING_PLAN part is done in 0.1.

**Phase 1 exit check:**
- A new `tests-hardening/test_replay_attacks.py`, driven through face-auth with a fake clock:
  - an old scan with a fresh nonce is refused
  - a fast upload is refused
  - rewritten timestamps are refused
  - a static photo fixture is refused
  - invalid_crop turned frames are refused
  - a cross-person enroll is refused
- All fixture live scans still pass, so the real-user pass rate doesn't change.

---

## Phase 2B — Remaining cleanup (large, mechanical)

1. **Console.** If the removal branch deleted `apps/console`, drop this item. Otherwise delete
   `apps/console/capture.js` and use the SDK. It captures HEAD_SEQUENCE every 500 ms, so the
   engine rejects every console HEAD_SEQUENCE scan. It also has no readiness check, leaks
   canvases, and silently returns oversize scans.
2. **Delete dead code.**
   - `apps/shared/presenter.js` and `apps/tests/controller.test.js` (its only user)
   - `apps/e2e_console.js`, which is stale
   - unused SDK exports: `OVAL_START` (internal), `CHALLENGE_ACTIONS`, `FACE_FAR`,
     `RUN_CANCELLED`

   Confirm each with grep over `apps/` and `packages/` first. Fix the comment at
   `apps/verify/cues.js:7`, which cites a nonexistent `verify-cues.test.js`.
3. **Dedupe test fixtures.** `scan_from_fixture` is defined in two engine tests; move it into
   `engine/tests/helpers.py`/`conftest.py`. Do the same for any other duplicated fixtures:
   grep for `def .*fixture` and repeated `def _scan`.
4. **Hardcoded paths and hosts.**
   - Replace `E:\Factech` in these files with `Path(__file__).resolve().parents[n]`:
     - `tools/hardening_m4_staging_checks.py:8`
     - `hardening_m4_proxy_staging_check.py:21`
     - `hardening_m4_capacity.py`
     - `hardening_head_gate_fixture.py`
   - Rewrite the README commands to be OS-neutral.
   - The VPS IP `31.97.186.120` appears in `deploy/amfatec/init.sh:7-8` and
     `traefik-amfatec.yml:10,17`. Move it to `PUBLIC_HOST` in `deploy/amfatec/.env.example`.
     Apps and test harnesses (`apps/tests/evaluation-form.test.js`, `page-harness.js`,
     integration-demo if it survives D1) take the origin from config or `location`.
5. **Rebaseline** the protected manifest at the end of the phase.

---

## Phase 3 — Backend and ops (face-auth only after D1)

Dropped by D1: the mock review API auth, the mock SQLite encryption, and the mock Dockerfile.

1. **Self-registration limits.** `registration.py` has no rate limit, only `MAX_SUBJECTS=1000`.
   Add a per-IP `Window` (for example 3/hour) and a global daily cap from config. When the cap
   is hit, return a clear `REGISTRATION_CLOSED`.
   Test: the 4th registration from one IP inside an hour is refused.
2. **TTL prune job.** Nothing prunes any of these:
   - sessions, grants, logout_replays, provider_revocations
   - challenges, operations, audit
   - consent_events, deletion_ledger, deletion_requests, purge_jobs, tombstones
   - expired logins

   Add `facetech_auth.prune`, a callable plus `python -m facetech_auth.prune`, with a retention
   per table in `config.py`. Audit and deletion ledgers keep their legal retention; write the
   values down in the docs. Run it on a schedule: a periodic task in `lifecycle.py`, every 15
   min with jitter, guarded by a Postgres advisory lock so only one replica runs it.
   Test: expired rows go and live rows stay, tested against the PG fixture (opt-in).
3. **Secrets hygiene.**
   - `.dockerignore`: add `.hardening-runtime`, `*.pem`, `*.key`, `*secrets*.json`, `.env*`
     (except `.env.example`).
   - `.gitignore`: add `*-secrets.json`, `*.pem` and `*.key`.

   No secrets are committed today. Rotation is an **owner task on the VPS**: list the steps in
   `deploy/amfatec/ROTATE.md` (engine key, Keycloak client secret, PG password, the data-cipher
   key and its re-wrap path).
4. **Token refresh mid-scan.** `sessions.py:182-191`: on the engine path, an access token that
   expires during a scan re-raises `Denied`, and `_refresh` has no lock, so concurrent requests
   can race the refresh.
   - Refresh once, under a per-session `asyncio.Lock`, and retry the reauthorize.
   - Never refresh twice concurrently.

   Test: a token expiring mid-scan completes, and parallel refreshes happen only once.
5. **Nonce expiry while queued (standalone engine).** `_secure_analysis` spends the nonce inside
   the worker thread, so a scan queued behind up to `MAX_QUEUED_SCANS=4` others can pass the
   30 s expiry. Fixed by 1.1(a) passing the receipt time: judge expiry at receipt, not at spend.
   face-auth is not affected, because it spends at claim time and seeds the record with a fresh
   expiry, but add a test that proves it.
6. **Review the face-auth panel and evaluation routes.** They replace the mock review UI. Make
   sure every `/review`-like or evaluation route requires the admin role, and that evaluation
   frames go through `encryption.py`.

---

## Phase 4 — UX (apps/verify, the SDK; all behind the page's own config where it can change the live flow)

1. **Start fallback.** The Start button shows only when the guide is off (`GUIDE_WAIT_MS` 15000,
   `main.js:79`) and is cleared on the first tick. If the guide hasn't reached "ready" after
   20 s, show Start anyway.
2. **Restart cap.** "You moved" restarts are unlimited in verify. Cap them at 3 per attempt,
   then show the retry screen. Use one shared `MAX_RESTARTS` in the SDK constants; the
   integration demo, if kept, uses 2 at `page.js:10`.
3. **Consent retry.** After a consent failure, retry goes to the ready view and skips consent
   (`flow.js:94/180`). Retry must show consent again.
4. **Replace `confirm()`** at `main.js:324-328` with the page's own dialog. It must be
   accessible: focus trap and Esc.
5. **Distinct camera errors.** `bridge.js` maps only denied vs nocamera. Map `NotAllowedError`,
   `NotFoundError`, `NotReadableError` (in use), `OverconstrainedError`, `SecurityError`
   (insecure context) and `AbortError` to separate messages through the 2A.3 code map.
6. **SDK camera.ts.**
   - Check abort before and after `getUserMedia` (:31), and stop the tracks if aborted after.
   - Set `playsinline` and `muted` on the video (:61-63).
   - A rejected `play()` is not ready (:99).
   - `waitForFrames` must check `readyState >= HAVE_CURRENT_DATA` and `!paused`, not only
     `videoWidth`.
7. **Mirrored preview.** Check that the arrow and prompt for LOOK_LEFT/`first_sign` match what
   the user sees in a mirrored preview (`cues.js:32-34` `ACTION_POSE`), and that the engine sign
   convention agrees. Add the test that `cues.js:7` claims exists.

Test each item with `apps/tests` and `node apps/e2e_verify_browser.mjs`.

---

## Phase 5 — CI

`engine.yml` deploys to the VPS. Keep CI edits away from its deploy job.

1. `contract.yml` runs `apps/tests/*.test.js` only. Add `*.test.mjs`.
2. Add jobs for these:
   - `packages/face-sdk` (`npm ci && npm run typecheck && npm test && npm run build`, plus a
     check that `dist/` matches the build)
   - `tests-hardening` (with Postgres and Keycloak services, so the 100 opt-in tests run too)
   - `tests-panel` (cache the MediaPipe bundle from `apps/vendor/fetch.sh`)
   - `packages/face-auth` unit tests
3. Pin all CI Python deps through the requirements-dev files from 0.5. Use
   `RUFF_VERSION: 0.14.14`, already set.
4. The PAD model is gitignored and the engine tests need it. Check how `engine.yml` "Models
   (restore, fetch, verify)" gets it, and make the other jobs reuse that cached step, not
   reconvert.
5. Path filters must include `packages/**`, `deploy/amfatec/**` and `tests-*/**`.

---

## Items from the old plan and where they went

| Old | New |
|---|---|
| 1.1 HMAC / sign frames | 1.1: server-measured timing. Signing with a key the browser holds protects nothing (D3). |
| 1.2 reveal pose just in time | 1.2: more variants and a single issuer. No protocol change (D3). |
| 1.3-1.6 | 1.3-1.6, with current line refs and face-auth coverage added |
| 1.7 depth/moiré vote | 1.7: shadow and calibration only; enforcing needs data |
| 1.8 env | 1.8: replaced with narrow options, because verify depends on `env` |
| 1.9 thresholds | 1.9 + 0.1 (D2 keep) |
| 2.1 console capture.js | 2B.1 (may disappear with D1) |
| 2.2-2.4 | 2A.3, 2A.2 + 2A.1, 2A.4 |
| 2.5 pad-evidence | 0.4 (D4 archive) |
| 2.6-2.7 | 2B.2-2B.4 |
| 3.1, 3.4, 3.7 mock items | dropped (D1) |
| 3.2, 3.3, 3.5, 3.6 | 3.1, 3.2, 3.3, 3.4 + 3.5 |
| 4.x | 4.1-4.7 |
| 5.x | 5.1-5.5, plus 0.3 and 0.5 |
| "Already done" superseded fix | incomplete: the test is still red, so it is 0.2 |

## Session prompt template

> Repo `autonoetic-edge/factech`. Read `FIX_PLAN.md` and the "Protected-file change procedure"
> in `HARDENING_PLAN.md`. Do **Phase <X>** only. Branch `claude/fix-phase-<x>` from the latest
> `main`, which should include `claude/remove-mock-gateway` if it has merged. Follow the ground
> rules. For each item: write the failing test, fix it, and run the full suite list. Commit per
> item and push. Do not open a PR. If an item needs a protected-file row that is not yet
> approved, stop and ask. Finish with a table showing each item's status and anything skipped
> or deferred.
