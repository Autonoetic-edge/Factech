"""Path setup only, same as tests-hardening; no services, no stores."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".hardening-deps"))
sys.path.insert(0, str(ROOT / "packages/face-auth/src"))
