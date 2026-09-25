# Isolated authentication and HTTP profile

`facetech_auth.http.create_gateway(sessions, ...)` and `create_engine(sessions, ...)`
construct independent ASGI applications using the self-service-only policy. They
do not import or change the legacy services, capture stores, models, UI or deployment.
No environment/secret discovery, startup migration or port binding occurs in the
factories. The normal operation adapter returns 503 until explicitly configured.

The gateway implements Keycloak authorization code + S256 PKCE with Authlib,
joserfc signature checks, browser-bound single-use state and encrypted opaque server
sessions. Exact configured HTTPS endpoints, RS256, bounded JWKS refresh, fresh
introspection and current local account/session checks are mandatory. Supplied
key URLs/embedded keys and provider role claims cannot grant local authority.
Unknown issuer/sub mappings cannot log in.

Cookies are __Host-, Secure, HttpOnly, SameSite=Lax and Path=/ without Domain.
Cookie/CSRF hashes and encrypted provider tokens are stored server-side. Idle and
absolute limits are 30 minutes/eight hours; enroll/add/revoke require a login within
five minutes. Access-token expiry requires login; automatic renewal is not present.

The gateway forwards only registered subject endpoints with a service key, the
actual user access token and a local session reference. The engine independently
checks all three and resolves ownership. Mutations and challenge issuance require
CSRF and exact Origin. Operators/reviewers/admins cannot perform participant actions
for anyone else. Review requires tenant/round metadata grants and separate frame
grants. Unknown methods/paths, docs/static/v1 fallback, identity conflicts and
spoofed forwarded context deny access.

## Durable integration

Inject explicit `Config`, `Provider`, `TokenVault`, `PostgresRepository` and
`Sessions`. PostgreSQL + psycopg is required; there is no SQLite/memory fallback.
Call `migrate(owner_engine)` with a separate authorized migration connection only;
it never runs during service startup. The unique issuer/sub mapping, opaque subject
provisioning and identity transition are described in the runbook below.

`PostgresOperations` provides transactional session/actor/subject/generation/grant/
consent guards, durable bound challenges and audit. It spends nonces before analysis,
rechecks provider state after analysis, then locks and checks local authority in
the mutation/audit transaction. Revocation, role changes and grants use the same
lock ordering. SQL review lists filter scope/expiry before pagination. Receipts
expose storage status only. Evaluation data uses a separate database and key.

`facetech_auth.inference.FrozenAnalyzer(engine_root)` implements the `Analyzer`
port by calling the unchanged engine pipeline (`engine/app/main._secure_analysis`
and the same parser, match and threshold code); it re-implements no rule. Its
template format binds the verified on-disk model bytes, and a mismatching
fingerprint or altered model fails closed. The default analyzer still returns 503;
the synthetic analyzer exists only in test fixtures and behind the explicit
`--analyzer synthetic` service flag. Scan routes require an `Idempotency-Key`
header; operations are durable (`operations` table), nonces are spent by a
conditional single-row update before analysis, capacity is an explicit
`CAPACITY_EXCEEDED`, revocation fails in-flight operations, and
`recovery.enable_restored_reads()` validates an authenticated fresh ledger and
independent digest checkpoint before lifting durable quarantine. Schema revision
is `0004_recovery_gate` (M3); `ready()` refuses any other. M3 supplies consent
recording, envelope keys, purge jobs and the application backup allowlist; see
[M3 runbook](../../docs/hardening/M3_RUNBOOK.md). M5 still owns the final v2/SDK
contract; M7 owns scheduling, production key custody/ACLs, separately durable
ledger checkpoints, backup operations and measured recovery. Mocked model
outputs in tests prove wiring, never biometric efficacy.

Logout commits local revocation plus an outbox before the remote request.
`drain_logout_outbox(sessions, limit=20)` supplies bounded leasing/retry delivery;
host scheduling belongs to M7. Backchannel logout verifies signed claims, replay
tracking and provider-session tombstones. Account/role changes increment epochs.
Offline `Provisioner` creates verified mappings and grants; it cannot enroll or
manage participant templates, import legacy aliases or escalate its own identity.

## Evidence and local checks

See the [route registry](../../docs/hardening/hardened-routes.json),
[M1 evidence](../../docs/hardening/evidence/M1.md), and
[identity/staging runbook](../../docs/hardening/M1_RUNBOOK.md).
M1–M3 are complete for this isolated profile; see [M3 evidence](../../docs/hardening/evidence/M3.md). M4–M8 and live integration remain open.

Dependencies are pinned in pyproject.toml and requirements-local.txt. Supplemental
packages live under `E:\Factech\.hardening-deps`; the existing venv is unchanged.
Authlib emits one unsuppressed HTTPX deprecation warning. Full image/dependency
vulnerability disposition and least-privilege deployment remain M4/M7 work.

```powershell
# Working directory: E:\Factech
# Default: deterministic tests; explicit integration tests are skipped.
.\.venv-pad\Scripts\python.exe -m pytest tests-hardening -q -p no:cacheprovider --basetemp=E:\Factech\.hardening-tmp
# For the full gate, first prepare/start the owned local stack per M1_RUNBOOK.md.
.\.venv-pad\Scripts\python.exe -m pytest tests-hardening -q -p no:cacheprovider --hardening-postgres --hardening-keycloak --basetemp=E:\Factech\.hardening-tmp
```

The recorded full run passed 666 tests, with zero skips. It combines mocked-provider
failure checks with actual PostgreSQL and Keycloak over verified loopback HTTPS,
the frozen engine models, four-process nonce races and a `pg_dump`/`pg_restore` drill.
The opt-in stack uses distinct storage, synthetic credentials and bounded resources.
No live process, original checkout, biometric capture, frozen model or timing value
was changed. The test stack is stopped after verification.
