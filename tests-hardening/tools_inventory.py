"""Import shim: the S07 inventory tool lives in tools/, which is not a package."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "tools/hardening_dependency_inventory.py"
_spec = importlib.util.spec_from_file_location("hardening_dependency_inventory", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

report = _module.report
