# M1 identity transition and isolated verification

This runbook applies only to `E:\Factech` and synthetic local staging. It does not
authorize a live migration, service restart, deployment, capture import or access
to `E:\Facetech`. The team continues using its unchanged environment until M8.

## Identity transition

1. The identity administrator verifies an explicit roster of exact OIDC issuer and
   immutable `sub`, tenant, and required roles. A displayed name, email, tester code,
   legacy user ID, forwarded header or submitted scan is never ownership evidence.
   The local fixture identity administrator is `synthetic-staging-provisioner`;
   the real roster and accountable administrator must be supplied for M8.
2. A migration operator uses a separate owner connection with the explicit Alembic
   `migrate(owner_engine)` entry point. ASGI startup never migrates. Revision
   `0001_identity` creates a new application schema; it reads no old capture or
   template store. Provisioning and runtime DB privileges must be separated before
   real data deployment; the isolated fixture uses one synthetic cluster owner.
3. `Provisioner.create_account(issuer, sub, tenant, roles)` creates random actor and
   subject IDs and an attributable audit event in one transaction. The unique
   issuer/sub constraint rejects duplicate mappings. Only participant accounts
   receive subjects; administrative/reviewer/operator roles confer no delegation.
   There is no bulk legacy alias mapping, template import, or enrollment function
   in the provisioner. Do not grant permissions based on provider role claims.
4. Existing volatile templates have no supported safe export route. The chosen
   transition is a clearly identified new round with fresh participant login,
   self-consent, and self-enrollment after M2/M5 implementation and the M8 window.
   Operators, reviewers and administrators cannot submit those actions for them.
   Do not read embeddings or restart the old engine to attempt migration. Legacy
   captures remain in their existing access/retention boundary; no automatic import.
5. Reviewers receive explicit expiring tenant/round grants from an accountable
   provisioner. Metadata and frames are distinct scopes; administrator status alone
   permits neither. `set_authority` increments the actor epoch and revokes sessions;
   role removal/account suspension take effect at the next check and business commit.
   Provisioners may not change their own authority or grant themselves review scope.

## Local runtime and commands

Portable PostgreSQL 17.11-4, Keycloak 26.7.4 and Temurin JDK 21.0.12.1+1 are kept
under `.hardening-runtime`. The exact URLs and hashes are in the evidence directory.
Keycloak/JDK hashes were checked against release metadata. PostgreSQL was obtained
through EDB HTTPS and its received hash pinned; no independent publisher checksum
was available. No system installer or Windows service is used.

All commands run with PowerShell working directory `E:\Factech`. Supplemental
dependencies are pinned in `packages/face-auth/requirements-local.txt` and installed
into `.hardening-deps`, leaving the existing virtual environment unchanged.

```powershell
.\.venv-pad\Scripts\python.exe tools/hardening_fetch_runtime.py
.\.venv-pad\Scripts\python.exe tools/hardening_local_stack.py prepare
.\.venv-pad\Scripts\python.exe tools/hardening_local_stack.py postgres
.\.venv-pad\Scripts\python.exe tools/hardening_local_stack.py keycloak
# Wait for the owned provider to become ready before the explicit integration run.
.\.venv-pad\Scripts\python.exe -m pytest tests-hardening -q -p no:cacheprovider --hardening-postgres --hardening-keycloak --basetemp=E:\Factech\.hardening-tmp --junitxml=E:\Factech\docs\hardening\evidence\m1-completion-tests.xml --tb=short
.\.venv-pad\Scripts\python.exe tools/hardening_local_stack.py stop
```

`prepare` preserves existing state and generates a two-day local test certificate
only on first use. An expired certificate requires a deliberately new synthetic
fixture; do not disable verification. Secrets, realm import and TLS private key
stay under `.hardening-runtime`; never print, attach, commit or reuse them for real
identities. Tests require explicit opt-in flags, a matching ownership marker, fixed
loopback endpoints and synthetic credentials. They never accept a live DSN.

| Component | Isolation and bounds |
|---|---|
| PostgreSQL | 127.0.0.1:15432; unique data directory; 40 connections, 64 MiB shared buffers |
| Application/evaluation/identity stores | Separate `facetech_m1`, `facetech_eval`, and provider database; no legacy mounts |
| Keycloak | HTTPS 127.0.0.1:18443, HTTP disabled; 512 MiB Java heap, two JVM processors, five DB connections |
| Gateway / engine fixtures | HTTPS 127.0.0.1:18444 / 18445; concurrency limit 20; access logging off, proxy trust off |
| Application transactions | Bounded pool, 3-second lock timeout, 8-second statement timeout; no inference/network calls in transactions |

The provider DB for the recorded corrected fixture is `facetech_idp_audience`.
A fresh run defaults to `facetech_idp`. Both are local synthetic stores. The initial
realm was corrected by creating a new synthetic provider database; no live database
was altered. The fixture uses real authorization-code/PKCE login through Keycloak's
HTML form, not a password/direct grant. Access tokens must include both the engine
audience and BFF introspection audience. Direct grants and service accounts are off.

The HTTPS fixture launches two actual ASGI processes. Since M2 the engine process
runs `FrozenAnalyzer` (the unchanged engine pipeline and models) by default;
`--analyzer synthetic` selects the test-only analyzer that returns
`synthetic_test_only`. Neither is a production inference service. Normal
`PostgresOperations` has no analyzer configured and returns 503. Nonbiometric bytes
are rejected by the frozen parser after authorization; M1/M2 evidence must never
be presented as PAD or recognition evidence. The M2 durable checks additionally use
`tests-hardening/durable_probe.py` (separate-process reads and nonce claims) and the
portable `pg_dump`/`pg_restore` for the restore drill; both touch only the owned
cluster and the temporary `facetech_m2_migrate` / `facetech_m2_restore` databases,
which the tests drop afterwards.

## Revocation, failure and rollback

Logout revokes the local session and commits an outbox item before contacting the
provider. `drain_logout_outbox(sessions, limit=20)` leases a bounded batch, delivers
logout, removes successes and backs off failures. A crash leaves leases reclaimable
after 30 seconds; local authorization stays revoked. A host scheduler for this
entry point belongs to M7. There is no positive provider-introspection cache.
Signed backchannel logout uses durable replay tracking, advisory identity locks
and tombstones so a racing callback cannot create a surviving session.

Transaction guards lock actor, session and resource; authority changes use the same
lock order. They check active state, epoch, token binding, generation, expiry,
current role/grants and consent before committing. Audit failure rolls back the
sensitive result. Nonces are spent before analysis and cannot be reused on failure.
Provider/store/key failures deny access; no shared-auth or memory fallback exists.

Stopping the owned fixture uses saved PIDs plus executable/command checks and the
verified local PostgreSQL data directory. It does not invoke Windows service
commands. Keep synthetic evidence; do not run legacy rollback scripts. Migration
downgrade intentionally refuses destructive schema deletion. A future incompatible
application must stay isolated from the new schema; M7 owns tested versioned
recovery. Existing live state needs no rollback because it has not changed.

Real data deployment still needs M2-M8: inference integration/idempotency/recovery,
envelope key management and purge, least-privilege credentials and dependency scan,
resource controls, SDK/UI/device checks, production roster, and independent review.
