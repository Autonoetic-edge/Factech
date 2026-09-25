"""Create a source-hashed evaluation release; excludes local env and captures."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "pad-evidence"
paths = [ROOT / name for name in ("docker-compose.demo.yml", "Caddyfile.demo", ".dockerignore",
         "engine/Dockerfile", "engine/.dockerignore", "engine/requirements.txt")]
for directory in ("engine/app", "engine/models/anti_spoof", "mock-gateway", "apps", "packages/face-sdk/dist"):
    paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                 and not any(part in ("__pycache__", ".pytest_cache", ".ruff_cache", "tests") for part in p.parts)
                 and p.suffix not in (".pyc", ".db") and not p.name.startswith("e2e_"))
manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}
for name in ("det_500m.onnx", "w600k_r50.onnx"):
    path = ROOT / "engine/models" / name
    manifest[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
build = "eval-pad-" + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
OUT.mkdir(exist_ok=True)
(OUT / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
(OUT / "build-id.txt").write_text(build)
with tarfile.open(OUT / "release.tar.gz", "w:gz") as archive:
    for path in sorted(set(paths)):
        archive.add(path, arcname=path.relative_to(ROOT).as_posix())
    archive.add(OUT / "release-manifest.json", arcname="release-manifest.json")
print(json.dumps({"build": build, "files": len(manifest), "archive_bytes": (OUT / "release.tar.gz").stat().st_size}))
