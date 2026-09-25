r"""The trusted-proxy source rule over real HTTPS, through a simulated proxy hop.

Needs the owned loopback gateway staged with itself declared as the proxy, so
every request arrives from a trusted peer and the `X-Forwarded-For` last hop is
what the limiter counts -- the shape the deployment has behind Traefik:

    set AMFATEC_TRUSTED_PROXY=127.0.0.1
    .\.venv-pad\Scripts\python.exe tools\amfatec_stage.py gateway   # own terminal
    .\.venv-pad\Scripts\python.exe tools\hardening_m4_proxy_staging_check.py

What it proves is the defect is closed: two client addresses behind one proxy
hold separate allowances, and a request the proxy layer cannot attribute is
refused instead of falling back to the peer every visitor shares.
"""

import json
import ssl
import sys
from pathlib import Path

ROOT = Path(r"E:\Factech")
sys.path[:0] = [
    str(ROOT / ".hardening-deps"),
    str(ROOT / "packages/face-auth/src"),
    str(ROOT / "tests-hardening"),
]
import httpx  # noqa: E402
from test_real_keycloak import ORIGIN, STATE  # noqa: E402

OUT = ROOT / "docs/hardening/evidence/m4-proxy-staging-checks.json"
AUTH_LIMIT = 60  # Limits.auth_source, per source per minute
tls = ssl.create_default_context(cafile=str(STATE / "tls.crt"))
checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "pass": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name, "" if ok else detail)


def spend(client, address, count):
    """`count` /auth/login calls attributed to `address` by the simulated hop."""
    return [
        client.get(
            ORIGIN + "/auth/login",
            headers={"X-Forwarded-For": f"203.0.113.9, {address}"},
        ).status_code
        for _ in range(count)
    ]


with httpx.Client(verify=tls, trust_env=False, timeout=30, follow_redirects=False) as c:
    r = c.get(ORIGIN + "/auth/login")
    check(
        "a trusted proxy sending no X-Forwarded-For is refused",
        r.status_code == 400 and r.json().get("error") == "UNTRUSTED_CONTEXT",
        f"{r.status_code} {r.text[:120]}",
    )

    r = c.get(ORIGIN + "/auth/login", headers={"X-Forwarded-For": ","})
    check(
        "an empty X-Forwarded-For is refused, not treated as the peer",
        r.status_code == 400 and r.json().get("error") == "UNTRUSTED_CONTEXT",
        f"{r.status_code} {r.text[:120]}",
    )

    r = c.get(ORIGIN + "/health")
    check("the liveness probe still answers", r.status_code == 200, r.status_code)

    # One client spends its whole per-source auth allowance. The forged entry to
    # the left of the proxy's own is what an attacker would send; it is ignored.
    first = spend(c, "198.51.100.7", AUTH_LIMIT + 1)
    check(
        "one client can spend exactly its own allowance",
        first.count(302) == AUTH_LIMIT and first[-1] == 429,
        f"302={first.count(302)} last={first[-1]}",
    )

    # The defect: before this fix, the line above would have spent the allowance
    # for every visitor behind the proxy, because all of them were one source.
    second = c.get(
        ORIGIN + "/auth/login", headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.8"}
    )
    check(
        "a second client behind the same proxy is unaffected",
        second.status_code == 302,
        f"{second.status_code} {second.text[:120]}",
    )
    forged = c.get(
        ORIGIN + "/auth/login",
        headers={"X-Forwarded-For": "198.51.100.8, 198.51.100.7"},
    )
    check(
        "a forged left-hand entry cannot spend another client's allowance",
        forged.status_code == 429,
        f"{forged.status_code} {forged.text[:120]}",
    )

out = {
    "stack": "owned loopback gateway 18444, staged with AMFATEC_TRUSTED_PROXY=127.0.0.1",
    "hop": "X-Forwarded-For: <forged>, <client>  -- the rightmost entry is the proxy's",
    "auth_window_per_source_per_minute": AUTH_LIMIT,
    "passed": sum(1 for c_ in checks if c_["pass"]),
    "total": len(checks),
    "checks": checks,
}
OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(f"\n{out['passed']}/{out['total']} proxy staging checks passed")
