# Code audit — Factech / Amfatec

Date: 2026-10-08 · Branch audited: `main` @ `027b1cb` · Scope: every source file in
`engine/app`, `packages/face-auth`, `packages/face-sdk`, `apps/`, `deploy/`,
`.github/`, plus tests (for coverage) and tracked binaries/evidence (for secrets/PII).

Method: line-by-line read of each area, every finding traced from a real request or
deployment path to the failure. Unsubstantiated suspicions were dropped. The
highest-impact findings were re-checked by hand against the code before inclusion.
No code was changed by this audit.

> The originally requested repo (`Codesstellar-Pvt-Ltd/Amfatec`, branch `audit`) was not
> reachable from the audit environment. This report covers `Autonoetic-edge/Factech`,
> which contains the same product and its `deploy/amfatec` stack. If the `audit`
> branch has diverged, re-check line numbers.

---

## 1. Executive summary

The codebase is, overall, **well above average for a prototype**: explicit
deny-by-default routing, a careful OIDC implementation (PKCE, nonce, `at_hash`,
introspection, `__Host-` cookies), sound envelope encryption, atomic single-use
challenge nonces, layered input limits and a fail-closed PAD pipeline. No
authentication bypass, IDOR, CSRF, XSS, SQL injection or committed secret was found.

The real risks are elsewhere:

| # | Area | Issue | Severity |
|---|---|---|---|
| 1 | Privacy | Purge / expiry / logout-outbox workers are **never run** in the deployed stack — withdrawn or revoked biometric templates and raw evaluation frames are never physically deleted | **High** |
| 2 | Liveness | Challenge freshness relies on **client-supplied timestamps**; the head-sequence challenge carries ~1 bit of usable entropy against frame injection | **High** |
| 3 | Deploy | The full `.env` (Keycloak admin password, all data keys, DB superuser) is injected into **both** internet-facing services | **High** |
| 4 | CI/CD | CI is red on every workflow; the production gateway has **no CI at all**; fixing CI would trigger an **unattended prod deploy** that the live-testing freeze forbids | **High** |
| 5 | Data integrity | `OPERATION_INTERRUPTED` fence is rolled back with its transaction, so a stalled worker can still commit an enrollment the user was told failed | Medium |
| 6 | Liveness | `FLASH_CHECK=on` fails open on every `inconclusive` / `error` outcome | Medium |
| 7 | Availability | Several unauthenticated DoS paths (slow-body backchannel, un-rate-limited 404s writing audit rows, login rows never purged) | Medium |

Recommended order of work: §2.1 → §2.3 → §2.4 → §2.2 → Medium items.

---

## 2. High severity

### 2.1 Biometric data is never physically purged in production
- **Where:** `packages/face-auth/src/facetech_auth/lifecycle.py` (`Lifecycle.expire`, `Lifecycle.drain`), `postgres.py:667` (`drain_logout_outbox`). Nothing in the repo calls them; `deploy/amfatec/entry.sh:6-9` only starts `serve` or `admin`; `admin.py` exposes only `migrate`/`account`; the HTTP lifespan starts no background task.
- **Effect:** revoke/withdraw only flip `active=false` and enqueue `purge_jobs`. Template ciphertext, raw evaluation frames and diagnostics stay in PostgreSQL indefinitely; backchannel provider logout is never delivered. Evaluation reads merely *filter* expired rows.
- **Scenario:** participant withdraws consent → UI says deleted → data still on disk months later. This is a consent/GDPR-class problem for a live biometric trial.
- **Compounding issues:**
  - `operations.py:320-326` — revoke selects `active_templates(tenant, subject, now)`, so **templates past `AMFATEC_TEMPLATE_EXPIRES_AT` are never revoked, ledgered or queued for purge**, and LIST hides them. `lifecycle.withdraw` correctly ignores expiry; revoke should too.
  - `evaluation_store.py` has no expiry sweep of its own; after an app-DB restore (`recovery.py:184` excludes captures/recordings) every evaluation record becomes an orphan with no deletion path.
- **Fix:** add an `admin purge` subcommand (or sidecar) on a schedule running `expire` + `drain` + `drain_logout_outbox`; alert on `status()["blocked"]`. Drop the `now` filter from revoke's selection. Add an evaluation-side `DELETE … WHERE expires_at <= now` sweep with tombstones. Add a deployment test proving purge runs.

