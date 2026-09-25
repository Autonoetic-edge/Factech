# M4 follow-up — the proxy lockout, and limits measured instead of guessed

Status: **LOCAL_VERIFIED and STAGING_VERIFIED on the owned loopback stack**,
20 September 2026, one agent. **No live change was made.** All **100 protected
hashes are unchanged**. No model, threshold, capture timing, challenge policy,
SDK or page was touched.

## The defect, stated plainly

`Panel.source()` fell back to the transport peer whenever `AMFATEC_TRUSTED_PROXY`
was unset or wrong. Behind Traefik that peer is the same address for every
visitor, so every per-source allowance collapsed into one bucket: one attacker
spending the 60-per-minute auth allowance would have locked out the whole team.

**The M4 report called this "safe but coarse". That was wrong.** It is a lockout,
not a coarse-grained control, and a wrong proxy address failed the same way with
no signal. This document supersedes that judgement.

Nothing was deployed with the fallback in place, so no live exposure was created
by it — but the release proposal in M4.md would have shipped it, and its point 4
explicitly said "without it, set nothing rather than guessing". That advice was
wrong and is withdrawn.

## What changed

**`serve.py` — `AMFATEC_TRUSTED_PROXY` is now required for the gateway.** It goes
through the same `need()` as every other S04 secret and deployment fact, and it is
read before any connection object is built, so a gateway with no declaration stops
at startup naming the variable. The literal `direct` declares no proxy in front. A
blank or comma-only value is not a declaration and also stops startup. The engine
does not need it and does not ask for it.

**`panel.py` — `source()` has three cases and no fallback.**

| Peer | `X-Forwarded-For` | Source |
|---|---|---|
| not a declared proxy | absent | the peer |
| a declared proxy | non-empty | its rightmost entry |
| anything else | | **refused**, `UNTRUSTED_CONTEXT` 400 |

The third row catches both a declared proxy that sent no usable hop **and** an
`X-Forwarded-For` arriving from a peer that is not declared — which is exactly how
a wrong address announces itself instead of failing silently. The rightmost entry
is the one the declared proxy appended, so a client's own forged entries stay to
its left and are never read. The header is still dropped from the forwarded
request in every case, and the gateway still refuses `Forwarded`/`X-Forwarded-*`
outright on its own.

**`http.py` — the refusal is carried, not swallowed.** `Gateway.source()` treats an
explicit `None` in the ASGI scope as "the trusted-proxy layer cannot name a source
I trust" and raises `Denied("UNTRUSTED_CONTEXT", 400)`, so the refusal gets the
same request id, audit line and response shape as every other forwarded-header
trust failure. `/health` never reaches this code and stays reachable: it is the
container and proxy liveness probe, and refusing it would turn a misconfiguration
into a false outage.

**`limits.py` line 56 — the comment said idle keys are dropped first; the code
drops the least recently used.** The comment now says what the code does, and the
eviction is one `popitem(last=False)` instead of a lookup-then-pop.

## Measured capacity, and what it changed

`tools/hardening_m4_capacity.py`, results in
`docs/hardening/evidence/m4-capacity.json`.

```powershell
.\.venv-pad\Scripts\python.exe tools\hardening_m4_capacity.py scans
.\.venv-pad\Scripts\python.exe tools\hardening_m4_capacity.py auth
```

`scans` drives four real recorded captures — the four the live engine accepted on
the current `HEAD_SEQUENCE` policy — through the frozen pipeline in process,
exactly as the engine runs it. It reads those captures read-only from outside the
repository and writes only timings, byte counts and frame counts.

