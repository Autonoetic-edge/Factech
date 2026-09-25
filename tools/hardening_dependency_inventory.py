"""M4 S07: read-only dependency, image, mount and asset inventory.

Reads files and the local interpreter's installed distributions. Opens no socket,
downloads nothing, imports no service and touches no capture store. The output is
evidence input: the disposition of each finding is written by a human in
docs/hardening/evidence/M4.md, not guessed here.
"""

import importlib.metadata as md
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (
    "starlette", "httpx", "msgpack", "uvicorn", "numpy", "onnxruntime",
    "opencv-python-headless", "h11", "anyio", "certifi", "idna", "click",
    "Authlib", "joserfc", "cryptography", "SQLAlchemy", "alembic", "psycopg",
)  # fmt: skip
PINNED = re.compile(r"^([A-Za-z0-9_.\[\]-]+)\s*(==)\s*([^\s#]+)")
IMAGE = re.compile(r"^\s*(?:image:|FROM)\s+(\S+)", re.M)


def installed():
    found = {}
    for name in RUNTIME:
        try:
            found[name] = md.version(name)
        except md.PackageNotFoundError:
            found[name] = None
    return found


def requirement_lines(path):
    path = Path(path)
    if path.suffix == ".toml":
        return tomllib.loads(path.read_text("utf-8"))["project"]["dependencies"]
    return path.read_text("utf-8").splitlines()


def declared(path):
    """Every requirement, split into exactly pinned and not."""
    exact, loose = {}, []
    for line in requirement_lines(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PINNED.match(line)
        if match:
            exact[match.group(1)] = match.group(3)
        else:
            loose.append(line)
    return {"pinned": exact, "unpinned": loose}


def images(path):
    text = Path(path).read_text("utf-8")
    return [
        {"reference": ref, "digest_pinned": "@sha256:" in ref}
        for ref in IMAGE.findall(text)
    ]


def mounts(path):
    text = Path(path).read_text("utf-8")
    found = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line and "/" in line:
            value = line[2:].strip()
            if not value.startswith(("127.0.0.1", "--")):
                found.append({"mount": value, "read_only": value.endswith(":ro")})
    return found


def assets():
    """Sources whose notices and use permissions must be reviewed separately."""
    manifest = ROOT / "engine/models/anti_spoof/manifest.json"
    return {
        "recognition_and_detection": {
            "pins": "engine/scripts/download_models.py",
            "upstream": "InsightFace",
            "permission": "UNRESOLVED: MIT source code, separate pretrained model "
            "terms including noncommercial research restrictions",
        },
        "presentation_attack_detection": {
            "pins": "engine/models/anti_spoof/manifest.json",
            "present": manifest.is_file(),
            "permission": "UNRESOLVED: the repository's Apache-2.0 notice is not "
            "separately reviewed checkpoint rights",
        },
        "browser_face_guide": {
            "pins": "apps/vendor/fetch.sh",
            "upstream": "MediaPipe tasks-vision 0.10.14 + blaze_face_short_range",
            "permission": "UNREVIEWED here: JS, WASM and tflite weights each need "
            "their own notice",
        },
        "fonts": {
            "files": sorted(
                p.name for p in (ROOT / "apps/shared/fonts").glob("*.woff2")
            ),
            "notices": sorted(
                p.name for p in (ROOT / "apps/shared/fonts").glob("OFL-*")
            ),
            "permission": "OK: SIL Open Font License 1.1 text is shipped alongside",
        },
    }


def report():
    return {
        "installed_runtime": installed(),
        "declared": {
            "packages/face-auth/pyproject.toml": declared(
                ROOT / "packages/face-auth/pyproject.toml"
            ),
            "packages/face-auth/requirements-local.txt": declared(
                ROOT / "packages/face-auth/requirements-local.txt"
            ),
            "engine/requirements.txt": declared(ROOT / "engine/requirements.txt"),
        },
        "images": {
            "deploy/amfatec/Dockerfile": images(ROOT / "deploy/amfatec/Dockerfile"),
            "deploy/amfatec/compose.yml": images(ROOT / "deploy/amfatec/compose.yml"),
        },
        "mounts": {
            "deploy/amfatec/compose.yml": mounts(ROOT / "deploy/amfatec/compose.yml")
        },
        "assets": assets(),
        "scan": {
            "performed": False,
            "reason": "No vulnerability database and no network access in this "
            "workspace; pip-audit/trivy are not installed. A scan is a separate "
            "approved step before the next cutover.",
        },
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=2, sort_keys=True))