### 2.2 Head-sequence challenge is not bound to server time or image content
- **Where:** `engine/app/challenge.py:117-123, 305-317`, `head_sequence.py:41-72`, `liveness.py:343-374`.
- **Defect:** phase segmentation, gap/span limits and timing all use `frames[i].ts_ms` from the client. The only server bound is `age + 250 < span` (span may not exceed nonce age) — a one-sided check. `switch_ms` ∈ {9000, 9200, 9400} adds nothing when the attacker restamps frames; only `first_sign` (±1) is real entropy. Nothing ties image content to the nonce.
- **Scenario:** with a virtual camera, an attacker holds two clips (left-then-right, right-then-left), requests a challenge, picks the matching clip, restamps 12 frames to fit, submits. Only MiniFASNet stands between that and acceptance, and it is not designed to detect injected genuine-looking frames.
- **Fix:** multi-step randomised sequences (direction × hold × count); enforce a content-bound cue (the glow schedule, actually enforced — see §3.2); bound server-observed time on both sides (`span ≤ age ≤ span + upload_budget`), or stream frames so the server timestamps arrival. README already states physical attack resistance is unestablished — this is the main reason why.

### 2.3 Every secret reaches both public-facing containers
- **Where:** `deploy/amfatec/compose.yml:6-8` — the shared `x-app` anchor has `env_file: .env`, and `.env` (from `init.sh:12-38`) holds `KEYCLOAK_ADMIN_PASSWORD`, the Postgres superuser password, `AMFATEC_DATA_KEY`, `AMFATEC_EVALUATION_KEY`, `TOKEN_KEY` and the OIDC client secret.
- **Effect:** defeats the per-side key split documented in `serve.py:26-27`. An RCE in either gateway or engine yields every key plus Keycloak master admin.
- **Related:**
  - **One Postgres superuser everywhere:** Keycloak, gateway and engine all use the `amfatec` superuser across three databases (`compose.yml:20-21,52-54`, `init.sh:24-25`).
  - **Internal cert is also a CA, key on both services:** the internal certificate is a self-signed `CA:TRUE` cert (`init.sh:42-50`). Its private key is mounted into both services, and `entry.sh:4` adds it to the trust bundle used for *all* outbound TLS, OIDC included. A leaked key can mint a certificate the containers trust for the sign-in server.
- **Fix:** split `gateway.env` / `engine.env` / `auth.env` (or Docker secrets). Use least-privilege DB roles per service in `initdb.sql`. Use a separate CA and leaf certificate, keep the CA key off-host, and pin the internal CA only for the engine hop.

### 2.4 CI/CD: red everywhere, production path untested, unsafe auto-deploy
- **All workflows failing:**
  - `engine.yml:67` installs `requirements.txt pytest httpx`, not `requirements-dev.txt`. `engine/tests/test_aenet_ensemble.py:11` imports `onnx` at module level, so collection fails.
  - The models action and `download_models.py` never fetch `engine/models/anti_spoof/*` (gitignored), so `engine/Dockerfile:20` (`get_pad()`) and the boot test cannot pass.
  - `ruff format --check` fails on 16 files.
- **No coverage of what production runs:**
  - `tests-hardening`, `tests-panel`, face-sdk tests/`tsc`, and `apps/tests/*.test.mjs` never run in CI. `tests-contract/test_apps_node.py` globs only `*.test.js`.
  - `contract.yml` path filters exclude `packages/**`.
  - `deploy/amfatec/Dockerfile` depends on a `package.sh` that is not in the repo, so the production image is not reproducible from source.
- **Unsafe deploy:** `engine.yml:143-163` SSHes to the VPS on every push to `main`:
  - It contradicts HARDENING_PLAN's live-testing freeze, and is masked only because CI is red.
  - There is no host-key `fingerprint`.
  - The deployed image is a rebuild, not the Trivy-scanned one.
  - `deploy.sh` rolls back only the engine although it recreates all services.
- **Fix:** make deploy `workflow_dispatch` with required reviewers, pin the host key, and deploy the scanned image by digest. Fix the dev-deps install, publish PAD ONNX + manifest as a hash-pinned release asset, and run `ruff format`. Add a face-auth/SDK workflow with Postgres (and optionally Keycloak) service containers. Commit `package.sh`.

