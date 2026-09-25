import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"

RELEASE = "https://github.com/deepinsight/insightface/releases/download/v0.7"

MODELS = [
    {
        "file": "det_500m.onnx",
        "url": f"{RELEASE}/buffalo_sc.zip",
        "zip_size": 14_969_382,
        "min_size": 2_000_000,
        "sha256": "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a",
        "desc": "SCRFD 500M face detection (buffalo_sc)",
    },
    {
        "file": "w600k_r50.onnx",
        "url": f"{RELEASE}/buffalo_l.zip",
        "zip_size": 288_621_354,
        "min_size": 150_000_000,
        "sha256": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
        "desc": "ArcFace w600k_r50 embedding (buffalo_l; S3)",
    },
]


def mb(n: float) -> str:
    return f"{n / 1_000_000:.1f} MB"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key() -> str:
    joined = "".join(f"{m['file']}:{m['sha256']}\n" for m in MODELS)
    return "facetech-models-v1-" + hashlib.sha256(joined.encode()).hexdigest()[:16]


def fetch(model: dict, dry_run: bool) -> None:
    target = MODELS_DIR / model["file"]
    if target.is_file():
        actual = sha256_of(target)
        if actual == model["sha256"]:
            print(f"[skip] {model['file']} present (sha256 matches pin)")
            return
        sys.exit(
            f"ERROR: {target} exists but sha256 {actual} != pinned "
            f"{model['sha256']} - delete the file and rerun"
        )
    print(
        f"[get ] {model['file']} - {model['desc']} - "
        f"zip {mb(model['zip_size'])} from {model['url']}"
    )
    if dry_run:
        return
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="facetech-models-") as tmp:
        zip_path = Path(tmp) / "pack.zip"
        with (
            urllib.request.urlopen(model["url"], timeout=300) as resp,
            open(zip_path, "wb") as out,
        ):
            shutil.copyfileobj(resp, out)
        actual = zip_path.stat().st_size
        if actual != model["zip_size"]:
            sys.exit(
                f"ERROR: {model['file']} zip size {actual} != expected "
                f"{model['zip_size']} - release asset changed, refusing"
            )

        with (
            zipfile.ZipFile(zip_path) as zf,
            zf.open(model["file"]) as src,
            open(Path(tmp) / model["file"], "wb") as dst,
        ):
            shutil.copyfileobj(src, dst)
        extracted = Path(tmp) / model["file"]
        if extracted.stat().st_size < model["min_size"]:
            sys.exit(
                f"ERROR: extracted {model['file']} is "
                f"{extracted.stat().st_size} bytes (< {model['min_size']})"
            )
        actual = sha256_of(extracted)
        if actual != model["sha256"]:
            sys.exit(
                f"ERROR: extracted {model['file']} sha256 {actual} != pinned "
                f"{model['sha256']} - release asset changed, refusing"
            )
        shutil.move(str(extracted), str(target))
    size = target.stat().st_size
    print(f"[ok  ] {model['file']} {mb(size)} sha256={sha256_of(target)}")


def verify() -> None:
    print(f"models dir: {MODELS_DIR}")
    problems: list[str] = []
    for model in MODELS:
        target = MODELS_DIR / model["file"]
        if not target.is_file():
            problems.append(f"{model['file']}: MISSING from {MODELS_DIR}")
            continue
        size = target.stat().st_size
        if size < model["min_size"]:
            problems.append(
                f"{model['file']}: {size} bytes (< {model['min_size']}) - truncated"
            )
            continue
        actual = sha256_of(target)
        if actual != model["sha256"]:
            problems.append(
                f"{model['file']}: sha256 {actual} != pinned {model['sha256']}"
            )
            continue
        print(f"[ok  ] {model['file']} {mb(size)} sha256 matches pin")
    if problems:
        sys.exit(
            "ERROR: model verification failed - the engine cannot be built or "
            "tested without these files, and tests that need them must not be "
            "allowed to skip:\n  " + "\n  ".join(problems)
        )
    print(f"verified {len(MODELS)} model(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download or verify the pinned ONNX models."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan without downloading anything",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify pinned models are present and hash-correct; never downloads",
    )
    parser.add_argument(
        "--print-key",
        action="store_true",
        help="print the CI cache key for the pinned model set and exit",
    )
    args = parser.parse_args()
    if args.print_key:
        print(cache_key())
        return
    if args.verify:
        verify()
        return
    print(f"models dir: {MODELS_DIR}")
    for model in MODELS:
        fetch(model, args.dry_run)
    print("done")


if __name__ == "__main__":
    main()
