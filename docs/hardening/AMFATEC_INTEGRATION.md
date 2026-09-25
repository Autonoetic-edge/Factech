# AmFatec face-check page: real integration (local + staged, NOT deployed)

20 September 2026. Turns design study 04 (`guided-experience.html`) into a working page on
the hardened `/v2` backend. **Status: LOCAL_VERIFIED and staged on the owned loopback stack.
Nothing is on the VPS. No frozen file was edited (100 of 100 baseline hashes match).**

## What was added (all new files)

| Path | Role |
|---|---|
| `apps/verify/index.html`, `verify.css`, `logo.png` | Approved markup and stylesheet, taken from the prototype; prototype controls, sample identity and simulated copy removed. No inline script or style. |
| `apps/verify/flow.js` | The participant flow as a pure state machine. Owns: practice never advances; Start works once; a sent scan is never cancelled or resent; success only from a server reply. |
| `apps/verify/bridge.js` | Adapter around the **unchanged** SDK via its `env.fetch` / `env.getUserMedia` hooks: single camera owner; `/v1` calls re-addressed to `/v2/subjects/{own}/…` with CSRF token and one `Idempotency-Key` per scan; recheck resends the same key and bytes. |
| `apps/verify/cues.js` | Exact-sentence table mapping the SDK's three `action` texts to `forward/left/right`. Pinned to `session.ts` by a test; unknown text maps to nothing. |
| `apps/verify/outcome.js` | Server-authoritative result: success needs HTTP 200, `accepted`, matching `operation`, enforced liveness, PAD `live`, and `match` (verify) or `template_id` (enroll). |
| `apps/verify/view.js`, `head-guide.js`, `main.js` | Rendering, the approved 3D guide (now small, beside the live video), wiring. |
| `apps/verify/workspace.html`, `workspace.js` | Minimal signed-in destination at `/workspace`. |
| `packages/face-auth/src/facetech_auth/panel.py` | ASGI wrapper around the unchanged gateway: allowlisted static files with page CSP + `Permissions-Policy: camera=(self)`; adds `subject_id` to `/auth/session`; sign-in lands on `/`; adds `Origin` to the same-origin challenge GET; drops proxy `X-Forwarded-*`. |
| `packages/face-auth/src/facetech_auth/serve.py` | Deployable entrypoint, all values from environment (names listed in its docstring). |
| `tools/amfatec_stage.py`, `apps/e2e_verify_browser.mjs` | Local staging launcher and real-browser journey. |
| `apps/tests/verify-flow.test.js`, `verify-bridge.test.js`, `tests-panel/` | 24 Node tests (the bridge tests run the real SDK), 11 pytest. |

## Backend gaps found (each is worked around in `panel.py`; the proper fix belongs to M5)

1. `/auth/session` does not tell a browser its own subject id.
2. The sign-in callback redirects to a JSON document, not a page.
3. The challenge GET requires `Origin`, which browsers do not send on same-origin GETs, so it
   could never succeed from a page. `panel.py` supplies it only when `Sec-Fetch-Site: same-origin`.
4. There is no consent **read** route, so the page cannot show the state of the optional
   recording consent. The page therefore never asks for it; recording stays off (the server
   skips with `NO_EVALUATION_CONSENT`). Add the toggle when a read route exists.
5. The gateway waits 10 s for the engine. A slower decision returns `DEPENDENCY_UNAVAILABLE`
   while the engine may still commit; the page treats that as "uncertain" and rechecks with the
   same key instead of scanning again.

## How to stage locally

    .venv-pad\Scripts\python.exe tools\hardening_local_stack.py postgres
    .venv-pad\Scripts\python.exe tools\hardening_local_stack.py keycloak
    .venv-pad\Scripts\python.exe tools\amfatec_stage.py prepare
    .venv-pad\Scripts\python.exe tools\amfatec_stage.py engine      (own terminal)
    .venv-pad\Scripts\python.exe tools\amfatec_stage.py gateway     (own terminal)
    node apps\e2e_verify_browser.mjs bob [--mobile]
    .venv-pad\Scripts\python.exe tools\hardening_local_stack.py stop

Result on 20 Sep 2026: 18 of 18 checks at desktop and at 390x844, Chrome 153, real Keycloak
sign-in, real Postgres, real frozen engine. Chrome's fake camera has no face, so the end state is
the server's `NO_FACE` refusal shown as a retry. **A passing face has not been seen.**

## Not done

