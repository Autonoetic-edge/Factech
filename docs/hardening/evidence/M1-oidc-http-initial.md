# Historical M1 — initial OIDC and HTTP slice

Preserved earlier checkpoint. Current completion status and new evidence are in
[M1.md](M1.md); the limitations and numbers below describe this historical slice.

Status: **IN_PROGRESS**. Updated 2026-09-19, one agent. The isolated OIDC/session
and HTTP authorization slice is **locally verified**. M1 as a whole is not complete.
No deployment, service startup/restart, migration, capture access or live request.

## Implemented in this continuation

The original M0 policy and its 186-test baseline remain intact. New modules in
`packages/face-auth/src/facetech_auth/` add:

- `config.py`: explicit HTTPS issuer/client/audience/origin/redirect/engine settings;
  no environment or secret-file discovery. Exact Keycloak endpoint matching.
- `oidc.py`: Authlib code exchange with S256 PKCE; browser-bound state and OIDC nonce;
  joserfc RS256 signatures, issuer/audience/authorized-party and timestamp validation;
  bounded signing-key refresh, rejection of injected key URLs/embedded keys, fresh
  introspection on every protected authentication, and signed backchannel logout.
- `sessions.py`: random opaque cookie and session rotation, hash-only cookie lookup,
  versioned AES-256-GCM encrypted provider tokens, current account/epoch checks,
  30-minute idle/eight-hour absolute limits, local-first logout with a repository
  outbox contract and explicit retry entry point. No positive introspection cache.
- `contracts.py`: durable repository and business-operation integration ports, audit
  schema, identity/session/resource types, and a default operation adapter that
  returns typed 503. The only in-memory implementation is a synthetic test double.
- `http.py`: separate hardened ASGI gateway and engine factories. There are 24 exact
  gateway registrations, 11 also available at the engine. No framework docs, generic
  proxy, static fallback, implicit HEAD/OPTIONS or legacy-v1 dispatch. The legacy
  applications are not imported, wrapped or changed. Missing readiness/operation
  integration remains unavailable, never a synthetic production success.

Both services resolve trusted ownership and enforce participant self-service only.
Operators, reviewers and administrators cannot enroll/add/revoke/verify for another
participant. Multiple roles confer no delegation. Review uses current tenant/round
metadata grants and separate frame grants. Other-owner/missing objects return the
same error class. Inputs cannot assign principals, tenants or roles. Route/body/
header scan selectors must agree. Both JSON/base64 and MessagePack are exercised
with nonbiometric maps. No FaceScan timing, challenge TTL, model or policy changed.

The gateway constructs its own service key/access-token/session headers. The engine
independently checks service authentication, the actual audience-bound user token,
local session binding and account/subject state. A shared key alone fails. Cookie
mutations, including GET challenge issuance, require exact Origin plus CSRF. Cookie
attributes are Secure/HttpOnly/SameSite=Lax/Path=/ with no Domain. Authenticated
session responses contain no provider token. Fresh reauthorization is supplied to
operation adapters and repeated before disclosure. Audit failure denies entry to
sensitive operations; records contain structured identifiers/outcomes, no payloads.

## Local validation and observed failures

All commands used PowerShell with working directory `E:\Factech`. Runtime:
Python 3.12.14, pytest 9.1.1, Ruff 0.16.8, HTTPX 0.28.1, Starlette 1.6.0,
MessagePack 1.2.2. Supplemental dependencies were installed only into
`E:\Factech\.hardening-deps`; the existing virtual environment was not upgraded.

```powershell
.\.venv-pad\Scripts\python.exe -m pip --isolated install --disable-pip-version-check --no-cache-dir --index-url https://pypi.org/simple --target E:\Factech\.hardening-deps --report E:\Factech\docs\hardening\evidence\m1-dependency-install.json Authlib==1.8.0
.\.venv-pad\Scripts\python.exe -m pytest tests-hardening -q -p no:cacheprovider --basetemp=E:\Factech\.hardening-tmp --junitxml=E:\Factech\docs\hardening\evidence\m1-local-tests.xml --tb=short
.\.venv-pad\Scripts\python.exe -m ruff check packages/face-auth/src tests-hardening tools/hardening_inventory.py --output-format concise
.\.venv-pad\Scripts\python.exe -m ruff format --check packages/face-auth/src tests-hardening tools/hardening_inventory.py
.\.venv-pad\Scripts\python.exe tools/hardening_inventory.py
```

The installation resolved Authlib 1.8.0, joserfc 1.7.5, cryptography 50.0.1,
cffi 2.1.1 and pycparser 3.0; exact supplemental pins are now in
`packages/face-auth/requirements-local.txt`. Direct runtime pins are in pyproject.
Wheel URLs/hashes and environment are in `m1-dependency-install.json`. This is not
an image lock or completed vulnerability review. Authlib emits one visible warning
that HTTPX is deprecated in favor of HTTPX2; no warning was suppressed.

Observed runs, in order:

1. First combined suite: **434 passed in 21.62s**, one deprecation warning.
   Initial Ruff check found six issues (imports and combined conditional/context
   syntax); initial format check identified seven files. Fixed the new code only.
2. Added registry-wide/malformed-input edge tests: **94 passed, four failed in
   9.21s**. Two failures were one missing-`facescan` KeyError at both boundaries;
   another was malformed discovery returning 400 instead of 503; the fourth was
   missing attributable logout completion audit. Fixed those three defects and
   hardened malformed/redirected token endpoint responses.
3. Focused edge rerun: **98 passed in 7.96s**, same one warning.
4. Final combined suite, with token-endpoint and insecure-config cases:
   **544 passed in 28.83s**, same one warning. Saved `m1-local-tests.xml`.
