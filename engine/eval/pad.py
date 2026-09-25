from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

import numpy as np  # noqa: E402

from app import challenge as challenge_mod  # noqa: E402
from app import detect, facescan, liveness  # noqa: E402
from app.main import _ranked_frames, _scan_detections, _tracked_faces  # noqa: E402
from eval.roc import clopper_pearson  # noqa: E402

BONA_FIDE = "bona_fide"

ATTACK_TYPES = ("print", "screen_phone", "screen_laptop", "virtual_cam")

TARGET_BPCER = 0.02

EVALUATION_SCOPE = {
    "purpose": "exploratory_signal_analysis",
    "threshold_selection": "same_corpus_as_reported_rates",
    "rate_denominator": "finite_measurements_only",
    "challenge_validation": "synthetic_verdict_no_nonce_validation",
    "held_out_validation": False,
    "deployment_ready": False,
}

MODEL_PINS = ENGINE_ROOT / "scripts" / "download_models.py"


class Measurement:
    def __init__(self, signal: str, field: str, direction: str, note: str):
        assert direction in ("low", "high"), direction
        self.signal, self.field, self.direction, self.note = (
            signal,
            field,
            direction,
            note,
        )

    @property
    def name(self) -> str:
        return f"{self.signal}.{self.field}"

    def accepts(self, value: float, threshold: float) -> bool:
        return value >= threshold if self.direction == "low" else value <= threshold

    def rule(self, threshold: float) -> str:
        comparator = ">=" if self.direction == "low" else "<="
        return f"accept when {self.name} {comparator} {threshold:.4f}"


MEASUREMENTS = (
    Measurement(
        "depth",
        "depth_index",
        "low",
        "landmark-ratio change per unit of approach. A plane scales uniformly, "
        "so every ratio is invariant and the index sits near zero; a head's "
        "nose moves relative to its eyes and mouth. The S7 signal this "
        "harness exists to threshold.",
    ),
    Measurement(
        "moire",
        "peakiness",
        "high",
        "max-over-median of the aligned crop's mid-high FFT band. A screen's "
        "pixel grid and a print's halftone are periodic; skin is not.",
    ),
    Measurement(
        "challenge",
        "scale_toward",
        "low",
        "log-scale gain after the settle window. Not a PAD measurement — an "
        "attack that carries a photo toward the lens produces a real one — "
        "but reported so a corpus where the attacks simply did not move is "
        "visible as such rather than mistaken for a working signal.",
    ),
    Measurement(
        "motion",
        "mean_abs_diff",
        "low",
        "inter-frame change between aligned crops. Anti-replay, not PAD; here "
        "for the same reason as challenge.",
    ),
    Measurement(
        "landmarks",
        "landmark_jitter",
        "low",
        "landmark spread over face size. Anti-replay, not PAD.",
    ),
)


def synthetic_verdict(scan: dict) -> challenge_mod.ChallengeVerdict:
    raw = scan.get("challenge") if isinstance(scan.get("challenge"), dict) else {}
    action = raw.get("action")
    if action not in challenge_mod.ACTIONS:
        action = challenge_mod.MOVE_CLOSER
    params = raw.get("params") if isinstance(raw.get("params"), dict) else None
    return challenge_mod.ChallengeVerdict(
        ok=True,
        reason=None,
        action=action,
        presented_action=action,
        params=params,
        nonce_prefix="offline",
        consumed=False,
    )


def score_scan(path: Path) -> dict:
    scan = facescan.parse_facescan_bytes(path.read_bytes())
    detections = _scan_detections(scan)
    ranked = _ranked_frames(scan, detections)
    if not ranked:
        return {"file": path.name, "error": "no face in any frame"}
    tracked = _tracked_faces(detections, ranked[0][1][0])
    result = liveness.check_liveness(scan["frames"], tracked, synthetic_verdict(scan))
    return {
        "file": path.name,
        "live": result.live,
        "score": result.score,
        "signals": {name: sig.as_dict() for name, sig in result.signals.items()},
    }


def read_corpus(root: Path) -> tuple[dict[str, list[dict]], list[str]]:
    corpus: dict[str, list[dict]] = {}
    missing: list[str] = []
    for name in (BONA_FIDE, *ATTACK_TYPES):
        directory = root / name
        if not directory.is_dir():
            missing.append(name)
            continue
        files = sorted(directory.glob("*.msgpack"))
        if not files:
            missing.append(name)
            continue
        print(f"[{name}] scoring {len(files)} scans...", file=sys.stderr, flush=True)
        corpus[name] = [score_scan(f) for f in files]
    return corpus, missing


def values_for(scans: list[dict], measurement: Measurement) -> list[float]:
    out = []
    for scan in scans:
        signal = scan.get("signals", {}).get(measurement.signal, {})
        value = signal.get(measurement.field)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            out.append(float(value))
    return out


def unusable_for(scans: list[dict], measurement: Measurement) -> int:
    return len(scans) - len(values_for(scans, measurement))


def rate(k: int, n: int) -> dict:
    if n == 0:
        return {"count": 0, "total": 0, "rate": None, "ci95": [None, None]}
    lo, hi = clopper_pearson(k, n)
    return {"count": k, "total": n, "rate": k / n, "ci95": [lo, hi]}