- No Dockerfile, compose file, Keycloak realm or Traefik route for this stack exists. M7 is TODO.
- Accounts are created offline by `Provisioner`; there is no sign-up and no admin CLI yet.
- Real face, real phone, Safari/iOS, left/right mirroring on a device: untested.
- Known pre-existing failure, not from this work: `apps/tests/face-guide.test.js` "the guided
  page's HTML holds markup and styles only" (frozen page has `?v=ui-v6`, frozen test does not).

## Deployed preview (20 September 2026) — approved by the user, M4/M7 NOT done

Live: https://amfatec.31.97.186.120.sslip.io  (sign-in server: auth-amfatec.31.97.186.120.sslip.io,
only /realms/amfatec/ and /resources/ are routed; the admin console is not public).
Server folder `/opt/amfatec`, compose project `amfatec` (db, auth, engine, gateway), image
`amfatec:<release>` also tagged `current`. Packaging source: `deploy/amfatec/`. Secrets were
generated on the server by `init.sh` into `/opt/amfatec/.env` (mode 600) and never left it.
Memory caps, swap off: db 256m, auth 800m, engine 1g, gateway 256m (measured ~940 MB total).
The facetech-demo project, its data and its Traefik file were not touched.

Verified live: `e2e_verify_browser.mjs` 18/18 desktop and 18/18 at 390x844 (set
`AMFATEC_E2E_ORIGIN` / `AMFATEC_E2E_PASSWORD`); end state is the server's NO_FACE refusal.
The throwaway `e2e-check` account is disabled. **A real face has still not been seen.**

Invite a person:  `cd /opt/amfatec && ./account.sh <name>`  (one-time password in `accounts/<name>.txt`).
Rollback:  `rm /docker/traefik/dynamic/amfatec.yml /docker/traefik/dynamic/amfatec-internal.crt`
then `cd /opt/amfatec && docker compose down` (data volume kept; never use `-v`).

Known gaps: no rate limits or HSTS (M4); no backups or recovery drill (M7); one DB role for
everything; templates expire 30 days after deploy (`AMFATEC_TEMPLATE_EXPIRES_AT`), and the engine
will refuse to restart after that date until it is moved; internal cert lasts 365 days.

## Release amfatec-20260920-0639 — self-registration (20 September 2026)

Shipped: 5 page files (flow, main, view, workspace, verify.css), 5 face-auth files (http, oidc,
registration, serve, sessions), `compose.yml`, `registration.sh`. No schema migration. Files were
sent with LF endings (local copies are CRLF; a CRLF `registration.sh` will not run) and sha256
matched on both ends. Only the gateway was recreated; engine, db and auth kept running. The engine
container still runs the old image; a plain `docker compose up -d` will recreate it on the new one.

Before: 662 passed / 7 skipped (`tests-hardening --hardening-postgres`), verify page tests 25/25.
Pre-release dumps: `/opt/amfatec/backups/{amfatec,keycloak}-20260920-0637.dump` (mode 600).

Verified live: `/auth/options` = `{"registration":true}`; `/auth/register` reaches Keycloak's
sign-up form; realm has `length(12)` policy and password reset off; a throwaway sign-up returned
to the app signed in (`/auth/session` 200) and was then disabled in Keycloak.
**Unverified:** real face, real phone, mismatched face, cross-subject denial, gateway-restart
persistence after a real enrollment (TEAM_ACCOUNT_SETUP acceptance 1-4).

**Registration is OPEN.** Close it once the team has joined: `cd /opt/amfatec && sh registration.sh disable`.
Rollback: `docker tag amfatec:amfatec-20260920-0035 amfatec:current && cp compose.yml.prev compose.yml`
then `sh registration.sh disable` (or `docker compose up -d --no-deps gateway`).

## Release amfatec-20260921-1003 — full-screen capture, M4 controls, engine (21 September 2026)

Gateway **and** engine release, approved by the user in-session ("B full", nobody testing).
Shipped, 59 files by manifest (`/opt/amfatec/releases/amfatec-20260921-1003/MANIFEST.sha256`,
verified on the VPS; 26 files re-hashed inside the built image):

- Page (5 files: flow, view, main, index.html, verify.css): optional demo with the camera off
  (automatic once for a first-time enrol, "Watch the demo" otherwise, always skippable); camera
  states are a full-screen stage showing only the face, an oval, one bold line, Start and a small
  close button. Cause fixed: the phone preview was a ~300 px landscape strip cropping a portrait feed.
- face-auth M4 (config, contracts, http, inference, panel, serve, sessions + new `limits.py`).
- Engine: `head_sequence.py` / `liveness.py` (`HEAD_GATE_POLICY` switch; **not set on the VPS**, so
  the frozen v2-slow gate still decides), exact pins in `requirements.txt`. Models unchanged
  (7 files, hashes equal, hard-linked). No schema migration.
