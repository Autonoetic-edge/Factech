# Replay and injection: what is defended, and what is not

Date: 20 September 2026. Milestone M4, acceptance ID **S06**. This document is a
statement of limits. It claims no measured attack resistance of any kind.

## The short version

The software controls in this repository stop a **replayed or duplicated request**.
They do not, and cannot, establish that the pixels in a FaceScan came from a live
person in front of a real camera. Every control below operates on bytes that the
client supplied. Nothing in the system attests to the origin of those bytes.

## What is actually defended

| Attack | Control | Where |
|---|---|---|
| Resubmitting a captured request body verbatim | Server-issued nonce, bound to actor, session, subject, generation and operation; consumed by one conditional UPDATE, expiry checked in the same statement | `operations.py` challenge claim, schema `challenges` |
| Retrying a mutation after an unclear outcome | Idempotency key scoped to actor/operation/subject with a canonical request digest; a different body under the same key is a 409 | `operations.py` |
| Replaying a completed operation to re-authorize | The saved result is returned marked as a replay, after current session and ownership checks; it consumes no new nonce and creates no new authentication event | `operations.py` |
| Replaying a session cookie after logout or revocation | Opaque server session, revoked locally before provider logout; every protected request rechecks session and provider token activity | `sessions.py` |
| Replaying a backchannel logout token | Issuer-scoped replay ID claimed atomically and retained through expiry | `postgres.py` |
| Flooding an endpoint to exhaust inference | Per-principal and per-source limits, bounded scan admission, streaming body cap | `limits.py`, `http.py` (M4) |
| A stale or oversized body held in memory | 481,964-byte streaming cap and a whole-body deadline at gateway and engine | `http.py` (M4) |

## What is NOT defended, and must not be claimed

1. **Camera attestation does not exist here.** The browser is an untrusted origin.
   A caller can replace `getUserMedia`, feed a virtual camera device, or skip the
   page entirely and post a FaceScan built by a script. Nothing in the request
   distinguishes a real camera from a synthesized stream. The passing nonce and
   challenge tests in `tests-hardening/` prove the *server's* single-use and
   binding logic. **They are not camera attestation and must never be cited as
   evidence of it.**

2. **Injection of a previously recorded genuine capture is only partly addressed.**
   A nonce stops the *same request* being submitted twice. An attacker who holds a
   genuine subject's video and can drive the capture flow live still produces a
   fresh, correctly-nonced request. Defeating that needs presentation-attack
   detection quality or a trusted capture path, not a nonce.

3. **These are not measured PAD efficacy.** The engine runs a two-model PAD gate
   and head-sequence and liveness heuristics, frozen and unchanged by M4. No
   genuine/impostor/print/screen-photo/screen-video/injection evaluation has been
   run by this track. Acceptance **Q05** is open and team-owned. Any number quoted
   for attack resistance today would be fabricated.

4. **A printed photo or a replayed screen held by a live attacker who performs the
   head-turn challenge is outside what the anti-replay controls cover.** This is
   the known gap recorded in the project vocabulary: anti-replay is not anti-spoof.

5. **Rate limits are availability controls, not identity controls.** They raise the
   cost of automated guessing; they do not make a forged capture fail.

6. **The M4 limiters are process-local.** One gateway and one engine process run
   per host today. A second replica would halve the effective limit's precision
   until the counters move to shared state. Recorded as a known ceiling in
   `limits.py`.

## What would change this

Only evidence would. In rough order of value:

1. Team-run physical evaluation under the frozen policy with held-out data,
   stratified by device and condition, reporting uncertainty (acceptance Q05).
2. A trusted capture path — a native or attested client — if the deployment ever
   needs to assert that frames came from a device rather than a script. That is a
   product decision with its own cost, not a backlog item.
3. Injection testing with a virtual camera against the deployed page, recorded as
   its own result and never merged with genuine-attempt denominators.

Until then the honest statement is: **the system resists replayed requests; it has
no evidence about resisting a presented or injected face.**
