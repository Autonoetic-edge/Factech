# M3 consent, keys and protected lifecycle — isolated profile

Applies only to `E:\Factech` and ownership-checked synthetic infrastructure from
[M1_RUNBOOK.md](M1_RUNBOOK.md). No live migration, service restart, biometric access,
export, deployment or original-checkout change is authorized. One agent.

## Contracts and configuration

Schema head is `0004_recovery_gate`: M3 adds `0003_privacy` (consent history,
recording reservations, purge outbox, original template expiry) and
`0004_recovery_gate` (durable restore quarantine). Explicit owner migration only;
no startup migration. Older application/schema combinations are refused by
`ready()`. Destructive downgrade is refused. Keep old evidence unchanged.

Participants grant or withdraw only their own `template_authentication` and
`evaluation_recording` scopes through authenticated, CSRF-protected routes. Text
versions are `template-authentication-v1` and `evaluation-recording-v1`. Each
event records participant/actor equality, authority `self`, server timestamp,
scope/version and increasing revision. There is no acting operator or delegation.

Engine operations require an explicit `template_expires_at` round-end timestamp;
an unconfigured or closed round cannot commit a template. New templates retain
that original expiry. Migration gives old isolated rows a conservative expiry
seven days after their original creation; real-data transition still needs M8.
Template use, listing and commit deny expired data even if purge is unavailable.

The gateway may be configured with `Recording(repo, evaluation, round_id=...)`.
Recording is off without this adapter or without current optional server consent.
The round must be active with a finite expiry. Maximum recording expiry is seven
days from the server-issued challenge time, further shortened by round expiry.
No browser flag or remembered preference establishes consent. Existing operations
cannot be recorded retroactively after a later grant, including rejected replays.

The gateway reserves recording metadata before forwarding. After the engine's
durable outcome, it checks current identity, session, subject, consent revision,
original expiry and operation digest, and holds the subject lock during a bounded
evaluation-store transaction and application publication. No inference or provider
request occurs under that lock. Withdrawal winning first prevents the write;
recording winning first is hidden and scheduled for deletion by withdrawal.
An application failure after the separate evaluation commit leaves inaccessible
ciphertext attached to a durable reservation for cleanup. Evaluation tombstones
prevent delayed writers from recreating deleted records.

`recording.status` is independent of `accepted`/`decision`/HTTP status. A rejected
scan may be stored, and an accepted enrollment may have failed recording. Receipt
routes reveal storage status only. M5 owns the final response/SDK integration,
including UI status reconciliation; M6 owns device validation. The guided HTML
preview remains simulated and unintegrated; its URL-policy restriction is not bypassed.

## Keys and rotation

`EnvelopeCipher` uses a random AES-256 data key per record, AES-GCM payload and
key wrapping with separate fresh nonces, and authenticated schema/tenant/class/
resource context. Key version is authenticated in wrapping and checked against
the template's stored version. Ciphertext and wrapped key are stored together in
an envelope; deleting the record removes both. No plaintext or automatic legacy
fallback exists. `KEY_UNAVAILABLE`, `KEY_PURPOSE_DENIED`, `CIPHERTEXT_INVALID` and
`KEY_STILL_REFERENCED` are explicit fail-closed outcomes.

Engine, evaluation and backup use distinct purpose-restricted key providers.
`Keyring.from_file` reads an existing secret file and rejects configured code,
data and backup roots; it never generates replacement keys. `ready(versions)`
requires both the active key and all referenced historical versions. Runtime
factories must receive only their service's provider. The synthetic gateway
receives the evaluation cipher, not a template cipher. The opt-in HTTPS fixture
writes separate gateway/engine configuration files, each omitting the other data
class key. These are still synthetic files owned by one Windows account.

Rotation procedure for synthetic state:

1. Load old and new versions from the trusted secret source; retain the old key.
2. Switch new writes to the new active version. Old reads remain valid.
3. Run bounded `rotate_templates()` batches or `EvaluationStore.rotate()` cursor
   batches. Each transaction authenticates old data and rewraps the data key;
   payload ciphertext and original expiry remain unchanged.
4. Ensure every writer has switched and outstanding old-key writes have drained.
   Verify all remaining references, including rows skipped by a concurrent worker.
   Do not infer zero references merely from reaching the end of one cursor walk.
5. Do not retire a key until all relevant backups have expired or been rewrapped
   and their inventory is verified. `Keyring.retire` refuses active/referenced keys
   or an unverified backup inventory. Production inventory/custody is an M7 gate.