5. Added the documented registry comparison to the existing registry test, then
   ran only that changed test: **one passed in 0.55s**. No runtime code changed
   after the 544-test run. The total suite remains 544 tests.
6. Ruff passed and **15 Python files** were formatted. The original source inventory
   passed: **19 legacy explicit method/path registrations** still mapped.
7. Executed the read-only SHA-256 comparison from M0 against
   `baseline/source-sha256.json`: **100 files checked, zero changed**.

The focused edge command used `tests-hardening/test_http_edges.py` with the same
`-q -p no:cacheprovider --basetemp=E:\Factech\.hardening-tmp --tb=short` arguments.
The final registry command selected
`tests-hardening/test_http_authorization.py::test_registry_is_unique_and_every_engine_route_exists_at_gateway`.
Lint repairs used `ruff check --fix` then `ruff format` on the same focused paths.
A documentation patch initially used duplicate delete/add targets and was rejected;
it applied no changes and was replaced by a valid update patch.

## Acceptance traceability — partial gates

| Criteria | Tests / concrete evidence | Still unproven |
|---|---|---|
| A01 | `test_oidc_sessions.py`: signed tokens, PKCE exchange, issuer/audience/nonce/state, callback replay, fixation, unknown account, algorithm/key URL rejection and bounded JWKS rotation | Real Keycloak/browser/TLS round trip, durable single-use state |
| A02/A03 | `test_http_authorization.py`: both services/transports, own versus peer/other tenant, no operator/admin/reviewer delegation, forged context, engine-key-only denial | Real template workflow with durable operations |
| A04 | Challenge HTTP route requires owned subject, operation, active session and recent login where applicable; liveness has an owned subject path | Actual bound nonce issuance/consumption and 30-second replay races, M2 |
| A05 | Own receipts, review metadata/diagnostics/frame scopes, grant expiry/round/tenant/revocation and uniform foreign/missing denial | SQL query filtering before pagination, actual receipt shaping, encrypted/expired data access |
| A06 | Both services check local session/account/epoch and fresh provider activity; synthetic revocation during work fails; logout failure queues retry; signed backchannel replay tested | PostgreSQL replicas, atomic commit races and durable queue worker |
| A07 | `test_http_edges.py` checks authentication for every protected registration; route/method/encoding/duplicate-input failures; registry comparison with `hardened-routes.json` | Real edge proxy normalization and staged hosted configuration |
| A08 | Typed audit events, no credentials/scan data, failure denies operation entry; attributable logout completion | Durable append storage, transactional result/audit commit and audit owner |
| A09 | Unknown `(issuer, sub)` denies despite matching name/email; no legacy aliases guessed | Named provisioner, verified identity transition/fresh-enrollment procedure, staging round trip |
| S05/Q01 | Cookie/CSRF/origin/header tests, test XML and source hashes | Hosted CI, dependency scan, browser execution and independent review |

Tests use real Authlib/joserfc/cryptography with HTTPX MockTransport/ASGITransport,
ephemeral signing/encryption keys, synthetic identities and nonbiometric payloads.
The mock provider checks code, PKCE challenge, redirect and client authentication
shape. Its behavior is not evidence of an actual Keycloak deployment. Synthetic
operation results are explicitly `synthetic_authorized`, never biometric verdicts.

Primary references consulted: [Authlib 1.8.0 source](https://github.com/authlib/authlib/tree/v1.8.0),
[joserfc JWT validation](https://jose.authlib.org/en/guide/jwt/),
[OIDC backchannel logout](https://openid.net/specs/openid-connect-backchannel-1_0.html).
Installed client source was inspected for the actual fetch-token/PKCE behavior.

## Remaining gates, rollback and next action

All M1 milestone checkboxes remain unchecked; the complete exit is not satisfied.
Implement PostgreSQL actor/membership/subject/session/login-state/replay/audit/outbox
repositories next, using isolated synthetic storage and migrations only. Repository
methods must translate outages to `Unavailable`, never supply a memory fallback.
Provisioning must create opaque IDs and exact issuer/sub mappings, not infer ownership.
Role changes must increment the account epoch and require a new session.

Then implement operation adapters with atomic session/actor/generation/consent/grant
checks under the same transaction as commit and audit. Query lists by authorized
tenant/round before pagination. Do not connect the legacy inference/store functions
directly: an HTTP recheck alone cannot close a database commit race. M2 owns nonce,
idempotency, capacity and template durability; M3 owns consent/retention/decryption.
No production capture, template, challenge or administrative operation is enabled.

Additional gaps: no automatic access-token renewal (expiry requires login), no
secret-file loader or durable logout worker, no private TLS deployment check, no
provider realm/client registration or reviewer/admin roster, no image build/scan,
no SDK/UI integration or real browser/device execution. No replica, load, restore,
biometric accuracy or physical liveness claim. M1/M6 remain IN_PROGRESS, M8 frozen.

Rollback is local only: after checking for later work, remove the new adapters and
supplemental dependency directory, restore package metadata/conftest/docs as needed,
and retain the unchanged original policy/tests and baseline evidence. There is no
running release or database to roll back. Do not use legacy deploy/rollback scripts.

Live changes: **none**. `E:\Facetech` was not accessed or changed in this continuation.
No SSH, VPS requests, port-8000 interaction, capture reads/downloads, model inference,
service start/restart, deployment, migrations or exporter changes occurred.

Weekly usage: **69% at start, 70% after initial changes/tests, 71% at evidence
checkpoint: +2 percentage points**, account-wide rounded. No deployment checkpoint
applied because no deployment was attempted.
