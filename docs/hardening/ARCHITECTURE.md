# Hardening architecture and implementation contract

Date: 2026-09-19. Scope: local design for M0; not deployed. Authority:
`HARDENING_PLAN.md`, then this document and `ACCEPTANCE.md`. User decision in this
session: **self-service only**. Assigned operators must not enroll participants,
add their templates, verify as them, or revoke their templates.

## 1. Current system and trust boundaries

Source inspection, not a fresh VPS inventory. The historical live baseline remains
gateway `eval-pad-0e45ad88290f`, engine `eval-pad-0d87e7b9a807`, UI `ui-v6`.

**Note (removal, later than the design date above):** the legacy dev-only gateway
module tree this section inspected (its app, evaluation/review, capture-storage and
export-tooling files), the demo compose stack and Caddyfile, and the `apps/console`
and `apps/integration-demo` pages it exclusively served have all since been deleted
from the repository; the rows below describe them as they existed for this design's
source inspection, not code you can still open. The hardened gateway in
`packages/face-auth`, run via `deploy/amfatec`, is the only gateway left; `apps/verify`
is the only page it serves from this repo's front end.

| Boundary / caller | Current interface and authority | State / consequence |
|---|---|---|
| Browser -> Caddy -> gateway | Shared Basic Auth except `/health`; browser selects `user_id`, `X-User-Id`, `X-Capture-Meta` | No individual identity or ownership proof; camera, device fields and labels untrusted |
| SDK -> gateway | `GET /v1/challenge`, `POST /v1/enroll`, `POST /v1/verify`; console also uses info, status, revoke and liveness | JSON/base64 or MessagePack FaceScan v1; SDK `ok` describes shaped transport completion, not necessarily acceptance |
| Gateway -> engine | Catch-all `/v1/{path:path}` for GET/POST/DELETE; replaces engine key, forwards caller identity/operation | Shared key authenticates gateway access, not authority over the selected subject |
| Engine | `/health`, `/v1/info`, `/v1/challenge`, `/v1/enroll`, `/v1/verify`, `/v1/liveness`, `/v1/templates/{user_id}` DELETE | `engine/app/main.py`; FastAPI also creates docs/OpenAPI routes by default |
| Gateway status | `/engine-status`, `/capture-status`, proxied `/health` | Capture counts and health are different privileges; capture-status calls purge indirectly |
| Review | `/review`, GET `/review/api/captures`, GET receipt by request ID, GET detail by capture ID, GET frame by index, DELETE capture | the gateway's evaluation/review module; shared access, store internals; GET reads can trigger retention cleanup |
| Static assets | `/`, `/console`, `/integration`, `/sdk`, `/shared`, `/vendor/mediapipe` | Gateway static mounts; some conditional on directories; entry pages currently use canonical live origin routing |
| Engine repositories | `store.py`, `challenge.py` | Process dictionaries and thread locks; 1,000 users, five templates/user, silent oldest eviction; restart loses state; 30-second nonces bound to operation and caller-selected ID |
| Evaluation repository | the gateway's capture-storage module | SQLite/WAL, raw MessagePack scans, labels, receipts, diagnostic JSON and export inventory; evaluation retention seven days in Compose, generic fallback 90 days |
| Export / administration | its export-tooling module, deploy shell/Python commands; no public identity administration API | Export is disabled; commands can mutate files/storage and must never be invoked against live during this track |
| Model pipeline | SCRFD -> aligned ArcFace -> mandatory two-model PAD and sequence/heuristic gates | Normalized 512-D embeddings; max cosine over templates; current threshold 0.55; no production accuracy claim |
| Operations | Compose/Caddy, `.github/workflows/*`, `deploy/*` | Separate gateway/engine containers; single host; several dependencies/image tags unpinned; old gateway-only rollback cannot undo new schema/auth changes |

Authoritative source entry points (as inspected; the gateway paths below no longer
exist — see the removal note above): `engine/app/{main,auth,store,challenge,trace}.py`,
the removed gateway's app/evaluation/capture-storage/export-tooling modules,
`packages/face-sdk/src/{types,transport/gateway,workflow/session,workflow/run}.ts`.
`routes.json` records the explicit current route surface without importing apps or
opening capture stores. Framework routes and static mounts are classified separately.