An absent key cannot be regenerated to recover its ciphertext. Restore the exact
key from a separately protected recovery package; otherwise the data is lost and
templates require an explicitly communicated new self-enrollment round. The tests
demonstrate both failure and restoration using synthetic keys. An explicit offline
`migrate_legacy` helper authenticates M1/M2 ciphertext under the old key and seals
an envelope under the new provider; it is never called by runtime reads. Existing
synthetic rows are not silently re-encrypted during schema migration.

## Purge, expiry and partial failures

`Lifecycle.expire(limit=...)` schedules bounded batches; `drain(limit=...)` leases
at most 100 jobs, with 30-second leases, capped exponential backoff and five total
attempts. A crashed last attempt becomes `blocked`; exhausted jobs do not spin.
Jobs persist across repository/process restarts. Record/receipt reads deny expiry
at the boundary and withdrawal immediately, independently of worker success.

Deletion removes template envelopes or evaluation envelopes containing scan,
frames and linked diagnostics, plus capture/receipt links. Minimal deletion
ledger/job/consent-proof metadata remains. Repeated deletes and lost purge
acknowledgements are safe; completed rows are never restored by retries.

Observe `Lifecycle.status()` and sanitized events `RECORDING_FAILED`,
`RECORDING_CLEANUP_PENDING`, `PURGE_FAILED`, `PURGE_STORE_UNAVAILABLE`, and
`PURGE_RETRY_EXHAUSTED`. Resolve the disk/store/key cause before explicitly
requeuing a blocked job with the offline service owner; never reactivate a resource
or discard its deletion ledger to make a retry succeed. Host scheduling, metrics
scraping, alert transport, named responders and incident drills belong to M7.

Retention policy: raw scans/diagnostics/receipts at most seven days and earlier on
withdrawal/self-deletion; templates no later than round end; idempotency outcomes
24 hours for access; audit metadata proposed 90 days; consent proof proposed 30
days after relationship closure; backup sets proposed seven days; separate deletion
ledger at least the oldest backup plus safety margin (proposed 14 days). These
non-raw durations need a data owner before real data use. Audit/consent-history
physical compaction and backup-set scheduling/custody are not implemented by the
raw/template purge worker and remain M7 operational work. Do not shorten ledger
retention before the backup inventory proves it safe.

## Recovery and backup scope

`backup_queries(now)` is an explicit application logical-backup allowlist. It
excludes evaluation databases, recordings, capture/receipt resource links,
sessions/provider tokens, login state, challenges, and operation results. It
filters expired templates and audit records. Raw evaluation data must also be
excluded from host/volume snapshots; no such snapshots are configured here.
Backup encryption uses a separate `backup` key provider. The exporter remains
absent/disabled; no dataset publishing or file/manifest repair was undertaken.

The synthetic recovery test performs an encrypted allowlisted application snapshot,
then post-backup withdrawal and an original-expiry boundary, then restores to the
owned temporary database. It does not claim a deployed backup service or RTO/RPO.

Recovery protocol:

1. Keep listeners disconnected. Prepare the compatible schema in a new owned
   restore database. Commit `quarantine(connection)` before loading the backup.
   Every repository checks the durable `recovery_state`; new replicas also deny.
   Never restore this control state from a backup as permission to serve reads.
2. Load only the allowed encrypted application backup. Obtain the separately kept
   authenticated ledger snapshot and independently trusted latest acknowledgement
   checkpoint, including exact snapshot digest and covered-through timestamp.
   Never derive that checkpoint from the old application backup.
3. Use `RestoreGate(required_through=..., required_digest=..., max_age=300)` and
   `enable_restored_reads(...)`. Missing, stale, tampered, future-dated or wrong
   checkpoint ledgers deny. Digest pinning prevents same-second stale snapshots.
4. The recovery transaction reapplies deletions/current consent, physically removes
   withdrawn/expired templates, invalidates sessions/nonces, refuses unavailable
   keys/model versions and disables evaluation links. Only a successful commit
   enables database reads. Restore must never extend original expiries.
5. New sessions and explicit self-enrollment are required where state was lost.
   The older `postgres.recover` is a low-level state-replay helper, not an entry
   point that proves ledger freshness or authorizes reads.

Independent ledger persistence before acknowledging real deletions, checkpoint
custody, backup inventory/expiration, separate OS service identities and key-file
ACL enforcement, recovery key packaging, clean-host drills and measured RTO/RPO
remain M7/M8 obligations. Current tests use one Windows owner and synthetic keys;
purpose restrictions demonstrate application capability separation, not OS
isolation or protection from a compromised host administrator.

Rollback is local: stop only the ownership-checked fixture. Keep schema head and
evidence; old incompatible code must remain disconnected. Use a fresh reviewed
quarantined synthetic restore if needed. There is no live release to roll back.
