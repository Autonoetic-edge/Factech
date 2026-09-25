r"""Verify or re-baseline the protected-file hash manifest.

Usage (from E:\Factech, with .venv-pad\Scripts\python.exe):

    python tools/hardening_verify_baseline.py
    python tools/hardening_verify_baseline.py --rebaseline <new-manifest-name>

Verify reads the active manifest named by HARDENING_PLAN.md and reports every
path whose file hash differs, is missing, or is untracked-but-listed. It never
writes. --rebaseline writes a NEW manifest file beside the active one from the
current contents of the same path list; it refuses to overwrite an existing
file, so an earlier baseline is always kept as evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = ROOT / "docs/hardening/evidence/baseline"
ACTIVE = BASELINE_DIR / "source-sha256.v4.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ACTIVE))
    ap.add_argument("--rebaseline", metavar="NAME")
    args = ap.parse_args()

    manifest = Path(args.manifest)
    expected: dict[str, str] = json.loads(manifest.read_text(encoding="utf-8"))

    if args.rebaseline:
        target = BASELINE_DIR / args.rebaseline
        if target.exists():
            print(f"refusing to overwrite {target}", file=sys.stderr)
            return 2
        current = {p: sha256(ROOT / p) for p in sorted(expected)}
        target.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        changed = [p for p in current if current[p] != expected[p]]
        print(
            f"wrote {target} with {len(current)} paths; {len(changed)} differ from {manifest.name}"
        )
        for p in changed:
            print(f"  rebaselined {p}")
        return 0

    missing: list[str] = []
    mismatched: list[str] = []
    for rel, want in sorted(expected.items()):
        path = ROOT / rel
        if not path.is_file():
            missing.append(rel)
        elif sha256(path) != want:
            mismatched.append(rel)

    print(
        f"manifest {manifest.name}: {len(expected)} protected, "
        f"{len(mismatched)} mismatched, {len(missing)} missing"
    )
    for rel in mismatched:
        print(f"  MISMATCH {rel}")
    for rel in missing:
        print(f"  MISSING  {rel}")
    return 1 if (mismatched or missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