| Measurement | Number |
|---|---|
| Per accepted 12-frame scan, **live engine's own recorded total** | 2,173–2,509 ms |
| Per accepted scan, this dev laptop | 4,033–4,506 ms, median 4,259 ms |
| 5 scans fired at once, this laptop | 20,086 ms wall; **last caller waited 19,810 ms** |
| 6 scans fired at once | 23,355 ms wall |
| Engine admission | 1 worker, 4 queued, 5 admitted total |
| One OIDC sign-in | 4.06 s, costs 2 auth-window calls |
| Authenticated requests served | 23.6 per second |
| Unauthenticated `/auth/*` served | 36.1 per second |

**The engine is strictly serial** — one worker lock — so concurrency buys nothing:
the Nth admitted caller waits N × the per-scan time.

### What changed: `Admission(5)` → `Admission(3)`

The gateway's own call to the engine has a 10-second timeout (`http.py`). At the
live engine's measured 2.51 s worst case, five admitted scans make the last caller
wait 12.5 s — **past the gateway's own timeout, so the gateway abandons a scan the
engine is still running.** On this laptop it was measured at 19.8 s. Three is the
largest value that fits: 3 × 2.51 = 7.5 s. Past that an immediate `BUSY` with a
`Retry-After` serves the caller better than a stall that ends in a timeout.

The gateway bound is now below the engine's own 5, so the engine's `BUSY` becomes
a second line of defence rather than the first one the caller meets.

### What did not change, and why

| Limit | Kept at | Against the measurement |
|---|---|---|
| `source` | 300/min | Service serves 1,400–2,100/min. A policy bound, two orders under capacity. |
| `auth_source` | 60/min | = 30 sign-ins a minute from one NAT address, at a measured 2 calls and 4.06 s each. |
| `actor` | 120/min | Spent in 5.0 s of flat-out calling; far under the 1,416/min served. |
| `scan` | 10/min | A capture takes ~16 s, so an honest participant cannot reach it. The engine ceiling is ~24 scans/min in total, so one principal could take ~42% of it — but `Admission(3)` and the capture time are what actually bound that, and tightening this would bite a tester retrying after a head-gate rejection. |

### Still unmeasured, and it says so in the code

- **Honest demand.** Nobody has counted what a real round of testers asks for per
  minute. Every `Window` is sized off capture arithmetic and the measured sign-in
  cost, not off observed usage. `limits.py` says **unmeasured** in those words.
- **`BODY_SECONDS = 15`.** No upload from a real participant's network has been
  timed. `limits.py` says **unmeasured** in those words.
- **The VPS's own scan latency under the deployed container's CPU limit.** The
  2.17–2.51 s figures are what the live engine recorded on 20 September under the
  load it had then, not a controlled measurement. **Unverified** as a ceiling.

## Tests

**772 passed, 0 skipped, 0 failed, 190.65s** with both real providers
(`m4-followup-tests.xml`). Delta from M4's 741: **+31, nothing removed, nothing
skipped, nothing weakened** — verified by comparing the two JUnit files name by
name. Of the 31, **16 are mine** (`test_trusted_proxy.py`) and **15 are
`test_head_gate_replay.py`**, which was already on disk in this workspace,
written at 16:58 after the 16:34 M4 completion run, and is not part of this work.

| Check | Result |
|---|---|
| `tests-hardening`, both providers | **772 passed, 0 skipped, 0 failed** |
| `tests-panel` | 11 passed |
| `node --test apps/tests` | 206 passed, **1 failed** — the same stale `?v=ui-v6` assertion M4 documented; both files are protected and untouched |
| Ruff check / format | pass, 3 files reformatted |
| Protected hashes | **100 / 100 unchanged** |
| Legacy route inventory | 19 routes, unchanged |

`tests-hardening/test_trusted_proxy.py`, 16 cases, alongside the existing files.
Two clients behind one declared proxy hold separate allowances and neither can
spend the other's; a forged left-hand entry cannot either; a declared proxy sending
no `X-Forwarded-For` is refused; an `X-Forwarded-For` from an undeclared peer is
refused; `/health` still answers when the source cannot be resolved; gateway
startup stops with the variable undeclared and passes the gate on `direct`; the
engine does not require it.

