# Fix plan (from the 2026-09 audit)

Ordered largest to smallest. Each phase is one focused session: fix, run tests, then move on.
Keep the live VPS UX and thresholds stable (HARDENING_PLAN.md); ship behind flags and try on staging first.

Already done: 5-frame off-by-one (liveness.py:242), camera stops on session expiry (flow.js),
stale 'superseded' reason in the SDK (session.ts).

## Phase 1 — Anti-spoof and replay (largest, Opus)
1. Bind every capture to its session: the server issues a nonce and challenge per session, the
   frames and timestamps are signed or HMAC'd with a session key, and the server rejects any
   mismatch, reuse or clock skew. Stop trusting timestamps the client sends.
2. Stop revealing challenge params ahead of time. Send the pose only when it is needed, and pick
   from more variants (direction, order, timing, glow colour) so replaying a recording fails.
3. Glow/flash check fails closed: no_baseline, too_bright and too_few_frames become a retry, not
   a pass (flash.py:245-268, main.py:611-625).
4. invalid_crop frames no longer count toward the challenge (anti_spoof.py:191-196).
5. Enroll must match the existing templates before it appends a new one (store.py:13-24).
6. Hide scores and thresholds from the client (the oracle), and add a per-user and per-IP lockout
   with backoff.
7. Let the depth and moiré signals vote, first in shadow mode, then enforced.
8. Remove frame injection through the public `env` in the SDK (freeze it or make it internal).
9. Review the loosened thresholds against HARDENING_PLAN; keep head_sequence.py:79-80 and its
   comment in agreement.
Tests: replay, a static photo, screen replay and an injected frame are all rejected; a real
user's pass rate doesn't change.

## Phase 2 — Duplicate code and consistency (large, Sonnet)
1. apps/console/capture.js: delete it and use the SDK's capture code (it has drifted).
2. One shared error-code map (it is tripled and disagrees), exported from the SDK.
3. One constants module: LUMA_MIN, SHARP_MIN, MAX_GAP_MS, and the payload limit 350*1024
   (4 places).
4. One env-flag helper in the engine (replaces 6 copies).
5. Delete or archive pad-evidence (49 MB). Exclude it from ruff and pytest.
6. Remove the unused presenter.js. Deduplicate the test fixtures.
7. Replace the hardcoded E:\Factech paths and the VPS IP 31.97.186.120 with config or env settings.

## Phase 3 — Backend and ops (medium, Sonnet)
1. Mock-gateway review API: real per-user auth instead of shared basic auth. Mark it dev-only.
2. Limit self-registration (rate limit and a capacity cap).
3. Prune the 6 tables that grow without limit (a TTL job).
4. Encrypt or drop the plaintext SQLite scans in the mock.
5. Add .hardening-runtime/ and the secrets JSON files to .gitignore and .dockerignore. Rotate
   those secrets.
6. Token expiry mid-scan: refresh or extend the token while a scan is running (sessions.py:186-190).
   Queued nonce expiry (main.py:472).
7. mock-gateway/Dockerfile: build dist/ inside the image.

## Phase 4 — UX flow (medium, Sonnet)
1. Start fallback: show Start if the guide doesn't reach "good" within ~20 s (flow.js, view.js).
2. Cap "You moved" restarts (e.g. 3), then show help text and a Start button.
3. Retrying consent must show consent again (flow.js:94).
4. Replace the blocking confirm() in main.js:326 with an inline dialog.
5. Distinct messages for each camera error (denied, in use, not found, lost).
6. SDK camera.ts: abort getUserMedia on cancel, set playsinline/muted itself, and treat paused
   video as not ready.
7. Confirm the LOOK_LEFT direction against a mirrored preview (cues.js).
8. Same restart cap in the verify app and the integration demo.

## Phase 5 — CI and hygiene (small, Sonnet/Haiku)
1. CI runs verify-*.test.mjs, tests-hardening, tests-panel and the SDK tests.
2. Match the local ruff version to CI (0.14.14).
3. Initialise git so changes can be reviewed and reverted.

Rough cost: Phase 1 ≈ 40%, Phases 2+3 ≈ 40%, Phases 4+5 ≈ 20%.
