"""B01/M6 source drift guard; does not assert that HTTP auth is enforced."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hardening_inventory", ROOT / "tools/hardening_inventory.py"
)
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_every_explicit_route_has_a_reviewed_policy():
    assert inventory.check_contract() == []


def test_new_route_cannot_silently_escape_the_inventory(tmp_path):
    for source in inventory.SOURCES:
        destination = tmp_path / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((ROOT / source).read_text("utf-8"), encoding="utf-8")
    contract = tmp_path / "docs/hardening/routes.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        (ROOT / "docs/hardening/routes.json").read_text("utf-8"), encoding="utf-8"
    )
    target = tmp_path / "engine/app/main.py"
    with target.open("a", encoding="utf-8") as stream:
        stream.write('\n@app.post("/v2/unreviewed")\ndef unreviewed():\n    pass\n')
    assert inventory.check_contract(tmp_path)


def test_missing_policy_is_rejected(tmp_path):
    for source in inventory.SOURCES:
        destination = tmp_path / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((ROOT / source).read_text("utf-8"), encoding="utf-8")
    contract = json.loads((ROOT / "docs/hardening/routes.json").read_text("utf-8"))
    contract["explicit_routes"][0]["hardened_policy"] = ""
    target = tmp_path / "docs/hardening/routes.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(contract), encoding="utf-8")
    assert inventory.check_contract(tmp_path)