## 2. Identity and authority

Use server-generated opaque IDs. An actor is a locally provisioned account bound to
the exact OIDC `(issuer, sub)` pair, never email, display name, tester code or a
forwarded header. A tenant is a team security boundary; begin with one configured
team but retain tenant keys in all repositories. No automatic cross-team membership.

A participant is the person associated with that account for self-service scans.
An enrollment subject is a server-created record uniquely linked to one participant
and tenant. A claimed legacy `user_id` is only a selector: it must resolve to that
actor's subject or be rejected. One subject per participant initially; multiple
templates are allowed within its capacity. Role sets do not imply ownership.
Self-enrollment links an account and a face; it does not establish civil identity.

Reviewers receive explicit team/round grants, including a separate raw-frame grant.
Administrators manage account memberships, grants and operational configuration,
but have no automatic biometric read or participant action rights. Service
principals have narrowly scoped purge/backup/audit duties and cannot log in as a
participant. No operator/delegation role. Accounts with several roles retain the
same resource restrictions for each action. Disabled accounts, revoked sessions,
expired grants, wrong tenant, absent ownership and unknown actions always deny.

### Role / action / resource matrix

`Own` requires both tenant and owner actor equality from a trusted repository.
`Grant` requires an active grant for the resource's team and round. A dash denies.

| Action / resource | Participant | Reviewer | Administrator | Service |
|---|---|---|---|---|
| Minimal health / public login assets | Public | Public | Public | Public |
| Authenticated info, own session, engine readiness | Yes | Yes | Yes | Explicit probe only |
| Issue challenge: enroll/verify/liveness | Own | - | - | - |
| Enroll or add template | Own, recent login, fresh bound challenge | - | - | - |
| Verify or liveness | Own, fresh bound challenge | - | - | - |
| List template metadata / revoke templates | Own; revoke needs recent login | - | - | - |
| Grant/withdraw template or evaluation consent | Own | - | - | - |
| Own receipt (storage status only) | Own | Grant | - | - |
| Capture list/detail/diagnostics | - | Grant: metadata | - | - |
| Capture frame image | - | Grant: frames AND metadata | - | - |
| Request deletion of own evaluation records | Own | - | - | - |
| Execute expiry/withdrawal deletion | - | - | - | Purge role, policy-bound |
| Capture count / operational status | - | Grant: aggregate | Yes, aggregate only | Monitoring role |
| Review labels or export | - | - | - | Disabled; separate future decision |
| Manage membership / review grants / disable account | - | - | Yes, audited; no self-escalation | - |
| Audit event metadata | - | - | Explicit audit grant | Append-only writer |
| Backup / restore | - | - | Coordinate approved runbook | Designated offline recovery role |
| Unknown route/action, generic proxy, framework docs | - | - | - | - |

New routes are denied until explicitly registered. Disable framework docs in the
hardened service. Replace gateway catch-all forwarding with exact allowlisted
method/path policies. Read ownership before revealing existence; other people's
IDs return a uniform resource-not-found response. Lists are filtered in the query,
not after pagination. Request IDs and sequential capture IDs are not credentials.
Participant deletion is self-service; administrative account suspension stops access
but does not grant template management. Retention service deletes under policy.

## 3. Authentication and gateway-to-engine contract

