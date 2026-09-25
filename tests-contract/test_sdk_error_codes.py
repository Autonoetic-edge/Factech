"""FIX_PLAN 2A.3: the SDK's ERROR_CODES map agrees with every emitter and copy."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_error_codes import ENGINE, TABLE

REPO = Path(__file__).resolve().parent.parent
SDK_ERRORS = REPO / "packages" / "face-sdk" / "src" / "errors.ts"
SDK_TYPES = REPO / "packages" / "face-sdk" / "src" / "types.ts"
FACE_AUTH = REPO / "packages" / "face-auth" / "src" / "facetech_auth"
MESSAGES = REPO / "apps" / "shared" / "messages.js"
README = REPO / "README.md"
NODE = shutil.which("node")

CODE = r"[A-Z][A-Z0-9_]{2,}"


@pytest.fixture(scope="module")
def sdk():
    if NODE is None:
        pytest.skip("node not found on PATH")
    script = (
        f"const m = await import({json.dumps(SDK_ERRORS.as_uri())});"
        "console.log(JSON.stringify({codes: m.ERROR_CODES, text: m.ERROR_TEXT}));"
    )
    out = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


def _face_auth_emitted():
    """code -> set of HTTP statuses (None where the site gives no literal status)."""
    found: dict[str, set] = {}
    for path in sorted(FACE_AUTH.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for code, status in re.findall(
            rf'Denied\(\s*"({CODE})"\s*(?:,\s*(\d{{3}}))?', text
        ):
            found.setdefault(code, set()).add(int(status) if status else 401)
        if re.search(r"Denied\(\)", text):
            found.setdefault("AUTHENTICATION_REQUIRED", set()).add(401)
        for code in re.findall(rf'"error":\s*"({CODE})"', text):
            found.setdefault(code, set()).add(None)
        # contracts.Unavailable: a Denied subclass with its own code and status.
        for code, status in re.findall(
            rf'super\(\).__init__\(\s*"({CODE})",\s*(\d{{3}})', text
        ):
            found.setdefault(code, set()).add(int(status))
    assert len(found) >= 20, f"parsed only {len(found)} face-auth codes"
    return found


def test_every_contract_row_is_in_the_map(sdk):
    for code, row in TABLE.items():
        assert code in sdk["codes"], code
        entry = sdk["codes"][code]
        assert (entry["origin"], entry["http"]) == (row["origin"], row["http"]), code


def test_engine_codes_match_the_engine(sdk):
    engine = {c for c, e in sdk["codes"].items() if e["origin"] == "engine"}
    assert engine == set(ENGINE.ENGINE_CODES)
    for code in ENGINE.ENGINE_CODES:
        assert sdk["codes"][code]["http"] == ENGINE.HTTP_STATUS[code], code


def test_face_auth_codes_are_all_in_the_map(sdk):
    for code, statuses in sorted(_face_auth_emitted().items()):
        assert code in sdk["codes"], f"face-auth emits {code}, absent from ERROR_CODES"
        entry = sdk["codes"][code]
        assert entry["origin"] in {"face-auth", "engine"}, code
        for status in statuses - {None}:
            assert entry["http"] == status, f"{code}: face-auth sends {status}"


def test_sdk_codes_are_exactly_the_failure_code_union(sdk):
    ts = SDK_TYPES.read_text(encoding="utf-8")
    union = re.search(r"export type FailureCode =([\s\S]*?);", ts)
    codes = set(re.findall(r"'([A-Z_]+)'", union.group(1)))
    assert {c for c, e in sdk["codes"].items() if e["origin"] == "sdk"} == codes


def test_messages_js_text_matches_the_sdk_default_text(sdk):
    """messages.js keeps its own ENGINE_TEXT / SDK_TEXT (the protected face-guide test pins
    that file import-free), so the copy is held equal to the SDK's ERROR_TEXT here."""
    text = MESSAGES.read_text(encoding="utf-8")
    for table in ("ENGINE_TEXT", "SDK_TEXT"):
        body = re.search(
            rf"export const {table} = Object\.freeze\(\{{(.*?)\}}\);", text, re.S
        ).group(1)
        for code, sentence in re.findall(rf"^\s*({CODE}): '([^']*)',", body, re.M):
            assert sdk["text"].get(code) == sentence, code


def test_the_resolved_codes_are_written_in_the_readme(sdk):
    md = README.read_text(encoding="utf-8")
    start = md.index("### 3.3 Shared error-code map")
    section = md[start : md.index("\n## ", start)]
    rows = {
        m[0]: m[1:]
        for m in re.findall(
            rf"^\|\s*`({CODE})`\s*\|\s*([a-z-]+)\s*\|\s*(\d{{3}}|—)\s*\|\s*(yes|no)\s*\|"
            r"\s*(yes|no)\s*\|\s*([a-z-]+)\s*\|",
            section,
            re.M,
        )
    }
    for code in (
        "LOW_QUALITY",
        "CHALLENGE_FAIL",
        "PAYLOAD_TOO_LARGE",
        "MODEL_UNAVAILABLE",
        "UNAUTHORIZED",
        "ENGINE_UNREACHABLE",
        "CHALLENGE_INVALID",
    ):
        assert code in rows, f"README §3.3 has no row for {code}"
        e = sdk["codes"][code]
        origin, http, retry, attempt, stage = rows[code]
        assert origin == e["origin"] and int(http) == e["http"], code
        assert (retry == "yes", attempt == "yes", stage) == (
            e["retry"],
            e["attempt"],
            e["stage"],
        ), code