---

## 3. Medium severity

### 3.1 `OPERATION_INTERRUPTED` fence never persists
`operations.py:516-529`: a stale `processing` row is set to `failed` plus an audit row, then `raise Denied(...)` *inside the same* `engine.begin()` (`postgres.py:91-98`), so both writes roll back. The client recaptures; the original slow worker later reaches commit, sees `processing`, and inserts a template the user was told failed. The test `test_durable_state.py:926-931` checks only the HTTP response.
**Fix:** commit the transition in its own transaction then raise outside, or make commit check `created_at + PROCESSING_LEASE > now`. Same pattern in `operations.py:567-588`: a challenge echo mismatch rolls back `spend_nonce`, leaving the nonce reusable.

### 3.2 Flash/glow check fails open
`engine/app/main.py:538` refuses only on `outcome == "fail"`. `flash.py:247-268` returns `inconclusive` for `no_baseline`, `too_few_frames` and `too_bright`, and `_log_flash` swallows errors. The baseline comes from the frames before the first glow step (in practice frame 0), and the schedule is sent to the client. An attacker therefore hides the screen in frame 0 or places timestamps so fewer than 3 glow frames remain → inconclusive → accepted. `test_an_error_in_the_check_is_logged_not_decided` locks this in.
**Fix:** in `on` mode accept only `pass`, plus `too_bright` if deliberately allowed. Return a retryable refusal otherwise.

### 3.3 Nonce expiry checked after queueing
`main.py:467-473`: the early check is non-authoritative; the spending check runs in the worker after up to 4 queued scans behind `CapacityLimiter(1)`. With 15–20 s of capture against a 30 s TTL, a legitimate scan can expire in the queue and count as a failed attempt. **Fix:** check expiry against arrival time.

### 3.4 Unauthenticated DoS paths on the gateway
- **Un-rate-limited audit writes:** `http.py:344-356` runs `headers()` and `select()` before `limits.source.enforce`. Every 404/400 (wrong host, unknown path, OPTIONS) writes a durable audit row with no rate limit.
- **Login rows never purged:** `postgres.py:157-178` deletes `logins` rows only on successful consume. Each `/auth/login` inserts a row (holding a PKCE verifier) and an audit row, and makes a live discovery fetch to Keycloak.
- **Slow-body backchannel:** `/auth/backchannel-logout` (`http.py:729`) reads 20 kB pre-auth with the 15 s deadline (`limits.py:29`), against uvicorn `limit_concurrency=20` (`serve.py:196`) and unbuffered Traefik. Two IPs dribbling bytes can starve every user.
- **Unhandled deep JSON:** `oidc.py:173` runs `json.loads` on an attacker JWT header; `RecursionError` (a `RuntimeError`) is not caught there or in `http.py:198`. `["` × 12000 in a logout token gives a raw 500 with traceback, no audit, no security headers. Verified.
- **Fix:** enforce the source limit first (keyed on peer); count pre-route failures in memory; sweep expired logins and cache discovery; set a ~2 s backchannel deadline and restrict that route to the IdP network; catch `RecursionError` plus a hardened catch-all 500.

### 3.5 Access token can expire mid-scan
`sessions.py:178-191` refreshes only after expiry; the engine (`http.py:444-447, 596-601`) then validates the same token after a 2.5–7.5 s queue. With the 300 s token lifetime, roughly 1–3 % of scans lose a 16 s capture to a 401. **Fix:** refresh proactively when `exp - now < 30` and serialise refresh per session.

### 3.6 Open registration can exhaust capacity
When `AMFATEC_SELF_REGISTRATION=true`, every verified IdP subject immediately gets an actor and subject (`sessions.py:116-118`). `registration.sh` enables Keycloak sign-up with no email verification or captcha, and the realm in `init.sh:57-58` has no password policy. At 60/min/IP, one IP reaches the 1000-subject cap in about 35 minutes. **Fix:** `verifyEmail` or invite-only; create subjects lazily at first enrollment; put a password policy in the imported realm.

