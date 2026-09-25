"""M4 controls over real HTTPS against the owned loopback staging stack."""

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
from facetech_auth.limits import BODY_LIMIT  # noqa: E402
from test_real_keycloak import ORIGIN, STATE, login  # noqa: E402

ENGINE = "https://127.0.0.1:18445"
OUT = ROOT / "docs/hardening/evidence/m4-staging-checks.json"
tls = ssl.create_default_context(cafile=str(STATE / "tls.crt"))
values = json.loads((STATE / "synthetic-secrets.json").read_text("utf-8"))
checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "pass": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name, "" if ok else detail)


def chunks(total, size=64 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b"\0" * n


# 1. Security headers on real responses from both services and the page.
with httpx.Client(verify=tls, trust_env=False, timeout=20, follow_redirects=False) as c:
    for label, url in (
        ("gateway /health", ORIGIN + "/health"),
        ("engine /health", ENGINE + "/health"),
        ("page /", ORIGIN + "/"),
    ):
        r = c.get(url)
        h = {k.lower(): v for k, v in r.headers.items()}
        check(
            f"{label}: HSTS",
            h.get("strict-transport-security") == "max-age=31536000; includeSubDomains",
            h.get("strict-transport-security"),
        )
        check(f"{label}: nosniff", h.get("x-content-type-options") == "nosniff")
        cors = [k for k in h if k.startswith("access-control-")]
        check(f"{label}: no CORS header", not cors, cors)
        check(
            f"{label}: no server banner",
            "uvicorn" not in h.get("server", "").lower(),
            h.get("server"),
        )

# 2. An unauthenticated oversized upload never reaches the body reader.
with httpx.Client(verify=tls, trust_env=False, timeout=60, follow_redirects=False) as c:
    r = c.post(
        ORIGIN + "/v2/subjects/subject-x/enroll",
        content=chunks(32 * 1024 * 1024),
        headers={"Content-Type": "application/msgpack", "Idempotency-Key": "0" * 32},
    )
    check("unauthenticated 32 MB upload refused", r.status_code == 401, r.status_code)
    check(
        "refused as unauthenticated, not parsed",
        r.json().get("error") == "AUTHENTICATION_REQUIRED",
        r.text[:120],
    )

# 3. Authenticated: real chunked oversized body, and the per-principal scan limit.
with httpx.Client(verify=tls, trust_env=False, timeout=60, follow_redirects=False) as c:
    session, _ = login(c, values, "alice")
    subject, csrf = session.get("subject_id"), session["csrf_token"]
    head = {
        "Content-Type": "application/msgpack",
        "Origin": ORIGIN,
        "X-CSRF-Token": csrf,
    }
    r = c.post(
        f"{ORIGIN}/v2/subjects/{subject}/enroll",
        content=chunks(32 * 1024 * 1024),
        headers={**head, "Idempotency-Key": "a" * 32},
    )
    check(
        "authenticated 32 MB chunked upload -> 413", r.status_code == 413, r.text[:200]
    )
    check(
        "413 body carries the code only",
        set(r.json()) == {"error", "request_id"}
        and r.json()["error"] == "PAYLOAD_TOO_LARGE",
        r.text[:200],
    )

    codes = []
    for i in range(13):
        r = c.post(
            f"{ORIGIN}/v2/subjects/{subject}/enroll",
            content=b"\x80",
            headers={**head, "Idempotency-Key": f"{i:032d}"},
        )
        codes.append(r.status_code)
        if r.status_code == 429:
            check(
                "scan-limit 429 carries Retry-After",
                r.headers.get("Retry-After", "").isdigit(),
                r.headers.get("Retry-After"),
            )
            break
    check("per-principal scan limit trips over real HTTP", 429 in codes, codes)

# 3b. This gateway is staged as "direct" (no proxy declared), so a forwarded
# header is a caller claiming a hop this server never saw. It is refused, not
# quietly counted against a bucket every visitor would share.
with httpx.Client(verify=tls, trust_env=False, timeout=20, follow_redirects=False) as c:
    r = c.get(ORIGIN + "/auth/login", headers={"X-Forwarded-For": "198.51.100.7"})
    check(
        "X-Forwarded-For from an undeclared proxy is refused",
        r.status_code == 400 and r.json().get("error") == "UNTRUSTED_CONTEXT",
        f"{r.status_code} {r.text[:120]}",
    )
    r = c.get(ORIGIN + "/health", headers={"X-Forwarded-For": "198.51.100.7"})
    check(
        "the liveness probe is not refused by that gate",
        r.status_code == 200,
        r.status_code,
    )


# 4. The unauthenticated auth surface. Last: it exhausts that window for a minute.
with httpx.Client(verify=tls, trust_env=False, timeout=20, follow_redirects=False) as c:
    codes, retry = [], None
    for _ in range(70):
        r = c.get(ORIGIN + "/auth/login")
        codes.append(r.status_code)
        if r.status_code == 429:
            retry = r.headers.get("Retry-After")
            break
    check("auth surface rate limits", 429 in codes, codes)
    # The sign-in above already spent two calls (login + callback) of this
    # source's 60-per-minute auth allowance, so 58 remain.
    check(
        "auth bound is the configured 60 per source",
        codes.count(302) == 58,
        codes.count(302),
    )
    check("auth 429 carries Retry-After", retry is not None and retry.isdigit(), retry)

out = {
    "stack": "owned loopback: postgres 15432, keycloak 18443, engine 18445, gateway 18444",
    "entrypoint": "tools/amfatec_stage.py -> facetech_auth.serve (the deployable path)",
    "body_limit_bytes": BODY_LIMIT,
    "passed": sum(1 for c_ in checks if c_["pass"]),
    "total": len(checks),
    "checks": checks,
}
OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(f"\n{out['passed']}/{out['total']} staging checks passed")
