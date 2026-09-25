# Hardening acceptance contract

Date: 2026-09-19. Implements M0 design in `ARCHITECTURE.md`. Test IDs below are
requirements, **not claims of passing tests**. Evidence must link exact commands,
artifact versions, environment, observed result and unresolved limitations.

## Gate rules

- M0 can complete with concrete local design and explicitly owned/pending business
  inputs. M1-M7 need their full exit evidence before milestone completion.
- LOCAL_VERIFIED means the specified local checks passed, not live integration.
  Stubs/in-process tests do not prove OIDC, replica consistency or physical efficacy.
- STAGING_VERIFIED needs isolated ports/storage/credentials, resource caps and exact
  release identity. No inherited `.env`, live database DSN, capture mounts or canonical
  live browser redirect. Use synthetic nonbiometric payloads only.
- M8 waits for the coordinated test pause, reviewed release and independent review.
  If SLOs remain unagreed, report measured capability and leave their gate open.
- A failing critical test blocks release. Never suppress a failure, substitute a
  weaker test or turn a planned check into a pass. No numerical readiness score yet.

## Source findings mapped to acceptance

This includes the eight priority findings and additional limitations in
`pad-evidence/consent-v6/REVIEW.md`; identifiers are local to this contract.

| Finding | Milestones | Acceptance IDs / outcome |
|---|---|---|
| F01 Shared evaluation auth, arbitrary identities, shared review | M1, M4, M8 | A01-A09: independent actor, self ownership, review grants, audit, route default-deny |
| F02 Uncalibrated recognition/limited physical evidence | M6, M8; separate biometric track | Q04-Q06: held-out people/devices/conditions, denominators/uncertainty, no threshold tuning here |
| F03 Untrusted camera origin/injection | M4, M6, M8 | S06, Q05: explicit threat limitation and physical/injection evidence; no attestation claim |
| F04 Volatile enrollment/nonces, eviction, replica divergence | M2, M7 | D01-D08, O03-O05: durable state, capacity errors, race and restore checks |
| F05 Gateway buffers body, absent principal rate limit | M4 | S01-S04: streaming bounds, quotas, admission, fail-closed dependencies |
| F06 SDK success can mean unknown/not-enforced liveness or no match | M5 | C01-C05: acceptance predicates, version matrix, retry/cancel/commit outcomes |
| F07 Raw SQLite, client consent, shared readers, no encryption/backup proof | M1, M3, M7 | A05, P01-P08, O03: authenticated consent, encryption, purge, minimal backup scope |
| F08 Head-position rejection / UX unmeasured | M6; deferred UX track | Q04, Q06: first attempt/retries counted; no change to head gate from this sample |
| F09 Legacy helpers, store internals, scattered configuration | M5 | C06: supported repositories, caller inventory, frozen constants/parity |
| F10 Single-host risk and limited rollback/recovery | M7, M8 | O01-O06: reproducible immutable release, compatible schema rollback, drills |
| F11 Model conversion parity does not establish efficacy; asset permissions pending | M4, M7, M8 | S07, O01, Q05: hashes/notices/permissions recorded, physical evidence separate |
| F12 Export file/manifest failure paths (plan follow-up) | M3 | P08: synthetic failure atomicity if repaired; exporter remains disabled |
| F13 Remembered consent Samsung confirmation pending | M6, M8 | Q04: real Samsung A13 verification, browser simulations do not satisfy it |
| F14 One tester, zero current-policy physical attacks, old fast results incompatible | M6, M8 | Q05-Q06: stratified round/version linkage; unmeasured gates stay open |

## M0 — architecture contract

| ID | Check | Required evidence |
|---|---|---|
| B01 | Inventory all explicit routes, wildcard forwarding, framework docs, static mounts, callers and state | Static source inventory; no app import opening capture storage |
| B02 | Self-service-only role/action/resource matrix; no admin/reviewer inheritance of participant authority | Architecture and prospective policy cases |
| B03 | Concrete auth, datastore, keys, consent, API, recovery and migration contracts | Architecture with rationale/cost/failure behavior and unresolved input impacts |
| B04 | Every review finding maps to a milestone and check; agreed targets distinguished | This document; no efficacy or deployment claim |
| B05 | Live freeze and original checkout preserved | Local source/model baseline comparison; no remote mutation, migration or service command |