- `compose.yml`: `no-new-privileges` on db and auth (those two containers were NOT recreated, so
  it applies at their next recreation). `.env`: one line added, `AMFATEC_TRUSTED_PROXY=172.16.18.1`
  (Traefik runs with host networking; that is the `amfatec_default` bridge gateway. If the compose
  network is ever recreated with another subnet the gateway will rate-limit wrongly: re-read it).

Before: tests-hardening 720 passed / 100 skipped (no live stack), engine 371 passed, apps 209 passed.
Dumps: `/opt/amfatec/backups/{amfatec,keycloak}-20260921-1004.dump` (mode 600, `pg_restore -l` ok).
Verified live: home 200, `/auth/options` registration true, `/auth/session` 401 signed out,
`/auth/login` 302, HSTS header present, the 5 page files served match the laptop by sha256,
gateway reaches engine `/health` 200 over internal TLS, 0 restarts, 0 error lines, db and auth
container IDs unchanged. A 33 MB unauthenticated POST was cut by the server (connection reset).
**Unverified:** real face / real phone / iOS Safari / left-right mirroring on the new screens,
a 429 with `Retry-After` from a real account, the updated `apps/e2e_verify_browser.mjs` (not run),
the image digest pins and vulnerability scan from the M4 proposal (skipped, need network approval),
separate image tags for gateway and engine (not done; both follow `amfatec:current`).

`/opt/amfatec/build` is now a symlink to the release's `build`; the old tree is
`build.amfatec-20260920-0639`. The old engine image (8400abce2c76) had already been pruned; the
rollback image was proven to hold the same engine code, models and `pip freeze` as that engine.
Rollback (both services, data untouched):
`cd /opt/amfatec && docker tag amfatec:rollback-amfatec-20260921-1003 amfatec:current && cp compose.yml.prev-amfatec-20260921-1003 compose.yml && docker compose up -d --no-deps engine gateway`
(the old gateway ignores the extra `.env` line; `.env.prev-amfatec-20260921-1003` is the copy).

## Release amfatec-20260921-1130 — slow sign-up fix, account reset (21 September 2026)

Incident: a new user got raw `AUTHENTICATION_REQUIRED` JSON after Create account. Diagnosis from
the data: the gateway login attempt started 10:31:00 and expired 10:36:00 (`sessions.begin`, 300 s;
the `__Host-facetech-login` cookie in frozen `http.py` also lasts 300 s); Keycloak logged five
`email_in_use` refusals and created the user at 10:53:56, so the callback arrived 18 minutes late.
Not caused by release 1003; the 5-minute window has been there since sign-up shipped.

Fix (1 file, `panel.py`; `http.py` untouched): a 401 on `/auth/callback` **with no login cookie in
the request** becomes a 302 to `/auth/login`. The Keycloak session is still live, so the user is
signed in without seeing anything. With the cookie present the 401 is shown as before, so a second
failure cannot loop. Test: `tests-panel` 12 passed (1 new); tests-hardening 720 passed / 100 skipped.
Only the gateway was recreated (image c9c1ab1d1397); the engine still runs ff8f9f596eef (same
engine code; a plain `docker compose up -d` would move it). sha256 of `panel.py` equal on laptop,
VPS build tree and inside the image. Verified live: callback without cookie 302 -> `/auth/login`,
with cookie 401, home 200, registration true, `/auth/register` 302, 0 error lines.
**Unverified:** a real slow sign-up end to end in a browser.
Rollback: `cd /opt/amfatec && docker tag amfatec:amfatec-20260921-1003 amfatec:current && docker compose up -d --no-deps gateway`.

Account reset, asked for by the user ("clean all the email and account created previously"):
dumps first, `/opt/amfatec/backups/{amfatec,keycloak}-20260921-1126.dump` (mode 600, `pg_restore -l`
ok). Then all 5 users of the `amfatec` realm deleted with `kcadm` (2 disabled e2e throwaways, 3
real; none was a service account; `users/count` = 0), and in one transaction all rows of
`face_auth` logout_outbox, sessions, grants, consent_events, consents, operations, challenges,
logins, resources, actors (4 actors, all plain participants). There were 0 templates and 0
recordings, so no face data existed. `face_auth.audit` (175 rows) is immutable by trigger and was
kept. The stale one-time-password file in `/opt/amfatec/accounts` was removed. Registration stays open.

