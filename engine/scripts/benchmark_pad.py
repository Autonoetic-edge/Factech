"""CPU resource measurement on public upstream sample images, never VPS captures."""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import anti_spoof, main  # noqa: E402
from app.detect import align_largest_face, decode_jpeg  # noqa: E402
from app.embed import embed_aligned  # noqa: E402


def main_benchmark():
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", type=Path)
    args = parser.parse_args()
    start = time.perf_counter()
    main.warm_models()
    warm_ms = (time.perf_counter() - start) * 1000
    results = []
    for name in ("image_F1.jpg", "image_F2.jpg", "image_T1.jpg"):
        image = cv2.imread(str(args.samples / name))
        scale = 480 / max(image.shape[:2])
        image = cv2.resize(
            image, (round(image.shape[1] * scale), round(image.shape[0] * scale))
        )
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 62])
        assert ok
        frames = [
            {"jpeg_bytes": encoded.tobytes(), "ts_ms": i * 700} for i in range(12)
        ]
        started = time.perf_counter()
        detections = main._scan_detections({"frames": frames})
        diag, _ = anti_spoof.evaluate(
            frames,
            detections,
            lambda f, d: embed_aligned(align_largest_face(f["jpeg_bytes"], d)),
            decode_jpeg,
        )
        results.append(
            {
                "sample": name,
                "detected": sum(bool(d) for d in detections),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "pad": diag,
            }
        )
    try:
        import resource

        memory = {
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        }
    except ImportError:
        import psutil

        memory = {"rss_mib": psutil.Process().memory_info().rss / 1024**2}
    print(
        json.dumps(
            {
                "warm_ms": round(warm_ms, 2),
                **memory,
                "note": "Public upstream still images repeated for workload; not physical efficacy validation",
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main_benchmark()