## M1 — full identity and authorization exit

Execute A01-A09 on **every applicable method/path**, both direct engine and gateway,
with synthetic actors in two tenants and same-tenant peers. Include list pagination,
receipt/detail/image/diagnostics and route variants; test each transport (JSON and
MessagePack). Include missing, invalid, expired, revoked and wrong-audience tokens.

| ID | Required behavior |
|---|---|
| A01 | OIDC login validates issuer/audience/signature/state/nonce/PKCE/redirect; rejects algorithm/key URL injection, reused callback and unknown provisioned actor; no session fixation |
| A02 | Self enrollment/add/revoke succeeds with recent login; another actor, delegated operator, reviewer or administrator cannot do it; wrong tenant denies even for identical subject/actor labels |
| A03 | Header/body/route identity conflicts and spoofed forwarded headers cannot override ownership; engine key alone fails; service-only token and forged context fail before inference |
| A04 | Challenge binds actor/session/subject/generation/operation; another actor's nonce fails; liveness is actor-bound too |
| A05 | Review requires live team/round grant, raw frames require separate permission; receipt checks owner or review grant; list queries scope before pagination; uniform other-owner/missing response |
| A06 | Logout/session expiry/account suspension/role removal apply across replicas and at commit; dependency failure never falls back to shared auth |
| A07 | Every new route and HTTP method defaults to denial; no catch-all passthrough bypass, framework docs leakage, static API fallthrough, HEAD/OPTIONS or path-normalization bypass |
| A08 | Audit records actor/action/resource/outcome/request ID for successful and denied security actions; contains no frames/embeddings/tokens; audit write failure denies sensitive mutation/read |
| A09 | Legacy identity transition never guesses ownership; fresh enrollment path and revocation work; real OIDC staging round trip and logout verified |

M1's isolated identity/HTTP boundary is complete: the broad deterministic
cryptographic/route matrix is supplemented by PostgreSQL transactional authorization,
replica/race/audit checks and real Keycloak login/logout through two HTTPS service
processes. See M1 evidence and M1_RUNBOOK.md for the exact 588-test run and limits.
These are synthetic nonbiometric workflows; M2-M8, production inference integration,
device/browser execution and live cutover remain separate gates.

## M2 — durable state exit

| ID | Required behavior |
|---|---|
| D01 | Synthetic normalized templates survive process restart; separate processes/replicas see identical records and model fingerprints |
| D02 | Capacity boundary (1,000 subjects/five templates), including concurrent additions, returns explicit error with no silent eviction; normalized comparison semantics unchanged |
| D03 | At least 20 simultaneous consumers of the same valid nonce yield exactly one successful claim; expiry/binding mismatch fails; service restart cannot revive a spent nonce |
| D04 | Enrollment versus revocation serializes by subject generation; no acceptance or inserted active template commits after earlier revocation; verify snapshot cannot bypass revocation |
| D05 | Duplicate idempotency request returns original committed outcome after current auth checks; changed payload conflicts; new key cannot reuse nonce; response loss cannot cause extra template |
| D06 | Datastore outage/transaction abort/capacity/invalid model version fail closed without partial template or false acceptance; retry is bounded |
| D07 | Forward migrations and supported-version rollback/restart succeed on synthetic data; old incompatible app refused |
| D08 | Recovery preserves allowed templates, invalidates sessions/nonces and respects deletion ledger; no embedding export from live needed |

M2's durable state boundary is complete for the isolated profile: D01–D08 are
closed by `tests-hardening/test_durable_state.py` with the frozen engine pipeline
behind the Analyzer port, four-process nonce races, a genuine 0001→0002 migration
and a `pg_dump`/`pg_restore` drill, all on synthetic nonbiometric data. See M2
evidence for the exact 617-test run and its limits (M3 keys/purge, M4 limits,
M5 envelope, M7 backup scope and RTO/RPO, M8 enrollment round remain open).

## M3 — privacy exit

