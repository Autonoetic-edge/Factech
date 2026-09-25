import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONTRACT = REPO / "README.md"
ENGINE_ERRORS = REPO / "engine" / "app" / "errors.py"
ENGINE_APP = REPO / "engine" / "app"
MESSAGES = REPO / "apps" / "shared" / "messages.js"
CLIENT_FILES = sorted((REPO / "apps" / "shared").glob("*.js"))

CODE_RE = r"[A-Z][A-Z0-9_]{2,}"

DECL_RE = re.compile(rf'^({CODE_RE})\s*=\s*"\1"\s*$')

_ROW_RE = re.compile(
    rf"^\|\s*`({CODE_RE})`\s*\|\s*(engine|gateway)\s*\|\s*(active|reserved)\s*"
    r"\|\s*(\d{3})\s*\|",
    re.MULTILINE,
)


def _contract_table():
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index("### 3.1 Canonical error codes")
    end = text.index("### 3.2", start)
    rows = _ROW_RE.findall(text[start:end])
    assert rows, "parsed no rows out of contract §3.1 — has the table shape changed?"
    return {
        code: {"origin": origin, "status": status, "http": int(http)}
        for code, origin, status, http in rows
    }


TABLE = _contract_table()
CANONICAL = frozenset(TABLE)
ACTIVE = frozenset(c for c, r in TABLE.items() if r["status"] == "active")
RESERVED = CANONICAL - ACTIVE
BY_ORIGIN = {"engine": frozenset(c for c, r in TABLE.items() if r["origin"] == "engine")}

# mock-gateway (the only "gateway"-origin emitter this repo ever had) is gone; the
# production gateway is packages/face-auth, which declares no equivalent code (its
# own DEPENDENCY_UNAVAILABLE is a different vocabulary, not part of this contract).
# "gateway"-origin rows (currently just ENGINE_UNREACHABLE) are documented and may
# still be client-handled, but this file can no longer verify an in-repo emitter or
# HTTP-status mapping for them.
NO_IN_REPO_EMITTER = frozenset(c for c, r in TABLE.items() if r["origin"] != "engine")


