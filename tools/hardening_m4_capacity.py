r"""Measure what the frozen engine actually sustains, so the M4 limits are numbers
and not guesses.

Two measurements:

* **scan capacity** — real recorded captures driven through the frozen pipeline
  in process, exactly as `facetech_auth.inference.FrozenAnalyzer` runs it in the
  engine. Serial latency first, then the gateway's admission bound fired at once,
  which shows what the last admitted caller waits.
* **auth throughput** — unauthenticated `/auth/login` over real HTTPS against the
  owned loopback gateway, with the per-source auth window widened for the run so
  the measurement is of the service and not of the limiter. Needs the stack up.

Reads recorded captures read-only from outside the repository and writes only
timings, byte counts and frame counts: no frame, embedding, template, subject id
or verdict detail leaves this tool.

Run from E:\Factech:

    .\.venv-pad\Scripts\python.exe tools\hardening_m4_capacity.py scans
    .\.venv-pad\Scripts\python.exe tools\hardening_m4_capacity.py auth
"""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / ".hardening-deps"),
    str(ROOT / "packages/face-auth/src"),
    str(ROOT / "tests-hardening"),
]
CAPTURES = Path("E:/facetech-vps-data/2026-09-20/scans/eval-d36fa4946ddf/bona_fide")
OUT = ROOT / "docs/hardening/evidence/m4-capacity.json"


def captures():
    """Recorded captures the live engine accepted on the current challenge policy.

    Returns (scan bytes, per-call challenge record, frame count, the live
    engine's own recorded timing for that same capture).
    """
    chosen = []
    for meta_path in sorted(CAPTURES.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        challenge = meta["decision_json"].get("challenge") or {}
        if meta.get("status_code") != 200 or challenge.get("action") != "HEAD_SEQUENCE":
            continue  # MOVE_CLOSER captures predate the policy the engine now enforces
        chosen.append(
            (
                (meta_path.parent / "scan.msgpack").read_bytes(),
                {
                    "action": challenge["action"],
                    "params": challenge["params"],
                    "age_ms": challenge["params"]["switch_ms"] + 12_000,
                    "subject": "capacity-probe",
                },
                meta["frame_count"],
                meta["decision_json"]["timing_ms"]["total"],
            )
        )
    if not chosen:
        raise SystemExit(f"no accepted capture found under {CAPTURES}")
    return chosen


def scans():
    from facetech_auth.inference import FrozenAnalyzer

    analyzer = FrozenAnalyzer(str(ROOT / "engine"))
    work = captures()

    async def one(index, raw, challenge):
        start = time.perf_counter()
        result = await analyzer.analyze(
            "enroll", raw, (), request_id=f"capacity-{index}", challenge=challenge
        )
        return (time.perf_counter() - start) * 1000, result.accepted, result.error

    async def serial():
        return [await one(i, raw, ch) for i, (raw, ch, *_) in enumerate(work)]

    async def burst(width):
        start = time.perf_counter()
        results = await asyncio.gather(
            *(
                one(i, work[i % len(work)][0], work[i % len(work)][1])
                for i in range(width)
            )
        )
        return (time.perf_counter() - start) * 1000, results

    asyncio.run(serial())  # discard: first pass pays lazy allocation
    measured = asyncio.run(serial())
    ok = [ms for ms, accepted, _ in measured if accepted]
    rejected = sorted({error for _, accepted, error in measured if not accepted})
    from app.main import BUSY_RETRY_AFTER_S, MAX_QUEUED_SCANS

    widths = {}
    for width in (1, 5, 6):
        total, results = asyncio.run(burst(width))
        widths[width] = {
            "wall_ms": round(total, 1),
            "slowest_caller_ms": round(max(ms for ms, _, _ in results), 1),
            "accepted": sum(1 for _, accepted, _ in results if accepted),
        }
    return {
        "captures_used": len(work),
        "frames_per_capture": sorted({item[2] for item in work}),
        "bytes_per_capture": sorted({len(item[0]) for item in work}),
        "live_engine_recorded_total_ms": sorted(item[3] for item in work),
        "accepted": len(ok),
        "rejected_codes": rejected,
        "serial_ms": {
            "median": round(statistics.median(ok), 1) if ok else None,
            "min": round(min(ok), 1) if ok else None,
            "max": round(max(ok), 1) if ok else None,
        },
        "concurrent": widths,
        "engine_admission": {
            "workers": 1,
            "max_queued": MAX_QUEUED_SCANS,
            "admitted_total": MAX_QUEUED_SCANS + 1,
            "busy_retry_after_s": BUSY_RETRY_AFTER_S,
        },
    }


def auth():
    """What the gateway actually serves per second on the two limited paths.

    Ordered so the auth flood runs last: it spends that source's whole auth
    window, and everything before it needs the window intact.
    """
    import ssl

    import httpx
    from test_real_keycloak import ORIGIN, STATE, login

    tls = ssl.create_default_context(cafile=str(STATE / "tls.crt"))
    values = json.loads((STATE / "synthetic-secrets.json").read_text("utf-8"))
    result = {}
    with httpx.Client(
        verify=tls, trust_env=False, timeout=20, follow_redirects=False
    ) as c:
        before = time.perf_counter()
        session, _ = login(c, values, "alice")
        result["sign_in"] = {
            "seconds": round(time.perf_counter() - before, 2),
            "auth_window_calls": 2,  # /auth/login + /auth/callback
            "note": "a full OIDC sign-in against the owned Keycloak",
        }

        # Per-principal request window: authenticated, cheap, no state change.
        head = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]}
        codes, start = [], time.perf_counter()
        while 429 not in codes and len(codes) < 400:
            codes.append(
                c.get(
                    ORIGIN + "/v2/receipts/receipt-capacity-probe", headers=head
                ).status_code
            )
        elapsed = time.perf_counter() - start
        result["authenticated_requests"] = {
            "served_before_refusal": len(codes) - 1,
            "elapsed_s": round(elapsed, 2),
            "served_per_second": round((len(codes) - 1) / elapsed, 1),
            "refused_429": codes.count(429),
        }

        # Per-source unauthenticated auth window. Last: it spends the window.
        codes, start = [], time.perf_counter()
        while 429 not in codes and len(codes) < 400:
            codes.append(c.get(ORIGIN + "/auth/login").status_code)
        elapsed = time.perf_counter() - start
        result["auth_requests"] = {
            "served_before_refusal": codes.count(302),
            "elapsed_s": round(elapsed, 2),
            "served_per_second": round(codes.count(302) / elapsed, 1),
            "refused_429": codes.count(429),
        }
    return result


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action not in {"scans", "auth"}:
        raise SystemExit(__doc__)
    record = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    record[action] = scans() if action == "scans" else auth()
    record["machine"] = "E:\\Factech dev laptop, owned loopback stack, no Docker"
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record[action], indent=2))