| ID | Required behavior |
|---|---|
| P01 | Missing/wrong-scope/other-person/old-text/revoked consent blocks recording; required template consent blocks enrollment; optional recording choice never masquerades as authentication success |
| P02 | Concurrent grant/withdraw/record ordering matches architecture; withdrawal cancels future persistence and schedules already committed recordings for deletion |
| P03 | Tampered ciphertext, key version, tenant/resource AAD or unavailable key cannot decrypt or fall back to plaintext; least-privilege service key access verified |
| P04 | Rotation permits old reads and new writes, retires keys only when safe; synthetic key-loss/recovery drill documents exact consequence |
| P05 | Boundary-time expiry/self-deletion removes linked scan, diagnostics, wrapped key and accessible receipts as specified; repeated deletion succeeds safely |
| P06 | Full disk/partial purge/restart failures alert and deny expired reads; retry resumes without orphaned accessible data |
| P07 | Restoring an older backup cannot resurrect withdrawn/expired data; stale/missing deletion ledger keeps recovery quarantined; raw data excluded from backups |
| P08 | Export remains disabled; if file/manifest recovery is implemented, injected write/rename/manifest/DB failures leave no published incomplete dataset, using synthetic bytes only |

M3's isolated privacy boundary is complete: 49 new synthetic cases plus the M1/M2
suite passed in the 666-test integrated run. See [M3 evidence](evidence/M3.md) and
[M3 runbook](M3_RUNBOOK.md) for the P01–P08 mapping, failures/fixes and exact scope.
Key-purpose/provider separation is verified; OS key-file ACLs, separately durable
ledger/checkpoint publication, backup operations, delivered alerts and measured
RTO/RPO remain M7 dependencies. No raw backup, export, live integration or biometric
efficacy claim is made.

## M4 — security exit

| ID | Required behavior |
|---|---|
| S01 | Both services stop oversized streaming bodies before unbounded buffering, regardless of Content-Length/chunking; disconnect and slow-body release resources; measure memory |
| S02 | Principal and trusted-source limits cannot be bypassed by spoofed headers, session churn or peer tenant; unauthenticated scans never invoke inference |
| S03 | Saturation has bounded queue, memory and deadlines; BUSY/429 Retry-After consistent; concurrent auth correctness preserved |
| S04 | No insecure startup on absent secrets/issuer/keys; credential rotation overlap and retirement tested; engine private and proxy trust explicit |
| S05 | Cookie/CSRF/origin/CORS/security headers, login/logout replay, malformed input and logs tested; no secrets, frames, embeddings or other-user existence leaked |
| S06 | Replay/injection threat is documented; software nonce tests distinguished from camera attestation and measured PAD efficacy |
| S07 | Dependencies/images pinned, scanned with recorded disposition; mounts/privileges reviewed; all source and asset notices/weight permissions inventoried |

M4 is locally and staging verified, 20 September 2026: 72 new synthetic cases in
`test_resource_limits.py` and `test_security_hardening.py`, a 741-test integrated
run with zero skips, 21 of 21 checks over real HTTPS on the owned loopback stack,
and 100 of 100 protected hashes unchanged. See [M4 evidence](evidence/M4.md).

A follow-up the same day closed a **lockout defect** in the per-source limit —
with no trusted proxy declared, every visitor behind one shared a single bucket —
and replaced the guessed concurrency bound with a measured one. 772 passed / 0
skipped, 23 of 23 direct staging checks and 6 of 6 through a simulated proxy hop,
100 of 100 protected hashes unchanged. See
[M4 follow-up](evidence/M4-FOLLOWUP.md).

S01-S05 are closed. The measured S01 bound is about 620 KB of application heap,
1.29x the 481,964-byte cap, flat from 1 MB to 64 MB of client data.

S06 is a **written limitation**, not a measurement:
[THREAT_REPLAY_INJECTION.md](THREAT_REPLAY_INJECTION.md). The software nonce tests
are not camera attestation and not measured PAD efficacy; Q05 stays open.

S07 is **partly closed**. Application dependencies and the base image are pinned,
mounts and privileges reviewed, asset and weight notices inventoried. Still open:
no vulnerability scan was performed (no database and no network in this workspace),
the postgres and keycloak images are tag-pinned only, and `cap_drop` for those two
services is proposed but untested. Each needs explicit approval and a window.

