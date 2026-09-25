# AmFatec UX release — prepared 23 September 2026

Status: PREPARED ON VPS, NOT LIVE. Waiting for the user's confirmation that team testing can pause for the gateway restart. The question was sent during preparation; no reply has been received.

## Scope

Candidate image `amfatec:ux-20260923`, immutable image ID `sha256:11eb2b3903b5098d80796cd15d569c1cd6400cc1f508184362935d090294a40c`.
Base/rollback image `amfatec:rollback-ux-20260923`, outgoing image `sha256:0da690978a071b16ca591623a4bde904f2bfaaf51c674e60cee322b9c6848d34`.

Nine updated page files: bridge.js, flow.js, index.html, main.js, outcome.js, verify.css, view.js, workspace.html, workspace.js.
Six updated backend modules: contracts.py, oidc.py, operations.py, panel.py, postgres.py, sessions.py.
One newly served shared module: shared/messages.js.

The local operations.py challenge target is 0.18; the outgoing gateway uses 0.20. The candidate includes the local value. All compared engine Python files already match the running engine. No engine restart or schema migration is needed.

Registration setup files (init.sh, registration.sh, realm-hints.sh, user-profile.json) are staged under the release's deployment directory. They have NOT been installed over active scripts or applied to the realm. Applying the profile needs its own verification and exact rollback snapshot; the existing helper hardcodes the old username flag rather than preserving arbitrary prior settings.

## Validation

- Focused page tests: 48 passed.
- OIDC/session, HTTP authorization, HTTP edges, registration and panel tests: 387 passed, 1 skipped.
- Additional isolated local PostgreSQL check passed: refresh token rotation, absolute lifetime unchanged, CSRF preserved, old access token rejected, no redundant refresh, revoked-session rotation refused.
- All 16 runtime files hash-verified inside the candidate image; imports pass as the existing non-root runtime user, without network.
- Candidate and rollback Compose overrides validate.
- Initial candidate failed its import preflight because copying the payload root over / changed directory permissions. Corrected Dockerfile copies each file to its existing destination; rebuilt candidate passes. Failed image was never deployed.
- All four live container IDs/images unchanged after preparation.
- Owned local PostgreSQL test process stopped after checks.
- Codex weekly usage: 57% to 58% (+1 percentage point).

## VPS artifacts

Release directory: `/opt/amfatec/releases/ux-20260923`.
Manifest: `MANIFEST.sha256`; preflight inventory: `containers.before.txt`; original Compose: `compose.before.yml`.
Database backups: `/opt/amfatec/backups/amfatec-ux-20260923.dump` and `keycloak-ux-20260923.dump`, mode 600; both checked with pg_restore -l. No backup contents left the server.
Candidate override: `override.json`; rollback override: `rollback.json`. Both pin the existing engine image independently.
Local preparation evidence: `E:/facetech-vps-snapshots/ux-release-20260923`.

## Remaining deployment

After the user confirms the testing pause, recheck live gateway still matches the preflight image. Refresh database backups if the interval warrants it. Recreate gateway only:

    cd /opt/amfatec
    docker compose -f compose.yml -f releases/ux-20260923/override.json up -d --no-deps gateway

Then verify HTTPS home, all changed assets and shared/messages.js, login/register redirects, /auth/options, signed-out /auth/session, internal engine health, gateway startup/restart status, and unchanged engine/auth/db containers. Record actual live image and source hashes. Keep registration availability unchanged unless separately requested. Real-device biometric checks remain the team's responsibility.

Rollback gateway only:

    cd /opt/amfatec
    docker compose -f compose.yml -f releases/ux-20260923/rollback.json up -d --no-deps gateway

Do not use a plain broad compose up: the base Compose still uses the older mutable current tag. Do not reset accounts, restore databases or remove volumes as routine rollback.