def candidate_thresholds(all_values: list[float]) -> list[float]:
    if not all_values:
        return []
    ordered = sorted(set(all_values))
    return ordered


def sweep(corpus: dict[str, list[dict]], measurement: Measurement) -> dict:
    bona = values_for(corpus.get(BONA_FIDE, []), measurement)
    attacks = {
        name: values_for(corpus.get(name, []), measurement)
        for name in ATTACK_TYPES
        if name in corpus
    }
    every = bona + [v for values in attacks.values() for v in values]
    rows = []
    for threshold in candidate_thresholds(every):
        rejected = sum(not measurement.accepts(v, threshold) for v in bona)
        row = {
            "threshold": threshold,
            "bpcer": rate(rejected, len(bona)),
            "apcer": {
                name: rate(
                    sum(measurement.accepts(v, threshold) for v in values), len(values)
                )
                for name, values in attacks.items()
            },
        }

        rates = [r["rate"] for r in row["apcer"].values() if r["rate"] is not None]
        row["apcer_worst"] = max(rates) if rates else None
        rows.append(row)
    return {
        "measurement": measurement.name,
        "signal": measurement.signal,
        "field": measurement.field,
        "direction": measurement.direction,
        "note": measurement.note,
        "distributions": {
            name: describe(values_for(scans, measurement))
            | {"unusable": unusable_for(scans, measurement)}
            for name, scans in corpus.items()
        },
        "sweep": rows,
        "operating_point": operating_point(rows),
    }


def operating_point(rows: list[dict]) -> dict | None:
    eligible = [
        r
        for r in rows
        if r["bpcer"]["rate"] is not None
        and r["bpcer"]["rate"] <= TARGET_BPCER
        and r["apcer_worst"] is not None
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda r: (r["apcer_worst"], r["bpcer"]["rate"]))


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "min": ordered[0],
        "p05": percentile(ordered, 0.05),
        "median": statistics.median(ordered),
        "p95": percentile(ordered, 0.95),
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
    }


def percentile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    return float(np.quantile(np.asarray(ordered), q))


def _pct(r: dict) -> str:
    if r["rate"] is None:
        return "—"
    lo, hi = r["ci95"]
    return f"{r['rate'] * 100:.1f}% [{lo * 100:.1f}–{hi * 100:.1f}] ({r['count']}/{r['total']})"