## Staging over real HTTPS

Two runs on the owned loopback stack (PostgreSQL 17.11 on 15432, Keycloak 26.7.4
on 18443, engine 18445, gateway 18444). No Docker was run on this machine.

```powershell
.\.venv-pad\Scripts\python.exe tools\hardening_local_stack.py postgres
.\.venv-pad\Scripts\python.exe tools\hardening_local_stack.py keycloak
.\.venv-pad\Scripts\python.exe tools\amfatec_stage.py prepare
.\.venv-pad\Scripts\python.exe tools\amfatec_stage.py engine     # own terminal
.\.venv-pad\Scripts\python.exe tools\amfatec_stage.py gateway    # own terminal
.\.venv-pad\Scripts\python.exe tools\hardening_m4_staging_checks.py

# then, with the gateway restarted declaring itself the proxy:
$env:AMFATEC_TRUSTED_PROXY = "127.0.0.1"
.\.venv-pad\Scripts\python.exe tools\amfatec_stage.py gateway    # own terminal
.\.venv-pad\Scripts\python.exe tools\hardening_m4_proxy_staging_check.py
```

| Run | Result |
|---|---|
| `hardening_m4_staging_checks.py` (declared `direct`) | **23/23**, was 21/21; the two new ones are the undeclared-proxy refusal and the liveness probe surviving it |
| `hardening_m4_proxy_staging_check.py` (simulated hop) | **6/6** |

The proxy run is the one that proves the defect is closed over the wire: one
client spends exactly 60 auth calls and is refused on the 61st, and a second
client behind the same hop is served normally on the next request.

## Preview stopgap — Traefik rate limiting and self-registration

> **Decided 20 September 2026: self-registration stays OPEN.** More testers are
> due to enroll, and closing it would mean creating each account by hand with
> `account.sh`. That is the user's call and it is the right trade for a preview
> that is still recruiting. It makes the Traefik rate limit the *whole* stopgap
> rather than half of it, so that half should not also be deferred.

**Yes to the rate limit. Registration stays open by decision.** Neither is a
substitute for this release. The preview is an
internet-reachable inference endpoint with open self-registration and, today, no
rate limit of any kind, because none of M4 is deployed. A Traefik rate-limit
middleware is a configuration change on the proxy that needs no image build, no
engine restart and no schema change, and it puts a per-source ceiling in front of
the sign-in surface within minutes — Traefik sees the real client address and is
the only thing in the stack that does until this code ships. Turning off
self-registration would remove the part of the attack surface that creates
durable state from an unauthenticated request, but it is staying on: more testers
are still to enroll, and the alternative is hand-creating accounts. That leaves
open registration as a known, accepted exposure for the duration of recruiting —
an unauthenticated request can still create a Keycloak account — which is
precisely why the rate limit should not wait. The
honest limitation is that Traefik's limiter is per-source only: it cannot see a
principal, a scan or a body deadline, so it does not close S01, S02's per-principal
half or S03 — it buys time for the release to be done properly rather than
rushed. **Unverified:** I have not read the preview's live Traefik configuration,
so I cannot say what middleware is already attached or what its current values are.

## What this does not establish

- **No deployment.** Everything here is local or on the owned loopback stack. The
  live preview still has no rate limit, no body deadline and no HSTS.
- **Synthetic traffic, real captures.** The capacity numbers come from four real
  recorded captures, which is a small sample on one machine. They are latency
  measurements, not a load test, and no biometric efficacy or attack-resistance
  claim is made anywhere here.
- **The deployed Traefik hop address is still unknown to this workspace.** It is a
  release-time input. The difference now is that getting it wrong stops the
  gateway or refuses the request instead of silently sharing one bucket.
- **The three M3 lifecycle bugs are untouched** — blocked jobs, duplicate face
  data, wrong reason code. They remain M5 exit criteria.
