"""Load pure local policy without importing services or opening capture stores."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".hardening-deps"))
sys.path.insert(0, str(ROOT / "packages/face-auth/src"))
sys.path.insert(0, str(ROOT / "engine"))  # frozen `app` package for FrozenAnalyzer


def pytest_addoption(parser):
    parser.addoption(
        "--hardening-postgres",
        action="store_true",
        help="Use only the owned loopback synthetic PostgreSQL stack",
    )
    parser.addoption(
        "--hardening-keycloak",
        action="store_true",
        help="Use only the owned loopback synthetic Keycloak realm",
    )
