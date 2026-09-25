> HISTORICAL RELEASE REPORT: preserve this evidence, but do not use its next steps or deployment commands as current instructions. Read E:\Factech\HARDENING_PLAN.md and pad-evidence/consent-v6/REVIEW.md for the current handoff and last verified baseline.

# Recording origin mismatch diagnosis and fix — ui-v5

Gateway release: eval-pad-c574af5467d2.
Engine unchanged: eval-pad-0d87e7b9a807. Policy unchanged: pad-sequence-v2-slow.
One agent. Work confined to E:\Factech; original E:\Facetech not edited.

## Evidence

The reported ui-v4 failure was not resolved by the prior status-refresh change.
Active VPS gateway logs since deployment contained internal health/status checks
and our served-file probes, but no phone status requests. Read-only inspection
confirmed the public Traefik route forwards this hostname to loopback Caddy 8099;
Caddy forwards all routes to the single gateway on facetech-demo_demo. No duplicate
gateway alias or alternate backend existed on that network.

The laptop still has a phone HTTPS server at https://192.168.1.5:8443 (PID 36828).
Using its existing CA certificate with normal certificate validation:
- /capture-status returned {"enabled": false}.
- /health returned engineVersion 0.1.0, buildId dev.
- Root page contained ui-v4 and served the updated E:\Factech source files.

Thus the latest UI version was being served by two different backends. The laptop
endpoint reproduces the exact disabled-recording condition even with the latest
UI, while the VPS enables recording. UI revision alone was insufficient evidence
of the intended deployment. This is a confirmed configuration/route mismatch,
not a caching diagnosis. The user's exact Samsung address and diagnostic JSON
were requested but not supplied during this turn, so attribution of their specific
phone request to the local endpoint remains unconfirmed.

## Fix

The evaluation page now declares its canonical recorded site explicitly:
https://facetech.31.97.186.120.sslip.io/?v=ui-v5

Before any status fetch or capture-option installation, the form checks its page
origin against that canonical URL. Other origins (including the existing laptop
phone address) disable capture actions and navigate to the recorded site. The
navigation uses only the fixed URL; it transfers no tester IDs, form values,
credentials or frames. The public HTTPS site retains Basic Auth. A visible link
also identifies the correct site. Diagnostics now include the actual page origin.
No cross-origin API fetch workaround and no enabling of local unrecorded testing.
The one explicit consent checkbox, fresh status checks, timeout, fail-closed policy,
consent withdrawal/reset and saved receipt behavior from ui-v4 remain.

## Checks

25 form tests and 7 affected form/page integration tests passed: 32 cases total.
New tests cover the actual laptop origin, localhost and HTTP origin, navigation
before any fetch, disabled actions, canonical-origin behavior and diagnostic origin.
The affected integration cases retain consent metadata/receipt/reset, failure
recovery, duplicate taps and cancellation during pending status requests.
Unchanged PAD/model/security suites were not rerun.

Verified over certificate-validated HTTPS that the live laptop service serves the
new local page and script exactly, while its backend still identifies itself as
old dev with recording disabled. A browser attempt at the laptop address hit that
browser's untrusted-local-CA warning; it was not bypassed. Actual browser execution
of the local redirect was therefore not established by that attempt. Unit tests
establish routing behavior; a phone confirmation remains necessary.

All 72 release checksums verified; only three UI runtime files differ. Staging
served ui-v5 and enabled recording with zero captures. Gateway-only cutover used
--no-deps, preserving the live engine and Caddy IDs/images/start times. A check
immediately after container start briefly received connection refused; retry after
startup passed, and both services are healthy. Caddy's gateway connection serves
exact deployed ui-v5 page/scripts. Public health remains the intended PAD engine
build; root, capture-status and review remain 401 without Basic Auth.

All eight capture metadata/decision records compare equal before/after deployment.
Current recording status: enabled true, evaluation_only true, retention seven days,
consent storage-consent-v1, total 8, write/bank/purge failures zero. No physical or
synthetic scan was submitted. No capture bytes transferred, labels changed,
captures deleted, exporter enabled, or capture backup claimed.

## Next physical check

Use the direct VPS link above. Verify ui-v5, Server build eval-pad-0d87e7b9a807,
and Recording is ready. Keep the same tester code for the same person, accurate
case/lighting/glasses, a fresh genuine enrollment target, and select Save this
attempt. Perform one enrollment and retain both request ID and saved capture
receipt; then review diagnostics before repeating failures. Physical recording
success on Samsung is still pending. Existing 5/5 labelled-genuine rejection
results and lack of fresh learned-PAD physical attack evidence remain unchanged.

## Gateway-only rollback

    cd /opt/facetech-releases/eval-pad-c9a87c4f2266
    docker compose --env-file .env -f docker-compose.demo.yml -p facetech-demo up -d --no-build --no-deps gateway

Old gateway image also tagged facetech-mock-gateway:rollback-origin-v5.
Current release intentionally reuses the previous engine image. No engine restart
or capture cleanup is needed for this UI rollback. Staging gateway is stopped.

## Weekly usage

Start: 64% used (+0 from prior completion).
After local changes/tests: 65% (+1 percentage point).
Immediately before deployment: 65% (+0 since tests; +1 this turn).