def render_report(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Presentation-attack detection — exploratory signal report")
    add("")
    add(f"Generated {report['generated']} by `engine/eval/pad.py` (S7.4).")
    add("")
    add(
        "**Exploratory only; not deployment validation.** Threshold candidates "
        "are selected and measured on the same corpus. No held-out validation "
        "is performed. Offline scoring supplies a synthetic challenge verdict; "
        "it does not validate nonce freshness or replay protection."
    )
    add("")
    add("## The corpus")
    add("")
    add("| Class | Scans | Unreadable |")
    add("|---|---:|---:|")
    for name, counts in report["corpus"].items():
        add(f"| `{name}` | {counts['scans']} | {counts['errors']} |")
    add("")
    if report["missing"]:
        add(
            "**Missing classes: "
            + ", ".join(f"`{m}`" for m in report["missing"])
            + ".** Every number below is silent about them. A threshold "
            "chosen against the attacks that happen to have been recorded is "
            "a threshold whose APCER against the others is unmeasured, not "
            "zero."
        )
        add("")
    add(
        "The APCER and BPCER columns describe hypothetical single-measurement "
        "rules: attacks accepted per type and genuine scans rejected. "
        "**Denominators include finite measurements only.** Missing, invalid "
        "and non-finite values appear under `no value` and are excluded from "
        "these rates, not counted as accepted or rejected. These are not "
        "end-to-end engine error rates or a standards-conformance result. "
        "Clopper-Pearson 95 % intervals assume independent trials and do not "
        "account for selecting thresholds on this corpus or repeated scans "
        "from the same participant."
    )
    add("")
    for entry in report["measurements"]:
        add(f"## `{entry['measurement']}`")
        add("")
        add(entry["note"])
        add("")
        direction = (
            "An attack produces a **low** value, so the decision is "
            "`accept when value >= threshold`."
            if entry["direction"] == "low"
            else "An attack produces a **high** value, so the decision is "
            "`accept when value <= threshold`."
        )
        add(direction)
        add("")
        add("| Class | n | min | p05 | median | p95 | max | no value |")
        add("|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, d in entry["distributions"].items():
            if not d.get("n"):
                add(f"| `{name}` | 0 | — | — | — | — | — | {d.get('unusable', 0)} |")
                continue
            add(
                f"| `{name}` | {d['n']} | {d['min']:.4f} | {d['p05']:.4f} | "
                f"{d['median']:.4f} | {d['p95']:.4f} | {d['max']:.4f} | "
                f"{d['unusable']} |"
            )
        add("")
        point = entry["operating_point"]
        if point is None:
            attacks_measured = sum(
                d.get("n", 0)
                for name, d in entry["distributions"].items()
                if name != BONA_FIDE
            )
            if not attacks_measured:
                add(
                    "**No attack scan produced a value for this measurement.** "
                    "Look at the `no value` column: the signal declined to "
                    "measure rather than scoring zero. For `depth`, this can "
                    "mean insufficient approach or unusable landmarks; inspect "
                    "the per-scan reason before drawing a conclusion. "
                    "There is nothing here to threshold against — record "
                    "attacks that actually perform the movement, or this "
                    "measurement is untested against them rather than "
                    "passing."
                )
            else:
                add(
                    f"**No threshold reaches BPCER ≤ {TARGET_BPCER * 100:.0f}% on "
                    "this corpus.** Either the classes do not separate on this "
                    "measurement, or there are too few bona-fide scans for any "
                    "threshold to clear the target. Both are answers; neither "
                    "is a reason to pick a threshold anyway."
                )
            add("")
            continue
        add(f"**In-sample candidate** — {report['rules'][entry['measurement']]}")
        add("")
        add(f"- BPCER {_pct(point['bpcer'])}")
        for name, r in point["apcer"].items():
            add(f"- APCER `{name}` {_pct(r)}")
        add("")
    add("## What to do with this")
    add("")
    add(
        "Keep advisory signals advisory. An in-sample candidate does not "
        "justify changing production thresholds or enabling a rejection rule. "
        "Freeze any candidate before testing it on independent held-out "
        "participants and sessions, covering each intended device, lighting "
        "condition and attack type. Report unavailable measurements and "
        "end-to-end decisions separately, including genuine failures. "
        "Agree acceptance criteria before validation; do not lower thresholds "
        "simply to make genuine attempts pass."
    )
    add("")
    add(
        "Record the chosen numbers **and the corpus they came from** (counts, "
        "people, lighting, devices — never the data) in `README.md` "
        "under evaluation results. A threshold without its corpus is the same unverifiable claim "
        "as no threshold at all."
    )
    return "\n".join(lines) + "\n"


def pinned_sha256(file_name: str) -> str | None:
    if not MODEL_PINS.is_file():
        return None
    spec = importlib.util.spec_from_file_location("facetech_model_pins", MODEL_PINS)
    pins = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pins)
    return next((m["sha256"] for m in pins.MODELS if m["file"] == file_name), None)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_detector(model: Path) -> str | None:
    if not model.is_file():
        return (
            f"detection model missing: {model}. Fetch the pinned models with "
            "scripts/download_models.py before evaluating."
        )
    pin = pinned_sha256(detect.DET_MODEL_FILE.name)
    if pin is None:
        return f"no pinned sha256 for {detect.DET_MODEL_FILE.name} in {MODEL_PINS}"
    if sha256_of(model) != pin:
        return (
            f"detection model {model} does not match the pinned sha256 for "
            f"{detect.DET_MODEL_FILE.name}: the file is corrupt or not the "
            "production detector"
        )
    detect.DET_MODEL_FILE = model
    try:
        detect.get_detector()
        detect._detect_all(np.zeros((64, 64, 3), dtype=np.uint8))
    except Exception as exc:
        return f"detection model {model} could not be loaded: {type(exc).__name__}"
    return None


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Measure APCER/BPCER for the liveness signals over a "
        "directory of recorded FaceScan payloads (S7.4).",
    )
    p.add_argument(
        "--data",
        type=Path,
        required=True,
        help="Directory holding bona_fide/ and the attack-type subdirectories.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=ENGINE_ROOT / "eval",
        help="Where pad_report.md and pad.json are written (default: eval/).",
    )
    p.add_argument(
        "--detector-model",
        type=Path,
        default=detect.DET_MODEL_FILE,
        help="The SCRFD detector file; it must match the pinned production model.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.data.is_dir():
        print(f"no such directory: {args.data}", file=sys.stderr)
        return 2
    failure = load_detector(args.detector_model)
    if failure is not None:
        print(failure, file=sys.stderr)
        return 3

    corpus, missing = read_corpus(args.data)
    if BONA_FIDE not in corpus:
        print(
            f"no {BONA_FIDE}/ scans: BPCER is undefined and every threshold "
            "below would be chosen against attacks alone",
            file=sys.stderr,
        )
        return 4

    entries = [sweep(corpus, m) for m in MEASUREMENTS]
    report = {
        "generated": date.today().isoformat(),
        "data": str(args.data),
        "target_bpcer": TARGET_BPCER,
        "evaluation_scope": dict(EVALUATION_SCOPE),
        "missing": missing,
        "corpus": {
            name: {
                "scans": len(scans),
                "errors": sum("error" in s for s in scans),
            }
            for name, scans in corpus.items()
        },
        "rules": {
            e["measurement"]: next(
                m for m in MEASUREMENTS if m.name == e["measurement"]
            ).rule(e["operating_point"]["threshold"])
            if e["operating_point"]
            else "no usable operating point"
            for e in entries
        },
        "measurements": entries,
        "scans": corpus,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "pad.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (args.out / "pad_report.md").write_text(render_report(report), encoding="utf-8")
    print(f"wrote {args.out / 'pad_report.md'} and {args.out / 'pad.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