### 3.7 Model integrity is self-attested
- **No hash check on load:** SCRFD and ArcFace load with only an `is_file()` check (`detect.py:24-29`, `embed.py:21-28`), and the Dockerfile never runs `download_models.py --verify`.
- **PAD hashes vouch for themselves:** the PAD hashes come from a `manifest.json` stored next to the weights (`anti_spoof.py:82-93`, `aenet.py:124`).
- **Scenario:** anyone who can write to the model volume can swap ArcFace for a model under which everyone matches.
- **Fix:** pin every model SHA-256 as code constants and verify at startup.

### 3.8 Retention, indexing and operability
- **No retention:**
  - `operations.result` stores verify scores forever, contrary to the documented 24 h.
  - Inactive sessions keep encrypted provider tokens.
  - `challenges`, `logout_replays` and `provider_revocations` are never pruned.
- **Missing index:** revoke and withdraw run `UPDATE operations WHERE tenant, subject_id, state='processing'` without a covering index. **Fix:** a partial index on `(tenant, subject_id) WHERE state='processing'`.
- **Restart time bomb:** `init.sh:32` writes an absolute `AMFATEC_TEMPLATE_EXPIRES_AT` 30 days out, and `serve.py:138` refuses to start after it. Any engine restart after day 30 becomes a crash loop under `restart: unless-stopped`.
- **Hung inference blocks everything:** `inference.py:136-157` has no deadline, so one hung analysis holds the worker until restart.
- **Backups:** manual, stored on the same VPS, never restore-tested.
- **Container hardening gaps** (`deploy/amfatec/compose.yml`):
  - No healthchecks.
  - No `read_only` filesystem or `pids_limit`.
  - No `cap_drop` on `auth`/`db`.
  - No log rotation.

### 3.9 Browser / SDK
- **Camera stream leaks on racing opens:** `apps/verify/bridge.js:29-42` guards `open()` only with `live()`, not an in-flight promise. A double open orphans a live stream (the camera light stays on) and stops the wrong one.
- **Recheck can create a duplicate scan:** `apps/verify/main.js:307-311` maps 503 `DEPENDENCY_UNAVAILABLE` to `busy`, whereas `outcome.js:130-135` maps the same reply to `uncertain`. The user may start a new scan while the original can still commit and count as an attempt.
- **Session timer trusts the client clock:** `main.js:164-166` compares the server's `expires_at` with `Date.now()` and never re-arms the timer. A fast clock means a sign-in loop, and active users are logged out at about 29.5 minutes. It also overwrites `uncertain` results.
- **Blocking confirm during capture:** the `confirm()` at `main.js:328` freezes SDK timers. A mis-tap followed by "Cancel" submits a gapped scan, which counts as a failed attempt.
- **Vendor file served before its hash check:** `apps/vendor/fetch.sh:45-46` does `mv` before the hash check, so on a mismatch the tampered file is left at the served path, which `panel.py` serves as an immutable `'self'` script. **Fix:** verify `.part` before `mv`, and have `panel.py` verify the hashes at startup.

---

## 4. Low severity

**Engine**
- **NaN scores become perfect matches:** `embed.py:51` clamps `max(-1, min(1, nan))`, which gives `1.0`. `liveness.py:182` `_ramp` behaves the same way. This is latent today because PAD validates embeddings first. Add `isfinite` guards.
- **Silent template planting:** `store.py:17-18` evicts the oldest user at 1000, and re-enrol appends a template that verify then matches against.
- **KeyError race:** `challenge.py:310-311` re-reads `_issued[nonce]` after `check()` under a separate lock acquisition. If the nonce is evicted in between, the result is a `KeyError` and a 500.
- **Scoring oracle and information leaks:**
  - `/v1/verify` returns the exact score and threshold (`main.py:735-736`), which can be used for hill-climbing.
  - Exception class names appear in errors.
  - `/docs` and `/openapi.json` are unauthenticated (`auth.py:43`).
- **JPEG decode cost:** progressive JPEGs are not rejected, there is no decode deadline, and each frame is decoded 4 or more times. Body parsing also runs on the event loop before admission control.

**Gateway**
- **No `auth_time` skew leeway:** `oidc.py:285` gives `auth_time` none, although `iat`/`nbf` get 30 s.
- **Panel loses hardened headers:**
  - The rewritten 302 (`panel.py:205-206`) drops `no-store`, CSP and HSTS.
  - `Panel.subject()` (`panel.py:218`) raises `Unavailable` outside the gateway's handler, giving a bare 500.
