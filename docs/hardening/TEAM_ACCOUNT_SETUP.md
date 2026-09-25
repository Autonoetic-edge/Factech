# AmFatec team account testing

Implementation lives in `apps/verify` and `packages/face-auth`. The standalone
`guided-experience.html` remains a design prototype; do not deploy it as the app.
Use [AMFATEC_RELEASE_ROLLBACK.md](AMFATEC_RELEASE_ROLLBACK.md) for the current
preflight, gateway-only cutover, acceptance and rollback procedure.

## User journey

- New tester: Create account → Keycloak username/password registration → consent
  → enable camera → practice → enroll → workspace.
- Returning tester: Sign in → backend checks their saved templates → verification
  → server result → workspace. An unfinished enrollment returns to setup.
- Each tester receives a separate participant and subject, bound to the verified
  provider issuer/subject. No shared password or browser-supplied account owner.
- Sign out before another tester uses the same browser. Different testers can use
  their own devices simultaneously; service capacity still applies.

## Deploy and enable

Use the existing isolated `deploy/amfatec` deployment and release/rollback process.
Build the updated application image, preserving the existing database volumes,
realm, `.env`, encryption keys and certificates. This change needs no schema migration.
Copy the updated compose file and `registration.sh` with the release.

After deploying the updated gateway image, from `/opt/amfatec` run:

```sh
sh registration.sh enable
```

The script updates the **existing** Keycloak realm (startup realm import does not
update an existing realm), applies a minimum 12-character password policy, enables
`AMFATEC_SELF_REGISTRATION=true`, and recreates only the AmFatec gateway if needed.
It does not restart the biometric engine or touch the legacy demo stack.
Run it during the AmFatec preview release window because gateway recreation can
interrupt an in-progress check. Requires the existing Keycloak admin credentials
in the auth container; none are printed by the script.

Registration is open to anyone who can reach this preview URL while enabled;
it does not establish team membership or grant administrator/reviewer access.
Disable registration after the team has joined:

```sh
sh registration.sh disable
```

Existing participants can still sign in and use their saved faces. Registration
is off by default. `/auth/options` tells the UI whether to show Create account.
Password reset by email is not enabled here; it needs configured SMTP and a
separate tested recovery flow. Operator-created accounts via `account.sh` remain
supported.

## Registration form hints (written, not yet applied)

The incident log showed one person refused five times with `email_in_use` because the form
asks for a username, an e-mail, first and last name and a 12-character password. The
UX plan (Part 2, item 9) asks for one identifier and a shorter form. That is realm
configuration, changed with `kcadm` on the running realm:

- `deploy/amfatec/init.sh` now writes `registrationEmailAsUsername:true` into a **new**
  realm's `realm.json`. An existing realm is not touched by startup import.
- `deploy/amfatec/registration.sh enable` now sets `registrationEmailAsUsername=true`
  instead of `false`, so enabling registration no longer undoes the hint.
- `deploy/amfatec/realm-hints.sh` applies the rest to the existing realm, and can show or
  revert it. `user-profile.json` beside it is the realm user profile with first and last
  name made optional and admin-only to edit (Keycloak 26 declarative user profile).

Procedure, from `/opt/amfatec`, during a release window (no gateway restart is involved,
but the form changes for anyone registering at that moment):

```sh
sh realm-hints.sh show     # what the realm has now; keep the output with the release notes
sh realm-hints.sh apply    # saves user-profile.before.json, then applies the hint and the profile
```

Check: open `/auth/register` in a private window. The form asks for an e-mail address and
a password only; the e-mail becomes the account name. Sign in with an existing account
still works with its username. Roll back with `sh realm-hints.sh revert`, which restores
the saved profile and username registration.

Not covered by configuration (theme work, deferred): showing the 12-character rule in
the password field's hint before the first refusal, and a line on the sign-in form saying
that password reset by e-mail is not available on this preview (`resetPasswordAllowed`
stays `false` until SMTP and a tested recovery flow exist). Duplicate e-mails are still
refused; with the e-mail as the account name the refusal now reads as "you already have
an account", which is the truth.

Existing accounts: `registrationEmailAsUsername` only shapes new registrations. Accounts
created before it keep their usernames; Keycloak also accepts the e-mail at sign-in
(`loginWithEmailAllowed` is on by default).

## Session length and the 5-minute access token

The realm issues 5-minute access tokens (`accessTokenLifespan: 300` in `init.sh`). The
gateway (`sessions.py`, from the 22 September 2026 UX fix, step 2) now refreshes an expired
access token with the stored refresh token as long as its own session is inside the
30-minute idle and 8-hour absolute limits, so a person is no longer signed out five minutes
after Keycloak issued the token. `/auth/session` reports the gateway's expiry, and the page
warns 30 s before it.

Alternative for a preview realm that runs an older gateway image (one line, reversible; the
JSON import does not update an existing realm). **Not run by this fix; run it only in a
release window, from `/opt/amfatec`:**

```sh
docker compose exec -T auth sh -c 'KC_CLI_PASSWORD="$KC_BOOTSTRAP_ADMIN_PASSWORD" /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME"'
docker compose exec -T auth /opt/keycloak/bin/kcadm.sh update realms/amfatec -s accessTokenLifespan=1800
```

Revert with `-s accessTokenLifespan=300`. Do both changes together only if the token refresh
is not deployed; with the refresh in place the 5-minute token is the better setting.

Saving a face (enrolment) still needs a sign-in from the last 5 minutes
(`policy.py RECENT_LOGIN_SECONDS`). The sign-in link always sends `max_age=0`
(`oidc.py authorization_url`, pinned by a test), so "Continue to sign in" shows the password
form again and refreshes `auth_time`; a silent SSO redirect cannot satisfy the check. The
page shows that case as "One more sign-in, please", not as a timeout.

## Deployment acceptance

1. On the HTTPS app URL, register two different accounts in separate browser
   profiles/devices. Choose different credentials. Verify consent precedes camera.
2. Complete each enrollment with the team's real devices. Sign out, sign back in,
   and verify each account goes to verification instead of enrollment.
3. Verify a mismatched face does not pass and another participant's subject URLs
   are denied. Do not publish biometric captures or credentials in test reports.
4. Restart the preview gateway and confirm accounts/templates remain available.
5. Close registration; confirm Create account disappears but sign-in still works.

Local validation covers OIDC callback/replay protection, registration disabled by
default, participant-only provisioning, concurrent duplicate registration, separate
subject ownership, disabled-account preservation, and UI flow tests. Local visual
preview uses mocked session responses; it is not a working account server. Real
Keycloak registration forms and real phone enrollment must be checked on the
deployed preview before distributing its URL to the team.

Registration uses Keycloak's supported `prompt=create` flow:
https://www.keycloak.org/docs/latest/server_admin/#registration-or-reset-credentials-requested-by-client