The M4 controls are **not deployed**. The live AmFatec preview still has no rate
limit, no body deadline and no HSTS until an approved gateway-and-engine release.

## M5 — SDK and backend exit

| ID | Required behavior |
|---|---|
| C01 | Enrollment success requires all mandatory gates passed and committed template ID; missing/unknown/not-enforced/failed gate in HTTP 200 rejected |
| C02 | Verify acceptance additionally needs match=true, finite score/threshold and consistent predicate; no-match remains a rejected completed decision; liveness alone never proves identity |
| C03 | Recording receipt status independent of acceptance and transport; version mismatch and all contradictory field combinations rejected |
| C04 | Timeout/lost response/cancel after commit reported as uncertain; status/idempotency resolve without duplicate template; no automatic fresh-nonce mutation retry |
| C05 | Supported API/SDK version pairs tested; unsupported v1/v2 combinations fail explicitly; cancellation stops camera/timers/network resources |
| C06 | Stage boundaries/config/repository interfaces isolate authorization and persistence; helper removal has caller evidence; frozen model/threshold/capture values and existing inference regressions retained |

## M6 — QA and physical evidence exit

| ID | Required behavior |
|---|---|
| Q01 | Each acceptance ID links real tests/evidence and release revision/hash; CI builds tested artifacts, checks types/contracts/lint and blocks critical failures |
| Q02 | Synthetic E2E covers enrollment/verify/consent/review/revoke across two tenants and replicas |
| Q03 | Inject network/model/database/disk/key/purge failures, expired auth and lost post-commit response; record failures and repairs |
| Q04 | Team real-device checklist: Samsung A13 first; remembered/new consent, account change, permission denial/retry, background/resume, orientation, slow/offline network, cancellation/retry; new auth UI only in coordinated round |
| Q05 | Independent genuine/impostor/print/screen-photo/screen-video and injection evaluation across participants/devices/conditions with frozen policy, held-out data and uncertainty; missing evidence explicitly unmeasured |
| Q06 | Preserve original first attempts, retries, rejection reasons, missing records and denominators, stratified by engine/model/policy/infrastructure round; never merge older fast-policy results |

Physical testing is team-owned. This track may consume permitted metadata, never
download frames or manufacture physical results. M6 engineering evidence does not
close Q05 by itself.

## M7 / M8 — release and operations exit

| ID | Required behavior |
|---|---|
| O01 | Clean isolated build reproduces immutable artifact and checks model/asset hashes and permissions |
| O02 | Staging config validates distinct credentials, ports, databases, mounts, resource limits; no live endpoint or exporter enabling; preflight/readiness/migration/smoke gates tested |
| O03 | Encrypted restore drill uses synthetic allowed backup scope, separate recovery keys and current deletion ledger; measure actual RTO/RPO against agreed targets |
| O04 | Restart and application/schema rollback preserve data integrity; incompatible versions rejected; enrollment transition explicit |
| O05 | Key rotation/dependency outage/purge failure alerts reach designated owners with actionable runbooks; single-host outage documented |
| O06 | Isolated load measures latency/queue/rejection/memory at recorded hardware and target; unagreed targets cannot pass an SLA gate |
| R01 | Exact reviewed release, changed contracts, data migration, new enrollment round, downtime and executable recovery presented before requesting M8 window |
| R02 | Explicit test pause/window before any live mutation; read-only state refresh, preserved frozen biometric policy/retention, no synthetic contamination of team evidence |
| R03 | Independent security review resolves critical/high issues; team access confirmed; scores tied to evidence and remaining gaps |

## Next implementation order

M1–M3 are complete for the isolated hardened profile. Continue with M4 (streaming
bounds, admission, principal limits and security/dependency disposition), preserving
the current frozen behavior. M6 has runtime/transactional privacy, key, purge and
recovery evidence alongside OIDC and durable inference checks; hosted CI, integrated
SDK/UI and physical gates remain open. M7 operational dependencies are explicit in
M3 evidence. No deployment is authorized here.