## Release amfatec-20260921-1335 — live face guide, clear result with scores (21 September 2026)

Asked for by the user after six live tries (2 enrol rejects on the head gate, 1 enrol pass,
1 verify match 0.991, 2 verify rejects where PAD called 12/12 frames spoof; decision lines saved
in `E:/facetech-vps-data/2026-09-21/`; this stack stores no frames). Gateway only. Image
`be84ba236897`. Engine, db and auth untouched. Dumps `*-20260921-1335.dump` verified.

**Face guide on the waiting camera screen.** `apps/shared/face-guide.js` and `face-detector.js`
are used byte-for-byte (frozen hashes unchanged), with the pinned MediaPipe detector from
`apps/vendor` (`fetch.sh --verify` OK). New `apps/verify/framing.js` turns the 15 Hz cue into one
steady line: a new line must hold 600 ms, a wobble never takes "Good" away, a real problem does.
Lines: Place your face in the oval / Only one face / Move closer / Move back a little / Centre
your face / Hold still / Good. Tap Start. The oval turns green and Start wakes only on "good"
(`flow.js`: `GUIDE`, `GUIDE_OFF`, `startGuide`). The detector is fetched while the person reads the
Prepare screen. If it cannot load, fails, or is not running 5 s after the camera opens, the page
falls back for that visit to the fixed oval with a working Start. The guide stops before capture.
It sends nothing and decides nothing; the server is still the only judge.

**Cost of the guide, stated plainly.** `panel.py` now serves six more allowlisted files
(`GUIDE_FILES`, from beside the page folder: `/srv/shared`, `/srv/vendor`; 10 MB, the vendor ones
cached 30 days `immutable`) and the page CSP gained `'wasm-unsafe-eval'` in `script-src`. That
permits compiling WebAssembly only; `eval`/inline script stay blocked (the vendor bundle uses
neither — checked). Everything else under `/shared` and `/vendor` is still 404.

**Result screen.** A server refusal (`quality`, `liveness`, `nomatch`) now reads "Not verified." /
"Not saved." with a cross; a pass reads "Verified." / "Face saved." with a tick; anything else is
"No result". The "Camera off" / "Your camera is off." texts are gone from results. Scores shown are
the server's own numbers, already in the /v2 reply (`outcome.js` keeps only finite numbers):
live-face score, face quality, match score with the pass mark. A liveness refusal says which part
refused (anti-spoof model vs head movement, from the reply's `message`), and shows the match as
"Not checked" because matching only runs after liveness.

**Checks.** apps 212 pass (3 new), tests-panel 13 (1 new), tests-hardening 720 pass / 100 skipped
(`test_trusted_proxy.py` rig now lays out the guide files). Headless Chrome, 390x844 and 1366x768,
under the panel's exact CSP: detector loaded and ticked, no CSP or console errors; guide, fallback
and result screens looked at. Live: all 19 files 200 with right types, wasm `application/wasm`,
page files byte-equal to local, unlisted `/shared/messages.js` and `/vendor/fetch.sh` 404,
0 gateway error lines. **Unverified:** the guide with a real face on a real phone (speed of the
10 MB first load on mobile data, iOS Safari), and `apps/e2e_verify_browser.mjs` (updated: asserts
the sleeping Start, then continues through the fallback) was not run — there are no accounts.

**Rollback:** `cd /opt/amfatec && docker tag amfatec:amfatec-20260921-1130 amfatec:current && docker compose up -d --no-deps gateway`

## Local mobile layout follow-up (20 September 2026, not deployed) — REVERTED

**Reverted the same day at the user's request (it damaged the layout).** `verify.css` and
`view.js` were restored from the VPS copies (sha256 match, LF-normalised), and the test
"the mobile live-camera layout is camera-first and keeps the guide compact" was removed with it
(verify UI tests 26 -> 25). The text below is kept as history only.

Phone screenshots from a real team device showed that the camera-ready screen
put a long preparation block before the live preview and that the capture screen
repeated too much guidance. The local UI now makes active camera states
camera-first below 680 px, keeps Start/Stop in the same phone viewport, removes
repeated preparation copy after the camera is on, hides redundant page/progress
chrome while capturing, and reduces the 3D guide without obscuring the real
preview. SDK cues, capture timing, challenge policy and backend behavior are
unchanged.

Verified on the isolated 390x844 fake-camera staging journey: 18/18 browser
checks, 26/26 verify UI tests and 11/11 panel tests passed. The fake camera has
no face, so the server correctly returned `NO_FACE`; this is layout/contract
evidence, not real-device biometric evidence. No VPS files or services changed.
