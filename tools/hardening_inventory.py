"""Read-only route inventory; never import an app or open its capture store."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("engine/app/main.py",)
HTTP_DECORATORS = {"get", "post", "put", "patch", "delete", "head", "options"}


def explicit_routes(root: Path = ROOT) -> list[dict]:
    routes = []
    for source in SOURCES:
        tree = ast.parse((root / source).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                method = decorator.func.attr
                if method not in HTTP_DECORATORS | {"api_route"}:
                    continue
                path = ast.literal_eval(decorator.args[0])
                if method == "api_route":
                    methods = next(
                        ast.literal_eval(k.value)
                        for k in decorator.keywords
                        if k.arg == "methods"
                    )
                else:
                    methods = [method.upper()]
                for verb in methods:
                    routes.append({"source": source, "method": verb, "path": path})
    return sorted(routes, key=lambda r: (r["source"], r["path"], r["method"]))


def check_contract(root: Path = ROOT) -> list[str]:
    contract = json.loads((root / "docs/hardening/routes.json").read_text("utf-8"))
    documented = [
        {key: route[key] for key in ("source", "method", "path")}
        for route in contract["explicit_routes"]
    ]
    errors = []
    if documented != explicit_routes(root):
        errors.append("Explicit routes changed: review and update every route policy.")
    for route in contract["explicit_routes"]:
        if not route.get("hardened_policy") or not route.get("acceptance"):
            errors.append(f"Missing policy/acceptance mapping: {route['path']}")
    return errors


if __name__ == "__main__":
    errors = check_contract()
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {len(explicit_routes())} explicit method/path registrations mapped.")
    print("Static mounts and framework routes require separate runtime checks in M1.")
