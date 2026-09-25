# AmFatec preview release and rollback plan

Updated: 20 September 2026. This plan covers the isolated AmFatec preview only.
It does not authorize changes to the older `facetech-demo` deployment.

## Current evidence

- Public read-only check: `https://amfatec.31.97.186.120.sslip.io/` serves the
  AmFatec account entry page and `/auth/options` returns
  `{"registration":true}`.
- The deployment handoff records gateway release `amfatec-20260920-0639` and
  rollback image `amfatec:amfatec-20260920-0035`. This has not yet been
  independently rechecked over SSH in the current session.
- The handoff records that only the gateway was recreated for the registration
  release. The engine, database and Keycloak were left running.
- Current local deterministic checks: 580 Python tests passed and 100
  real-service tests skipped; the deployable UI passed 25/25 tests; the SDK
  passed 214/214 tests. These are local results, not live biometric evidence.
- Still unverified: a real face on a real phone, mismatched-face rejection,
  cross-subject denial with two real accounts, and persistence after a gateway
  restart following real enrollment.

Self-registration is currently open. It is **not** a team invitation system:
anyone who can reach the preview URL can create a participant account while it
is enabled. Keep it open only for an agreed onboarding period, then run
`sh registration.sh disable`. Existing accounts and templates remain usable.

## Required gates before another live cutover

1. Agree a short test pause and name one person who can confirm that no capture
   is in progress. A gateway recreation interrupts an in-progress check.
2. Supply or document the existing SSH host alias (or SSH username and key
   association). Use `BatchMode=yes` and `StrictHostKeyChecking=yes`. Do not
   print `.env`, credentials, tokens, keys or account files.
3. Decide whether registration should be closed before the window. Default:
   close it unless named team members are actively joining.
4. Record, read-only, the running container IDs, image IDs, health/status,
   compose configuration hash, database volume name, free disk space and the
   public `/auth/options` value. Record no secret environment values.
5. Create fresh PostgreSQL dumps for both `amfatec` and `keycloak` under
   `/opt/amfatec/backups`, mode 600, and verify each dump with `pg_restore -l`.
   Keep the documented 20 September dumps; do not overwrite them.
6. Build one immutable `amfatec:<release>` image from reviewed local files,
   generate a SHA-256 manifest, transfer with LF shell scripts, and verify the
   manifest on the VPS before tagging or restarting anything.
7. Do not change models, preprocessing, thresholds, challenge order, 12-frame
   cadence, capture timing or existing database volumes in this release.

## Release procedure

The current compose file gives both gateway and engine the mutable
`amfatec:current` tag. Therefore a broad `docker compose up -d` can recreate the
frozen engine unintentionally. Until the compose file is changed to pin the two
services independently, every UI/auth-only release must use the gateway-only
procedure below.

1. Save the exact outgoing gateway image ID and tag it with a unique rollback
   tag. Save a copy of the active `compose.yml`; never copy or display `.env`.
2. Tag the verified candidate image as `amfatec:current`.
3. Install only reviewed non-secret deployment files. Preserve `.env`,
   `realm.json`, certificates, backups, account files and the `db` volume.
4. Validate the rendered compose configuration without printing its environment
   expansion into the report.
5. Recreate only the gateway:

   ```sh
   cd /opt/amfatec
   docker compose up -d --no-deps gateway
   ```

6. Do not restart or recreate `engine`, `auth` or `db`. Confirm their container
   IDs and image IDs are unchanged from preflight.
7. Check the gateway logs for startup failure without exposing request data or
   secrets. Confirm the HTTPS account page, `/auth/options`, sign-in redirect and
   unauthenticated session response.
8. If onboarding is agreed, enable registration only for that period. State to
   testers before enabling it that the reachable preview is open registration,
   not an invitation list. Otherwise leave or set registration disabled.

## Acceptance after cutover

Use two named team testers in separate browser profiles or devices. Do not put
their credentials, images or biometric payloads in the report.

1. Each account signs in independently. A new account sees consent, camera
   preparation and enrollment. An account with no saved template resumes this
   enrollment path automatically.
2. During preparation, the real mirrored camera preview remains visible and the
   Forward/Left/Right practice controls move only the small head guide. During
   the real challenge those controls are disabled and cues come from the SDK.
3. Each tester enrolls, signs out, signs back in, verifies, receives a
   server-confirmed result and reaches `/workspace`.
4. A mismatched face does not pass. Attempts to use the other participant's
   subject URL are denied.
5. Recreate only the gateway once, outside a capture, and confirm both accounts
   and templates remain available. Do not restart the engine for this check.
6. Disable registration and confirm Create account disappears while existing
   sign-in and verification still work.

Any failed critical check ends the window and triggers rollback. A locally
passing suite or a `200` response alone is not biometric acceptance.

## Gateway rollback

Rollback preserves databases, templates, accounts, certificates and volumes.

1. Disable registration first unless the incident prevents the script from
   running:

   ```sh
   cd /opt/amfatec
   sh registration.sh disable
   ```

2. Retag the recorded outgoing gateway image as `amfatec:current`, restore the
   saved compose file, and recreate only the gateway:

   ```sh
   docker compose up -d --no-deps gateway
   ```

3. Verify the engine, auth and database container IDs did not change. Confirm
   existing sign-in works and `/auth/options` reports registration disabled.
4. Keep newly created participant accounts and templates unless a separately
   reviewed privacy request requires deletion. Rollback is not authorization to
   delete data.

If the whole isolated preview must be withdrawn, remove only its two Traefik
files and run `docker compose down` in `/opt/amfatec`. Never use `-v`; do not
touch the legacy demo project. Restoring database dumps is a separate recovery
operation, not a routine application rollback, and requires its own reviewed
window.

## Next safe action

Obtain read-only SSH inventory using the existing access configuration, reconcile
it with this document, and decide whether to close the currently open
registration period. No deployment or service restart is needed for that audit.
