"""Fetch pinned portable test tools into this workspace; no system install."""

import hashlib
import json
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".hardening-runtime"
ASSETS = (
    (
        "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.zip",
        "f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e",
    ),
    (
        "https://github.com/keycloak/keycloak/releases/download/26.7.4/keycloak-26.7.4.zip",
        "a286e98b4296d4e75ee88d8527c7cd463b307caa022f088c9f22cffccc741fa1",
    ),
    (
        "https://get.enterprisedb.com/postgresql/postgresql-17.11-4-windows-x64-binaries.zip",
        # Pinned received SHA256, not a separately published publisher checksum.
        "b9424ee7bc60b52450ff910a3630225df32e633f3cb29c1d126d9299d59aea28",
    ),
)


def fetch(asset):
    url, expected = asset
    path = DEST / "downloads" / url.rsplit("/", 1)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        request = urllib.request.Request(
            url, headers={"User-Agent": "Facetech-Local-Hardening"}
        )
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            path.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("Portable runtime hash mismatch: " + path.name)
    target = (DEST / "bin").resolve()
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if not (target / entry.filename).resolve().is_relative_to(target):
                raise ValueError("Unsafe archive member")
        archive.extractall(target)
    print("Verified/extracted " + path.name, flush=True)
    return {"url": url, "sha256": actual, "bytes": path.stat().st_size}


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, ASSETS))
    (ROOT / "docs/hardening/evidence/m1-portable-runtime.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