def _load_engine_errors():
    spec = importlib.util.spec_from_file_location("_engine_errors", ENGINE_ERRORS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _load_engine_errors()


def _engine_source_files():
    return [p for p in sorted(ENGINE_APP.rglob("*.py")) if p != ENGINE_ERRORS]


def _engine_emitted():
    found = set()
    for path in _engine_source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            found.update(re.findall(rf"\berrors\.({CODE_RE})\b", line))
    return frozenset(found) & frozenset(ENGINE.ENGINE_CODES)


ENGINE_EMITTED = _engine_emitted()
EMITTED = ENGINE_EMITTED
EMITTED_BY_ORIGIN = {"engine": ENGINE_EMITTED}


def _client_handled():
    text = MESSAGES.read_text(encoding="utf-8")
    table = re.search(
        r"export const ENGINE_TEXT = Object\.freeze\(\{(.*?)\}\);", text, re.DOTALL
    )
    assert table, f"ENGINE_TEXT not found in {MESSAGES.name} — has its shape changed?"
    found = set(re.findall(rf"^\s*({CODE_RE}):", table.group(1), re.MULTILINE))
    for path in CLIENT_FILES:
        found |= {
            a or b
            for a, b in re.findall(
                rf"""engineCode\s*(?:===|!==|==|!=)\s*['"]({CODE_RE})['"]"""
                rf"""|['"]({CODE_RE})['"]\s*(?:===|!==|==|!=)\s*[\w.]*engineCode""",
                path.read_text(encoding="utf-8"),
            )
        }
    assert found, f"extracted no codes from {MESSAGES.name} — has its shape changed?"
    return frozenset(found)


CLIENT_HANDLED = _client_handled()


def test_every_client_code_is_emitted_somewhere():
    dead = CLIENT_HANDLED - EMITTED - NO_IN_REPO_EMITTER
    assert not dead, (
        f"the client handles {sorted(dead)}, which the engine never emits — "
        f"those branches can never run. Engine emits {sorted(ENGINE_EMITTED)}. "
        f"Fix the client, or add the emitter and its §3.1 row."
    )


def test_client_codes_are_canonical():
    unknown = CLIENT_HANDLED - CANONICAL
    assert not unknown, (
        f"the client handles {sorted(unknown)}, absent from the contract "
        f"§3.1 table. Add a row (with Emitted by + Status), or use a listed code."
    )


def test_client_does_not_branch_on_reserved_codes():
    premature = CLIENT_HANDLED & RESERVED
    assert not premature, (
        f"the client handles reserved code(s) {sorted(premature)}. A "
        f"reserved row is documented but not emitted yet, so the branch is "
        f"dead; flip the row to `active` in §3.1 once an emitter lands."
    )


def test_declared_codes_match_the_contract():
    declared = frozenset(ENGINE.ENGINE_CODES)
    expected = BY_ORIGIN["engine"]
    assert declared == expected, (
        f"the engine declares {sorted(declared)} but contract §3.1 lists "
        f"{sorted(expected)} for it. Undeclared: {sorted(expected - declared)}; "
        f"undocumented: {sorted(declared - expected)}."
    )


def test_emitted_codes_come_from_the_component_the_contract_names():
    strays = EMITTED_BY_ORIGIN["engine"] - BY_ORIGIN["engine"]
    assert not strays, (
        f"the engine emits {sorted(strays)}, which §3.1 attributes to another "
        f"component (or does not list at all)."
    )


def test_active_codes_are_actually_emitted():
    missing = sorted(
        code
        for code in ACTIVE
        if TABLE[code]["origin"] == "engine" and code not in EMITTED_BY_ORIGIN["engine"]
    )
    assert not missing, (
        f"§3.1 marks {missing} `active`, but no emission site references them. "
        f"Either implement the emitter or mark the row `reserved`."
    )


def test_reserved_codes_are_not_emitted():
    leaked = sorted(RESERVED & EMITTED)
    assert not leaked, (
        f"§3.1 marks {leaked} `reserved`, but they are emitted. Promote the "
        f"row to `active` so clients may branch on it."
    )


def test_http_statuses_match_the_contract():
    for code in sorted(BY_ORIGIN["engine"]):
        assert ENGINE.HTTP_STATUS[code] == TABLE[code]["http"], (
            f"{code}: errors.HTTP_STATUS says {ENGINE.HTTP_STATUS[code]}, "
            f"contract §3.1 says {TABLE[code]['http']}."
        )


def test_emitters_use_constants_not_string_literals():
    offenders = []
    for path in _engine_source_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DECL_RE.match(line.strip()):
                continue
            offenders += [
                f"{path.relative_to(REPO).as_posix()}:{n}: {lit!r}"
                for lit in re.findall(rf"""['"]({CODE_RE})['"]""", line)
                if lit in CANONICAL
            ]
    assert not offenders, (
        "error codes hardcoded as string literals instead of constants:\n  "
        + "\n  ".join(offenders)
    )


RETIRED = "ENGINE_UNAVAILABLE"

_RETIRED_USE_RE = re.compile(rf"""['"]{RETIRED}['"]|\b{RETIRED}\b\s*[=:]""")


def test_the_retired_name_stays_retired():
    assert RETIRED not in CANONICAL, (
        f"{RETIRED} is back as a contract §3.1 row. It reads as a synonym of "
        f"both MODEL_UNAVAILABLE and ENGINE_UNREACHABLE and describes neither."
    )
    hits = [
        f"{path.relative_to(REPO).as_posix()}:{n}"
        for path in [*CLIENT_FILES, *_engine_source_files()]
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _RETIRED_USE_RE.search(line)
    ]
    assert not hits, (
        f"{RETIRED} is retired (contract §3.2) — use ENGINE_UNREACHABLE "
        f"(engine never answered) or MODEL_UNAVAILABLE (engine answered, "
        f"models missing). Found at: {hits}"
    )
