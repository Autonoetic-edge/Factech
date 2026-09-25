from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

import cv2  # noqa: E402

from app import detect, embed  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".ppm"}

SDK_JPEG_QUALITY = 62

THRESHOLD_SWEEP = [t / 100.0 for t in range(30, 81)]
TARGET_FARS = [1e-3, 1e-4, 1e-5, 1e-6]
DEPLOY_TARGET_FAR = 1e-4

HIST_LO, HIST_HI, HIST_STEP = -1.0, 1.0, 0.01

MODEL_NAME = "w600k_r50"
TRAINING_SET = "WebFace600K"


def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps, max_iter = 1e-300, 3e-16, 500
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return (
        1.0
        - math.exp(
            math.lgamma(a + b)
            - math.lgamma(a)
            - math.lgamma(b)
            + b * math.log1p(-x)
            + a * math.log(x)
        )
        * _betacf(b, a, 1.0 - x)
        / b
    )


def _betainv(p: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    k = int(k)
    lower = 0.0 if k == 0 else _betainv(alpha / 2.0, k, n - k + 1)
    upper = 1.0 if k == n else _betainv(1.0 - alpha / 2.0, k + 1, n - k)
    return (lower, upper)


def _rate(k: int, n: int) -> dict:
    lo, hi = clopper_pearson(k, n)
    return {
        "count": int(k),
        "total": int(n),
        "rate": (k / n) if n else None,
        "ci95_low": lo,
        "ci95_high": hi,
    }


def list_identities(root: Path) -> list[tuple[str, list[Path]]]:
    identities = []
    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        images = sorted(
            p
            for p in person_dir.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        if images:
            identities.append((person_dir.name, images))
    return identities


def embed_image(path: Path, jpeg_quality: int):
    data = path.read_bytes()
    dets = detect.detect_faces(data)
    if not dets:
        return None, "no_face"
    if len(dets) > 1:
        return None, "multi_face"

    aligned = detect.align_largest_face(data, detection=dets[0])
    if aligned is None:
        return None, "align_failed"

    if jpeg_quality < 100:
        ok, buf = cv2.imencode(
            ".jpg", aligned, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
        )
        if not ok:
            return None, "encode_failed"
        aligned = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if aligned is None:
            return None, "encode_failed"

    try:
        return embed.embed_aligned(aligned), None
    except ValueError:
        return None, "embed_failed"


def build_gallery(identities, jpeg_quality: int, progress_every: int):
    vectors: list[np.ndarray] = []
    labels: list[int] = []
    names: list[str] = []
    skips: dict[str, int] = {}
    total = sum(len(imgs) for _, imgs in identities)
    done = 0
    started = time.monotonic()

    for label, (name, images) in enumerate(identities):
        names.append(name)
        for image_path in images:
            vec, reason = embed_image(image_path, jpeg_quality)
            done += 1
            if reason is not None:
                skips[reason] = skips.get(reason, 0) + 1
            else:
                vectors.append(vec)
                labels.append(label)
            if progress_every and done % progress_every == 0:
                rate = done / max(time.monotonic() - started, 1e-9)
                print(
                    f"  {done}/{total} images  "
                    f"({rate:.1f} img/s, {len(vectors)} embedded)",
                    file=sys.stderr,
                    flush=True,
                )

    elapsed = time.monotonic() - started
    if not vectors:
        raise SystemExit(
            "no image produced an embedding — check --data layout and models/"
        )
    return (
        np.vstack(vectors).astype(np.float32),
        np.asarray(labels, dtype=np.int64),
        names,
        skips,
        total,
        elapsed,
    )


def genuine_scores(vectors: np.ndarray, labels: np.ndarray) -> np.ndarray:
    scores = []
    for label in np.unique(labels):
        idx = np.flatnonzero(labels == label)
        if idx.size < 2:
            continue
        block = vectors[idx]
        sims = block @ block.T
        iu = np.triu_indices(idx.size, k=1)
        scores.append(sims[iu])
    if not scores:
        raise SystemExit(
            "no identity has two usable images — no genuine pairs to score"
        )
    return np.concatenate(scores).astype(np.float64)


def impostor_scores(
    vectors: np.ndarray, labels: np.ndarray, n_pairs: int, seed: int
) -> tuple[np.ndarray, int]:
    n = vectors.shape[0]
    counts = np.bincount(labels)
    total_pairs = n * (n - 1) // 2
    same_pairs = int(np.sum(counts * (counts - 1) // 2))
    available = total_pairs - same_pairs

    rng = np.random.default_rng(seed)

    if n_pairs >= available:
        i_idx, j_idx = np.triu_indices(n, k=1)
        keep = labels[i_idx] != labels[j_idx]
        i_sel, j_sel = i_idx[keep], j_idx[keep]
    else:
        keys = np.empty(0, dtype=np.int64)
        while keys.size < n_pairs:
            want = n_pairs - keys.size
            draw = min(max(int(want * 1.3) + 1024, 4096), 4_000_000)
            a = rng.integers(0, n, size=draw)
            b = rng.integers(0, n, size=draw)
            ok = labels[a] != labels[b]
            lo = np.minimum(a[ok], b[ok]).astype(np.int64)
            hi = np.maximum(a[ok], b[ok]).astype(np.int64)
            fresh = np.unique(lo * n + hi)
            if keys.size:
                fresh = fresh[~np.isin(fresh, keys, assume_unique=True)]
            keys = np.concatenate([keys, fresh])

        keys = rng.permutation(keys)[:n_pairs]
        i_sel = keys // n
        j_sel = keys % n

    return _paired_dots(vectors, i_sel, j_sel), available


def _paired_dots(vectors: np.ndarray, i_sel, j_sel, chunk: int = 100_000):
    out = np.empty(len(i_sel), dtype=np.float64)
    for start in range(0, len(i_sel), chunk):
        end = min(start + chunk, len(i_sel))
        a = vectors[i_sel[start:end]]
        b = vectors[j_sel[start:end]]
        out[start:end] = np.einsum("ij,ij->i", a, b)
    return out


def far_at(imp_sorted: np.ndarray, threshold: float) -> int:
    return int(imp_sorted.size - np.searchsorted(imp_sorted, threshold, "left"))


def frr_at(gen_sorted: np.ndarray, threshold: float) -> int:
    return int(np.searchsorted(gen_sorted, threshold, "left"))


def sweep(gen_sorted: np.ndarray, imp_sorted: np.ndarray) -> list[dict]:
    rows = []
    for t in THRESHOLD_SWEEP:
        rows.append(
            {
                "threshold": round(t, 2),
                "far": _rate(far_at(imp_sorted, t), imp_sorted.size),
                "frr": _rate(frr_at(gen_sorted, t), gen_sorted.size),
            }
        )
    return rows


def threshold_for_far(
    gen_sorted: np.ndarray, imp_sorted: np.ndarray, target: float
) -> dict:
    n_imp = imp_sorted.size
    k = int(math.floor(target * n_imp))
    resolvable = k >= 1

    if k >= n_imp:
        raw = float(imp_sorted[0]) - 1e-6
    else:
        raw = float(imp_sorted[n_imp - 1 - k]) + 1e-9

    threshold = math.ceil(raw * 10_000) / 10_000
    threshold = min(max(threshold, -1.0), 1.0)

    far_k = far_at(imp_sorted, threshold)
    frr_k = frr_at(gen_sorted, threshold)
    return {
        "target_far": target,
        "resolvable": resolvable,
        "impostor_budget": k,
        "threshold": threshold,
        "far": _rate(far_k, n_imp),
        "frr": _rate(frr_k, gen_sorted.size),
        "tar": _rate(gen_sorted.size - frr_k, gen_sorted.size),
    }


def equal_error_rate(gen_sorted: np.ndarray, imp_sorted: np.ndarray) -> dict:
    grid = np.arange(-1.0, 1.0 + 1e-9, 1e-4)
    far = (
        imp_sorted.size - np.searchsorted(imp_sorted, grid, "left")
    ) / imp_sorted.size
    frr = np.searchsorted(gen_sorted, grid, "left") / gen_sorted.size
    i = int(np.argmin(np.abs(far - frr)))
    t = float(grid[i])
    return {
        "threshold": round(t, 4),
        "eer": float((far[i] + frr[i]) / 2.0),
        "far": _rate(far_at(imp_sorted, t), imp_sorted.size),
        "frr": _rate(frr_at(gen_sorted, t), gen_sorted.size),
    }


def auc(gen: np.ndarray, imp: np.ndarray) -> float:
    merged = np.concatenate([gen, imp])
    order = np.argsort(merged, kind="mergesort")
    srt = merged[order]
    _, inv, counts = np.unique(srt, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    starts = ends - counts
    midrank = (starts + ends + 1) / 2.0
    ranks = np.empty(merged.size, dtype=np.float64)
    ranks[order] = midrank[inv]
    n_g, n_i = gen.size, imp.size
    u = float(ranks[:n_g].sum()) - n_g * (n_g + 1) / 2.0
    return u / (n_g * n_i)


def histogram(scores: np.ndarray) -> dict:
    edges = np.round(np.arange(HIST_LO, HIST_HI + HIST_STEP / 2, HIST_STEP), 4)
    counts, _ = np.histogram(scores, bins=edges)
    return {
        "bin_edges": [float(e) for e in edges],
        "counts": [int(c) for c in counts],
    }


def describe(scores: np.ndarray) -> dict:
    return {
        "count": int(scores.size),
        "mean": float(scores.mean()),
        "std": float(scores.std(ddof=1)) if scores.size > 1 else 0.0,
        "min": float(scores.min()),
        "p01": float(np.percentile(scores, 1)),
        "median": float(np.median(scores)),
        "p99": float(np.percentile(scores, 99)),
        "max": float(scores.max()),
        "histogram": histogram(scores),
    }


def _pct(rate: dict) -> str:
    if rate["rate"] is None:
        return "n/a"
    return f"{rate['rate']:.3e} [{rate['ci95_low']:.2e}, {rate['ci95_high']:.2e}]"


def _ascii_hist(gen_h: dict, imp_h: dict, lo: float = -1.0, hi: float = 1.0) -> str:
    edges = gen_h["bin_edges"]
    rows = []
    gmax = max(gen_h["counts"]) or 1
    imax = max(imp_h["counts"]) or 1
    for i in range(len(edges) - 1):
        left = edges[i]
        if left < lo - 1e-9 or left >= hi - 1e-9:
            continue
        if i % 2:
            continue
        g = gen_h["counts"][i] + gen_h["counts"][i + 1]
        m = imp_h["counts"][i] + imp_h["counts"][i + 1]
        if not g and not m:
            continue
        gbar = "#" * int(round(24 * g / gmax / 2)) if g else ""
        ibar = "#" * int(round(24 * m / imax / 2)) if m else ""
        rows.append(f"| {left:+.2f} | {g:>9,} | {gbar:<24} | {m:>10,} | {ibar:<24} |")
    header = (
        "| bin    | genuine   | genuine (scaled)         "
        "| impostor   | impostor (scaled)        |\n"
        "|--------|-----------|--------------------------"
        "|------------|--------------------------|"
    )
    return header + "\n" + "\n".join(rows)


def render_report(r: dict) -> str:
    m = r["meta"]
    d = r["dataset"]
    g, i = r["genuine"], r["impostor"]
    deploy = next(
        x for x in r["thresholds_at_target_far"] if x["target_far"] == DEPLOY_TARGET_FAR
    )

    lines = []
    add = lines.append

    add("# Matcher threshold validation — ROC report")
    add("")
    add(
        f"Generated {m['date']} by `engine/eval/roc.py` "
        f"(seed {m['seed']}, engine {m['engine_version']})."
    )
    add("")
    add("## Read this before quoting the threshold")
    add("")
    add(
        f"The embedding model is **{MODEL_NAME}**, trained on "
        f"**{TRAINING_SET}**. The corpus measured here is "
        f"**{d['name']}** — an LFW-layout set of frontal, mostly "
        "well-lit press photographs."
    )
    add("")
    add(
        f"**LFW is near-saturated for {MODEL_NAME}.** Modern ArcFace "
        "backbones sit at ~99.8% verification accuracy on it, which means "
        "the protocol has almost no discriminative headroom left: the "
        "impostor distribution it exercises is far easier than production "
        "traffic, so the threshold derived below is **optimistic**. Treat "
        "it as an upper bound on performance and a lower bound on the "
        "threshold you would actually deploy — not as a validated "
        "operating point."
    )
    add("")
    add(
        "The harder protocols that would make this number trustworthy have "
        "**not been run**:"
    )
    add("")
    add(
        "- **CFP-FP** — frontal-to-profile pairs; pose is the failure mode "
        "LFW does not contain."
    )
    add(
        "- **AgeDB-30** — 30-year age gaps; ageing is the second failure "
        "mode LFW does not contain."
    )
    add(
        "- **IJB-C** — the one that matters for an operating point: "
        "template-based, millions of impostor pairs, and FAR resolvable to "
        "1e-6 with real CI width at that rate."
    )
    add("")
    add("Until at least IJB-C is run, every threshold in this report is provisional.")
    add("")

    add("## Pipeline measured")
    add("")
    add("The real engine path, not a re-implementation:")
    add("")
    add("1. `app.detect.detect_faces` — SCRFD `det_500m`, CPU provider")
    add(
        "2. vendored 5-point similarity alignment "
        "(`app.vendor.face_align.norm_crop`) → 112×112 BGR"
    )
    add(
        f"3. JPEG re-encode at quality **{m['jpeg_quality']}** — the SDK's "
        "own capture quality (`apps/demo/index.html`, `encodeScan` first "
        "rung 0.62), so the measurement includes the compression the engine "
        "actually receives"
    )
    add(f"4. `app.embed.embed_aligned` — ArcFace `{MODEL_NAME}`, L2-normalised 512-d")
    add("5. cosine similarity = dot product of unit vectors")
    add("")
    add(
        "Deliberate deviations from a live `/v1/verify`: one image per "
        f"sample where the endpoint averages the top-"
        f"{m['reference_frames']} frames of a 12-frame scan "
        "(`main.REFERENCE_FRAMES`) — averaging reduces per-sample noise, so "
        "a single-image threshold is the conservative one; and the "
        "re-encode is applied to the 112×112 crop rather than to the full "
        "480 px frame."
    )
    add("")

    add("## Corpus and detection skip rate")
    add("")
    add(f"- Source: `{d['path']}`")
    add(
        f"- Identities: **{d['identities']:,}** "
        f"({d['identities_with_pairs']:,} contributed a genuine pair)"
    )
    add(f"- Images found: **{d['images_total']:,}**")
    add(f"- Images embedded: **{d['images_embedded']:,}**")
    add(
        f"- **Detection skip rate: {d['skip_rate'] * 100:.2f}%** "
        f"({d['images_skipped']:,} images), 95% CI "
        f"[{d['skip_rate_ci95_low'] * 100:.2f}%, "
        f"{d['skip_rate_ci95_high'] * 100:.2f}%]"
    )
    add("")
    add(
        "The skip rate is a result in its own right: these are images the "
        "engine would have answered `NO_FACE` or `MULTI_FACE` on, and they "
        "never reach the matcher. A pipeline that quietly drops them scores "
        "better than the one users meet."
    )
    add("")
    add("| skip reason | images | share of corpus |")
    add("|-------------|--------|-----------------|")
    for reason, count in sorted(d["skips"].items(), key=lambda kv: -kv[1]):
        add(f"| `{reason}` | {count:,} | {count / d['images_total'] * 100:.2f}% |")
    if not d["skips"]:
        add("| _(none)_ | 0 | 0.00% |")
    add("")
    add(
        f"Embedding wall time: {d['embed_seconds']:.0f}s "
        f"({d['images_total'] / max(d['embed_seconds'], 1e-9):.1f} img/s, "
        "CPU)."
    )
    add("")

    add("## Score distributions")
    add("")
    add("| | pairs | mean | std | p01 | median | p99 | min | max |")
    add("|---|-------|------|-----|-----|--------|-----|-----|-----|")
    for label, s in (("genuine", g), ("impostor", i)):
        add(
            f"| {label} | {s['count']:,} | {s['mean']:.4f} | "
            f"{s['std']:.4f} | {s['p01']:.4f} | {s['median']:.4f} | "
            f"{s['p99']:.4f} | {s['min']:.4f} | {s['max']:.4f} |"
        )
    add("")
    add(
        f"Impostor pairs are a seeded random sample of the "
        f"{r['impostor_pairs_available']:,} cross-identity pairs available; "
        "genuine pairs are exhaustive."
    )
    add("")
    add(
        "Histogram (0.02-wide rows, bins empty in both columns dropped; "
        "bars are scaled per column, so the two are **not** comparable in "
        "height — the full 0.01 bins over [-1, 1] are in `roc.json`):"
    )
    add("")
    add(_ascii_hist(g["histogram"], i["histogram"]))
    add("")

    add("## Summary metrics")
    add("")
    add(f"- **AUC**: {r['auc']:.6f}")
    add(
        f"- **EER**: {r['eer']['eer'] * 100:.4f}% at threshold "
        f"**{r['eer']['threshold']:.4f}**"
    )
    add(f"  - FAR {_pct(r['eer']['far'])}")
    add(f"  - FRR {_pct(r['eer']['frr'])}")
    add("")

    add("## Threshold at each target FAR")
    add("")
    add(
        "Decision rule is `score >= threshold`. Rates are measured **at the "
        "reported (rounded-up) threshold**, with exact Clopper-Pearson 95% "
        "intervals."
    )
    add("")
    add(
        "| target FAR | threshold | measured FAR [95% CI] | "
        "FRR [95% CI] | TAR | resolvable |"
    )
    add(
        "|------------|-----------|------------------------|"
        "--------------|-----|------------|"
    )
    for row in r["thresholds_at_target_far"]:
        add(
            f"| {row['target_far']:.0e} | **{row['threshold']:.4f}** | "
            f"{_pct(row['far'])} | {_pct(row['frr'])} | "
            f"{row['tar']['rate'] * 100:.2f}% | "
            f"{'yes' if row['resolvable'] else '**no**'} |"
        )
    add("")
    add(
        f'"Resolvable" means the impostor sample is large enough to '
        f"demonstrate the target at all: at {i['count']:,} impostor pairs "
        f"the finest FAR that can be *measured* rather than bounded is "
        f"{1.0 / i['count']:.1e}. A `no` row is a lower bound on the "
        "threshold, not a measurement — its upper confidence limit says so."
    )
    add("")
    add(
        f"**The deployed value** is the FAR {DEPLOY_TARGET_FAR:.0e} row: "
        f"`MATCH_THRESHOLD = {deploy['threshold']:.4f}` in "
        "`engine/app/main.py`, published on `/v1/info` as "
        "`thresholds.match`."
    )
    add("")

    add("## FAR / FRR sweep, 0.30 → 0.80")
    add("")
    add("| threshold | FAR | FAR 95% CI | FRR | FRR 95% CI |")
    add("|-----------|-----|------------|-----|------------|")
    for row in r["sweep"]:
        far, frr = row["far"], row["frr"]
        add(
            f"| {row['threshold']:.2f} | {far['rate']:.3e} | "
            f"[{far['ci95_low']:.2e}, {far['ci95_high']:.2e}] | "
            f"{frr['rate']:.3e} | "
            f"[{frr['ci95_low']:.2e}, {frr['ci95_high']:.2e}] |"
        )
    add("")

    add("## Reproducing")
    add("")
    add("```")
    add(f"python engine/eval/roc.py --data {d['path']} \\")
    add(
        f"    --impostor-pairs {i['count']} --seed {m['seed']} "
        f"--jpeg-quality {m['jpeg_quality']}"
    )
    add("```")
    add("")
    add(
        "The harness makes no network calls; the corpus must already be on "
        "disk. Neither the corpus nor any embedding derived from it may be "
        "committed — `engine/eval/.gitignore` admits only `*.py` and this "
        "report."
    )
    add("")
    return "\n".join(lines)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Offline ROC / threshold validation for the Facetech matcher. "
            "Runs the real engine pipeline over an identity-labelled corpus."
        )
    )
    p.add_argument(
        "--data",
        required=True,
        type=Path,
        help=(
            "Directory of identity-labelled face images, LFW layout "
            "(one subdirectory per person). No default, no download."
        ),
    )
    p.add_argument(
        "--impostor-pairs",
        type=int,
        default=1_000_000,
        help="Cross-identity pairs to sample (default: 1_000_000).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=20260913,
        help="RNG seed for the impostor sample (default: 20260913).",
    )
    p.add_argument(
        "--jpeg-quality",
        type=int,
        default=SDK_JPEG_QUALITY,
        help=(
            f"JPEG quality for the crop re-encode (default: "
            f"{SDK_JPEG_QUALITY}, the SDK's capture quality). "
            "100 skips the re-encode."
        ),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Where roc_report.md and roc.json are written (default: eval/).",
    )
    p.add_argument(
        "--max-identities",
        type=int,
        default=0,
        help="Smoke-test cap on identities (0 = all).",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=250,
        help="Progress line every N images to stderr (0 = silent).",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root: Path = args.data.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"--data is not a directory: {root}")

    identities = list_identities(root)
    if not identities:
        raise SystemExit(
            f"no identity subdirectories with images under {root} "
            "(expected LFW layout: one directory per person)"
        )
    if args.max_identities:
        identities = identities[: args.max_identities]

    print(
        f"[1/4] embedding {sum(len(x) for _, x in identities):,} images "
        f"across {len(identities):,} identities "
        f"(jpeg q={args.jpeg_quality})...",
        file=sys.stderr,
        flush=True,
    )
    vectors, labels, _names, skips, images_total, elapsed = build_gallery(
        identities, args.jpeg_quality, args.progress_every
    )
    skipped = images_total - vectors.shape[0]
    skip_lo, skip_hi = clopper_pearson(skipped, images_total)

    print("[2/4] scoring genuine pairs...", file=sys.stderr, flush=True)
    gen = genuine_scores(vectors, labels)

    print(
        f"[3/4] sampling {args.impostor_pairs:,} impostor pairs...",
        file=sys.stderr,
        flush=True,
    )
    imp, available = impostor_scores(vectors, labels, args.impostor_pairs, args.seed)

    print(
        "[4/4] computing rates and writing the report...", file=sys.stderr, flush=True
    )
    gen_sorted = np.sort(gen)
    imp_sorted = np.sort(imp)

    counts = np.bincount(labels)
    report = {
        "meta": {
            "date": date.today().isoformat(),
            "generator": "engine/eval/roc.py",
            "seed": args.seed,
            "jpeg_quality": args.jpeg_quality,
            "model": MODEL_NAME,
            "training_set": TRAINING_SET,
            "detector": detect.DET_MODEL_FILE.name,
            "embedder": embed.EMB_MODEL_FILE.name,
            "engine_version": _engine_version(),
            "reference_frames": _reference_frames(),
            "protocols_not_run": ["CFP-FP", "AgeDB-30", "IJB-C"],
            "lfw_saturated": True,
        },
        "dataset": {
            "name": root.name,
            "path": str(root),
            "identities": len(identities),
            "identities_with_pairs": int(np.sum(counts >= 2)),
            "images_total": images_total,
            "images_embedded": int(vectors.shape[0]),
            "images_skipped": skipped,
            "skip_rate": skipped / images_total if images_total else 0.0,
            "skip_rate_ci95_low": skip_lo,
            "skip_rate_ci95_high": skip_hi,
            "skips": skips,
            "embed_seconds": elapsed,
        },
        "genuine": describe(gen),
        "impostor": describe(imp),
        "impostor_pairs_available": int(available),
        "auc": auc(gen, imp),
        "eer": equal_error_rate(gen_sorted, imp_sorted),
        "thresholds_at_target_far": [
            threshold_for_far(gen_sorted, imp_sorted, t) for t in TARGET_FARS
        ],
        "sweep": sweep(gen_sorted, imp_sorted),
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "roc.json"
    md_path = args.out_dir / "roc_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_report(report), encoding="utf-8")

    deploy = next(
        x
        for x in report["thresholds_at_target_far"]
        if x["target_far"] == DEPLOY_TARGET_FAR
    )
    print(f"\nwrote {md_path}\nwrote {json_path}", file=sys.stderr)
    print(
        f"\nMATCH_THRESHOLD @ FAR {DEPLOY_TARGET_FAR:.0e} = "
        f"{deploy['threshold']:.4f}  "
        f"(FRR {deploy['frr']['rate']:.3e}, "
        f"skip rate {report['dataset']['skip_rate'] * 100:.2f}%)",
        file=sys.stderr,
    )
    return 0


def _engine_version() -> str:
    try:
        from app.main import ENGINE_VERSION

        return ENGINE_VERSION
    except Exception:  # pragma: no cover
        return "unknown"


def _reference_frames() -> int:
    try:
        from app.main import REFERENCE_FRAMES

        return REFERENCE_FRAMES
    except Exception:  # pragma: no cover
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