**Selected:** Keycloak OIDC, authorization code + PKCE S256, through a gateway
backend-for-frontend using a maintained OIDC client library (Authlib integration
to be pinned and tested in M1). No custom password database, implicit grant, direct
password grant or browser-stored access tokens. Keycloak provides standard discovery,
code exchange, introspection and logout endpoints ([official protocol guide](https://www.keycloak.org/securing-apps/oidc-layers)).

Use exact issuer/audience/redirect allowlists, state and OIDC nonce, signature and
algorithm allowlist, exp/nbf/iat validation with at most 30 seconds clock tolerance.
An unknown signing key triggers one bounded refresh, then denial. Never discover
an issuer or fetch a key URL supplied by an untrusted token.

The browser holds only a random opaque session cookie, `__Host-facetech`, Secure,
HttpOnly, SameSite=Lax, Path=/, no Domain. Store its hash and encrypted provider
tokens server-side. Rotate on login/privilege change. Local contract: 30-minute
idle and eight-hour absolute expiry, five-minute recent-login requirement for
enroll/add/revoke. These security limits are implementation decisions; the capture
challenge TTL remains exactly 30 seconds. Cookie mutations require CSRF token plus
exact Origin validation; no wildcard credentialed CORS. Login callback uses state.

Check local session/account status and provider token activity on protected requests,
including engine acceptance/commit. No positive introspection cache initially.
Provider/database unavailability fails closed with a typed 503; no Basic Auth or
shared-key fallback in the hardened profile. Logout invalidates local session first,
then requests provider logout/revocation; retry remote failure without restoring
local access. Backchannel logout must verify the signed logout token and replay ID.
Do not hold inference resources while waiting for identity network requests.

**Engine context:** retain service authentication as one control; additionally
forward the actual audience-bound user access token over a private authenticated
TLS channel. Engine independently validates token activity/issuer/audience and
resolves `(issuer, sub)` to active actor, session and subject in the repository.
Local session reference is checked against that same token/session binding.
Gateway never signs an arbitrary subject assertion as a substitute for ownership.
Reject client-supplied engine keys/context/role headers; the proxy constructs its
own headers. Subject in payload, route, challenge and resolved owner must agree.
For liveness bind to the actor's subject too, even though legacy liveness had no ID.
Recheck session and subject generation at commit to close revocation races.

Cost: self-hosted Keycloak adds an identity process, database/schema, TLS, patching
and backup duties; PostgreSQL is another stateful service. No paid provider or new
host is required for local work; no VPS capacity allocation is authorized. M1 now
verifies a separate real Keycloak realm/client with an explicit synthetic roster.
The real team identity owner and named reviewer/admin roster remain M8 inputs.
Pin supported release versions/digests after vulnerability review, before staging.

## 4. Durable state and transaction contract

**Selected:** PostgreSQL 17, SQLAlchemy 2 + psycopg 3 and Alembic, with exact package
patches pinned during M2. Separate identity, application and evaluation databases,
service accounts and backup policies; no shared live database. SQLite remains only
the legacy capture repository and small test doubles, never replica-consistency
evidence. PostgreSQL supports row locks and serializable transactions; retry a whole
aborted transaction, not selected writes ([transaction documentation](https://www.postgresql.org/docs/17/transaction-iso.html)).

Logical records (all timestamps UTC, all relationships tenant-scoped):

- actors: issuer, external subject, local actor ID, active state, authorization epoch.
- memberships / reviewer grants: actor, tenant, role, round, scopes, expiry, revocation.
- subjects: owner actor, participant ID, opaque subject ID, generation, state;
  unique `(tenant, owner)`; legacy aliases require explicit verified mapping.
- templates: immutable template ID, subject, encrypted normalized vector, model
  fingerprint/dimension/format, creation and revocation time, key version.
- challenges: nonce digest, actor/session/subject/generation/operation binding,
  frozen action/params, issued/expires/consumed timestamps. Never back up active nonces.
- operations: actor/subject, idempotency key, request digest, challenge digest,
  processing/completed/failed state, sanitized result, expiry; unique actor/key.
- consents: participant=actor, subject, scope/text version, grant/withdrawal time,
  authority=`self`, monotonic revision. No operator may grant for a participant.
- captures/receipts: owner, round, consent revision, request/operation ID, ciphertext,
  original expiry, diagnostic link and recording outcome. No frames in receipts.
- audit/deletion ledger: actor/action/resource reference, outcome, request ID,
  time and policy version; no scan, embedding, token, raw nonce or device payload.

Capacity remains 1,000 active subjects per team and five active templates per
subject for the initial contract; exceed either -> `CAPACITY_EXCEEDED`, no eviction.
Lock the team capacity row and subject row in consistent order for creation/addition.
Preserve vector normalization, float representation and max-cosine comparisons.
Reject unknown/incompatible model fingerprint or vector format before comparison.

Atomic challenge consumption uses conditional UPDATE of unused/unexpired, fully
bound state and checks the affected row count. Claim an operation and spend its
nonce in a short transaction **before** inference; failed inference leaves it spent.
No database transaction stays open for a 16-second capture or inference call.
Final enrollment transaction rechecks active session, subject generation, required
consent and capacity before inserting template + result + audit. Revocation locks
the same subject, increments generation, revokes templates and invalidates pending
operations. Verification checks generation again before committing acceptance;
an acceptance committed before revocation remains a historical event, not a new
long-lived authorization token. A new request after revocation cannot accept.

Idempotency is actor/operation/subject scoped, with a canonical request digest.
Different body with same key -> 409. Same completed operation may return the saved
result for up to 24 hours after **current** session/ownership checks, explicitly
marked replay; it never consumes another nonce or authorizes a new authentication
event. Reuse of a spent/expired challenge with a new key always fails. A processing
operation returns 409 `OPERATION_IN_PROGRESS`; interrupted work ends failed and
requires fresh capture. If commit outcome is unknown, query the operation; never
blindly resubmit enrollment with a fresh idempotency key. Cancellation cannot undo
a commit. Operation records expire independently of template lifetime.

## 5. Consent, retention, encryption and recovery

Separate scopes: `template_authentication` for required template use and
`evaluation_recording` for optional raw scans plus linked diagnostics/reviewer
access under the displayed text version. Reject enrollment if required consent
is missing; missing optional evaluation consent skips storage without changing
the biometric verdict. Grant/withdraw is authenticated, server-timestamped and
self-service. Read consent before processing and compare revision under lock at
recording commit. Withdrawal won first -> no record; recording won first -> enqueue
its deletion under the withdrawal transaction. Existing live consent semantics and
seven-day expiry remain unchanged until a separate M8 round.

| Class | Local contract / proposed deployment policy | Backup |
|---|---|---|
| Raw evaluation scans, device metadata, linked diagnostics | Maximum seven days from capture, earlier self-deletion/withdrawal; original expiry immutable | Excluded, including physical snapshots containing their database |
| Templates | Until revocation/account closure; additionally expire at evaluation round end until a longer production period is agreed | Encrypted application backup; purge/deletion ledger applied before any restored reads |
| Consent records | Minimal proof during active relationship plus proposed 30 days after closure | Application backup, same expiry enforcement |
| Security/audit metadata | Proposed 90 days, pseudonymous IDs, restricted audit grants | Encrypted metadata backup, original expiry enforced |
| Sessions, provider tokens, challenges | Session/nonce expiry above; spent nonce diagnostic tombstones at most five minutes | Excluded from logical backup; revoke all restored sessions and nonces |
| Idempotency results/receipts | Results 24 hours; evaluation receipts at most seven days; no raw payload | Results may be excluded; reconcile uncertain commits by durable operation reference |
| Application backup sets | Proposed maximum seven days; expired sets deleted | Separate encrypted recovery location; separate keys |
| Deletion ledger | Survives oldest retained backup plus a restore safety margin; proposed 14 days after deletion | Separate durable ledger; restore unavailable if ledger freshness cannot be proved |

Seven days for evaluation is agreed by the existing plan. The other durations are
explicit conservative local defaults/proposals requiring a data owner before M7/M8,
not a legal compliance determination. No raw-frame backup is needed for this track;
loss of those optional records is acceptable for engineering staging but must be
reported as missing physical evidence. No existing capture transfer is authorized.

**Selected encryption:** use `cryptography` AES-256-GCM with a fresh 96-bit nonce
per encryption, key version and authenticated context binding tenant, resource ID,
data class and schema version. Integrity/tag failure must not yield partial
plaintext ([library AEAD requirements](https://cryptography.io/en/latest/hazmat/primitives/aead/)).
Use per-record data keys wrapped by a versioned key-encryption key. Keyring supplied
through a read-only, service-restricted secret file outside repository/data volumes,
not `.env` literals or database columns. Separate template/evaluation/backup keys;
only the engine can decrypt templates, only authorized evaluation service can
decrypt frames. Review does not read the template database.

This initial file-backed key provider is an explicit single-host trust decision,
not protection against a compromised host administrator. Provider interface permits
a managed KMS later; none is purchased/provisioned. New writes use active key,
rotation rewraps data keys with old/new keys available, verifies all references,
then retires old key only after relevant backups expire or are rewrapped. Missing
key fails readiness and operations; never regenerate a lost key or write plaintext.
Keep an encrypted recovery key package separately under a named custodian before
any real data deployment. Key loss without recovery -> unrecoverable ciphertext,
fresh enrollment after explicit communication.

Deletion is idempotent and includes ciphertext, wrapped key, diagnostics and derived
links; audit retains only minimum permitted event metadata. Purge failures deny
expired reads immediately, emit alerts and retry bounded batches. Restore starts
quarantined: replay fresh deletion ledger, apply original expiries, invalidate
sessions/challenges, check key/model versions, then enable access. Old backups must
never restore withdrawn templates to active use. Separate databases prevent raw
frames entering application backups accidentally.

## 6. Decision API and migration

Introduce hardened API v2 and SDK major contract version separately from FaceScan
wire v1 and frozen biometric policy. Local v1 remains unchanged during team testing.
At cutover, legacy v1 is disabled on hardened infrastructure (410), never an auth
bypass. No automatic fallback by SDK. Roll out tested compatible API/SDK pairs.

Typed v2 decision envelope:

```json
{"api_version":2,"request_id":"opaque","operation_id":"opaque",
 "operation":"verify","decision":{"accepted":true,"reason":"MATCH",
 "gates":{"pad":"passed","sequence":"passed","liveness":"passed"},
 "match":true,"score":0.8,"threshold":0.55},
 "recording":{"status":"skipped","reason":"NO_EVALUATION_CONSENT"},
 "replayed":false,"engine_build":"release","model_fingerprint":"manifest-hash",
 "policy_version":"pad-sequence-v2-slow"}
```

Example values are synthetic, not evidence. Gates have enum passed/failed/unknown;
mandatory gates must all be passed. Enrollment additionally requires committed
`template_id`; verification additionally requires `match=true` and finite consistent
score/threshold; liveness has no identity-match claim. Unknown, missing, not-enforced
or contradictory fields cannot accept. No-match is a completed rejected decision,
not a transport failure. Recording has stored/skipped/failed/pending states and is
independent of acceptance. SDK exposes transport completion, decision acceptance,
recording and uncertain commit independently; old `ok=true` is not redefined silently.

Errors: `{api_version,request_id,error:{code,message,retryable},operation_id?}`;
uniform shape even validation errors. 401 AUTHENTICATION_REQUIRED/SESSION_EXPIRED;
403 FORBIDDEN/CONSENT_REQUIRED/CSRF_FAILED; 404 RESOURCE_NOT_FOUND without other-owner
existence disclosure; 409 CAPACITY_EXCEEDED/IDEMPOTENCY_CONFLICT/OPERATION_IN_PROGRESS/
SUBJECT_REVOKED/MODEL_VERSION_MISMATCH; 413 PAYLOAD_TOO_LARGE; 422 MALFORMED_SCAN/
CHALLENGE_FAIL/LOW_QUALITY/LIVENESS_FAIL/NO_FACE/MULTI_FACE; 429 RATE_LIMITED;
502 UPSTREAM_UNAVAILABLE; 503 IDENTITY_UNAVAILABLE/STORE_UNAVAILABLE/KEY_UNAVAILABLE/
MODEL_UNAVAILABLE/BUSY. v1 codes stay unchanged until explicit migration. No internal
URLs, credentials or biometric bytes in errors; structured reasons are allowlisted.

Only idempotent status reads get bounded automatic retry (two retries, jitter,
overall deadline); mutations require idempotency and status reconciliation.
Apply 481,964-byte streaming body cap before full buffering at both services,
including absent/false Content-Length, disconnect and slow-body cases. Keep the
350 KiB decoded scan bound. Admission and principal/source quotas precede inference;
initial one inference worker/four queued scans remains bounded until measured M4/M7.

Migration: create new stores, expand schema compatibly, test synthetic forward and
rollback paths. No ownership inference from existing tester codes or names. Inspect
volatile-template export feasibility by code only; there is currently no supported
template export route. Default cutover therefore requires a new enrollment round;
never restart expecting memory to survive. Legacy captures remain under existing
access/retention and are not imported into new principals automatically. Document
any approved verified mapping separately. Application rollback may use only the
schema range it supports; otherwise isolate failed release and restore a compatible
snapshot plus deletion ledger. Gateway rollback alone is insufficient.

## 7. Targets and unanswered business inputs

Agreed: live team environment unchanged, self-service only, frozen biometric values,
no raw transfer/export, seven-day live retention, no readiness score without evidence.
The following are **proposed staging measurement targets, not agreed SLOs**:

| Target | Proposed experiment / measurement | Gate until agreed |
|---|---|---|
| Availability | Measure protected-route availability and dependency failures; candidate 99.5% monthly | No production availability claim; single host fails as a unit |
| Peak load | 10 active synthetic sessions, one inference worker, four waiting scans; overload rejects within one second | Team to supply expected concurrent users and arrival rate; do not load-test VPS |
| Latency | At that load, p95 post-upload decision <=5s, auth/status <=500ms; report p99, queue and cold starts separately | 16s capture is excluded and also reported in end-to-end time |
| RTO | Synthetic clean-host restoration <=4 hours | Owner must accept outage window and designate recovery operator |
| RPO | Templates/consent <=24h with nightly encrypted backup; deletion ledger must include all acknowledged deletions | Owner must accept possible fresh enrollment; no durability claim until drill |
| Raw evaluation loss | No backup; report missing records without fabricated denominators | Team must accept this policy for new round |

Pending named owners: real identity/realm and reviewer roster (M8 cutover; M1 uses
an explicit synthetic staging provisioner), jurisdiction,
retention/data purpose and key custodian (M3 real data), SLO/load/RTO/RPO and hardware
budget (M7 gate), model use/redistribution permission and independent review (M8).
Local code, synthetic tests and schema work do not wait for these inputs. Never
interpret silence as approval for live deployment or a weaker control.

## 8. Provenance and license gates

Keep detector/recognition hashes in `engine/scripts/download_models.py`, PAD ONNX
and checkpoint hashes/conversion versions in `engine/models/anti_spoof/manifest.json`,
and browser asset URLs/hashes in `apps/vendor/fetch.sh`. Do not change model bytes,
preprocessing, thresholds, nonce policy or capture cadence in this track.

Before redistribution/production, inventory each source/weight/asset separately:
upstream URL + revision, received checksum, local checksum, conversion command,
license text/notice, intended use and documented permission. InsightFace distinguishes
MIT source code from pretrained model terms, including noncommercial research
restrictions; treat weight-use permission as unresolved for intended deployment
([upstream licensing](https://github.com/deepinsight/insightface#license)). PAD's
bundled Apache-2.0 repository notice is not proof of separately reviewed checkpoint
rights. Record that uncertainty. Inspect MediaPipe JS/WASM and detector weight
licenses and all fonts separately; missing notices or unclear rights block
redistribution. Dependency SBOM, immutable image pins and vulnerability disposition
are M4/M7 evidence. Hash equality demonstrates provenance consistency, not efficacy.

The findings-to-test map and exact exit gates are in `ACCEPTANCE.md`. None of these
design selections authorizes a live change.

## 9. M3 isolated implementation checkpoint

M3 is locally and synthetically verified at schema `0004_recovery_gate` (revisions
0003/0004 both belong to M3). See [M3 evidence](evidence/M3.md) for 666 passing
integrated tests and [M3 runbook](M3_RUNBOOK.md) for the executable adapter contracts.
The server requires explicit round-end template expiry, authenticated self-consent,
and optional recording revision checks under the subject lock. Envelope data keys
are purpose-separated and rewrapped without changing payload/expiry. Linked purge
is bounded and observable. Recovery requires durable quarantine plus an independently
pinned authenticated ledger digest and freshness checkpoint before reads.

Application backup scope excludes raw evaluation stores, sessions/tokens/nonces,
operation results and capture/receipt links. Production checkpoint durability,
backup execution/expiration, key custody/OS permissions, scheduler/alert transport,
non-raw metadata compaction and measured recovery remain M7/M8 gates. Existing live
consent/retention/capture behavior and the guided design preview are unchanged.