- **Duplicate-cookie guard mismatch:** the guard (`http.py:278-284`) doesn't strip keys the way Starlette does, so `__Host-facetech =A; __Host-facetech=B` passes.
- **IdP cancellation gives a raw 401:** `/auth/callback?error=access_denied` (`http.py:669-674`) returns a raw JSON 401 instead of redirecting to the page.
- **AEAD context too narrow:** `encryption.py:97-100` binds `[tenant, kind, template_id]` but not `subject_id` or the model fingerprint. Someone with database write access can re-point a template to a victim.
- **Rotation blocked by one bad row:** a single row that fails `rewrap` aborts every rotation batch (`recovery.py:194-219`).
- **Migration drift:** `recovery_state` exists only in migration `0004`, not in `schema.py`, and it is seeded `enabled=true`.
- **Gateway stricter than engine:** `head_parameters_match` (`operations.py:90-99`) requires exact floats, which is stricter than the engine's single-turn tolerance.

**Frontend / SDK**
- **Cross-origin gateway URL:** `resolveGatewayUrl` (`gateway.ts:19-23`) accepts `/\evil.com` and `/\t/evil.com`, and both resolve cross-origin.
- **Recheck can hang:** the recheck has no timeout (`bridge.js:109-130`).
- **Camera stays on during upload**, which contradicts the page copy. A hide or track `ended` during upload aborts a POST that has already been sent.
- **Wrong message on local failures:** local encode failures are shown as "offline".
- **Detector restart race:** `start()`/`stop()` in `face-detector.js:96-103` can leave the loop running after stop.
- **Sign-out inconsistency:** `workspace.js:31` treats a logout 401 as a failure, while `main.js:336` treats it as success.
- **Accessibility:** WCAG AA contrast failures on 8–10 px text in `verify.css` (`.action-note`, footer).

**Deploy**
- **Keycloak proxy trust:** `--proxy-headers=xforwarded` is set without `--proxy-trusted-addresses` (`compose.yml:45`).
- **No edge security headers:** neither the Caddyfile nor the Traefik routes add security headers or rate limits.
- **Unpinned versions:**
  - `postgres:*`, `caddy:2`, `keycloak:26.7.4` and `amfatec:current` are tags with no digest.
  - pip installs without `--require-hashes`.
  - There is no Dependabot.
- **`.gitignore` gaps:** it doesn't cover `deploy/amfatec/realm.json` (holds the client secret), `accounts/*.txt` (one-time passwords), `certs/` or `backups/`.
- **Script bugs:**
  - `deploy.sh:105` references `${API_DOMAIN}` under `set -u`.
  - `realm-hints.sh:28` contradicts `registration.sh:15`.
  - `kcadm` leaves admin tokens in the container's home directory.
- **Engine tests:** about 35 tests fail rather than skip when models are absent, because they lack the `models` marker.

---

## 5. Code quality / maintainability

- **Dead code:**
  - `main.py`: `REFERENCE_FRAMES`, `inflight_scans`, `_tracked_faces`, `_ranked_frames`, `_embedding_frames`, `_reference_embedding`, `_liveness_of`.
  - Vendor helpers `compute_sim`, `get_scrfd`, `transform` and `trans_points*`; `square_crop` is buggy (`int(h/w)*S`).
  - `KEY_VERSION`; `recovery_state.checkpoint` is written but never read.
  - `/engine-status` is a stub that always returns 503.
- **Misleading flag:** `LIVENESS_ENFORCE` only skips the early refusal; enforcement happens regardless.
- **Duplicated constants:**
  - `LIVENESS_THRESHOLD` in `liveness.py` and `constants.py`.
  - `0.55` hard-coded in `anti_spoof.py:232`.
  - Session limits 28800/1800 repeated three times in `postgres.py`.
  - The auth-route set repeated in `http.py:366`.
- **Stale comments and docstrings:**
  - `head_sequence.py:107-111` claims the limits match the frozen gate (they don't).
  - The trace labels the policy `amfatec-switch-trial-…` rather than `pad-sequence-v2-slow`.
  - `policy.py:5` still says "not yet an enforcement layer".
  - `operations.py:232` describes the audit and decrypt ordering incorrectly.
- **Dead admin guard:** `admin.py:53` uses `administrator_id="amfatec-operator"`, which can never equal a hex actor id. The self-escalation guards at `provision.py:89,113` are therefore dead, and audit attribution is shared rather than per person.
- **Errors hidden as client errors:** the broad `except (ValueError, TypeError, …)` at `http.py:198-204` reports server bugs as a client 400 `MALFORMED_REQUEST`.
- **Fragile coupling:** the enrol response reads its quality score back out of the diagnostic trace (`main.py:687-693`).
- **Windows-only tooling:** `tools/hardening_m4_*.py` hard-code `E:\Factech`, and `hardening_local_stack.py` is Windows-only.
- **Very large planning documents** (HARDENING_PLAN 68 KB, FIX_PLAN 28 KB, plus many evidence files) make the current state hard to find. README links to untracked `pad-evidence/` and `engine/models/anti_spoof/POLICY.md`.

## 6. Data / secrets hygiene

- No secrets in any tracked file or in the history (27 commits).
- `docs/hardening/evidence/BIOMETRIC_METADATA_SNAPSHOT_2026-09-20.csv` and `tests-hardening/fixtures/head_gate_traces.json` are pseudonymised, biometric-derived data. Confirm that participant consent covers keeping them in a git repository.
- The provenance and rights of `docs/hardening/assets/amfatec-portrait.png` are unknown.
- The public VPS IP and hostnames are committed.

## 7. Strengths worth keeping

1. **Routing and authorization:** a deny-by-default route registry with raw-path canonicalisation and per-action query allowlists. Missing and foreign resources get a uniform 404, and ownership is always derived from tenant-filtered lookups, never from request input.
2. **OIDC:**
   - PKCE S256, nonce, `max_age=0`, and state bound to a hashed `__Host-` cookie.
   - RS256 only, with a bounded JWKS refresh.
   - Token introspection tied to the ID token's `sub`/`sid`, with `at_hash` checked.
   - Opaque session cookie stored hashed, with rotation and idle/absolute expiry.
3. **Crypto and durability:**
   - A fresh AES-256-GCM data key per record, random nonces, and versioned, purpose-scoped keyrings.
   - The nonce is spent in one conditional `UPDATE … RETURNING`.
   - No transaction is held during inference, and the commit re-checks generation and consent.
   - Lock order is consistent, and statement and lock timeouts are set.
4. **Engine:**
   - 128-bit single-use nonces bound to `(operation, user_id)`, and the server's own params are used rather than the client's echo.
   - PAD fails closed: every frame must be live, softmax output is validated, and runs have timeouts.
   - Layered input caps stop decompression bombs.
   - The engine key is compared in constant time, and the engine refuses to start without one.
5. **Frontend:**
   - Server text is never rendered as HTML.
   - Strict CSP and `frame-ancestors 'none'`.
   - One Idempotency-Key per scan, with a same-key recheck.
   - The msgpack encoder is correct at every boundary.
6. **Supply chain:** Actions are pinned by SHA, Dockerfiles pin base images by digest and run as non-root, and model and vendor downloads are size- and sha-checked.

## 8. Test-coverage gaps to close alongside the fixes

- **Purge in deployment:** no test shows purge running in the deployed stack.
- **Revoke after expiry:** nothing revokes templates after `AMFATEC_TEMPLATE_EXPIRES_AT`.
- **Interrupted operations:** no test asserts the database row state after `OPERATION_INTERRUPTED`.
- **Challenge echo mismatch:** no test checks that the nonce stays spent.
- **Flash inconclusive in `on` mode:** must refuse (the current test asserts the opposite).
- **Nonce expiry while queued:** not covered.
- **NaN embeddings:** no test runs NaN through `cosine_similarity` or `_ramp`.
- **Deeply nested JWT JSON:** not covered.
- **Rate limiting of 404/400 responses:** not covered.
- **Login-row sweeping:** not covered.
- **Frontend races:** no test covers concurrent `camera.open`, a recheck returning 503, timer clock skew, `resolveGatewayUrl` with `\` or a tab, or a `fetch.sh` hash mismatch.
- **CI:** run `tests-hardening`, `tests-panel`, face-sdk and `apps/tests/*.mjs` in CI.
