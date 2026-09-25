r"""Build the numbers-only head-gate regression fixture from the VPS metadata.

Reads only `meta.json` from the local read-only capture expansion. It never
opens a frame, `scan.msgpack`, an embedding or a template, and it writes no
subject identifier: real subject ids are replaced with S1..S5 in first-seen
order. The output holds timestamps, the recorded five-point yaw ratio pairs and
the recorded head-gate verdict, which are numbers, not biometric samples.

Run from E:\Factech:

    .\.venv-pad\Scripts\python.exe tools\hardening_head_gate_fixture.py
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("E:/facetech-vps-data/2026-09-20/scans/eval-d36fa4946ddf")
OUTPUT = ROOT / "tests-hardening/fixtures/head_gate_traces.json"

# Fields copied verbatim from the recorded `head_sequence` decision trace.
VERDICT_FIELDS = ("ok", "reason", "first_sign", "switch_ms", "span_ms", "phases")


def _traces(source: Path = SOURCE) -> list[dict]:
    aliases: dict[str, str] = {}
    rows = []
    for meta in sorted(source.glob("*/*/meta.json")):
        record = json.loads(meta.read_text(encoding="utf-8"))
        rows.append((record["id"], record))
    rows.sort()

    traces = []
    for scan_id, record in rows:
        subject = record["subject_id"]
        alias = aliases.setdefault(subject, f"S{len(aliases) + 1}")
        decision = record["decision_json"]
        challenge = decision.get("challenge") or {}
        recorded = decision.get("head_sequence")
        origin = record["frame_meta"][0]["ts_ms"]
        traces.append(
            {
                "id": scan_id,
                "subject": alias,
                "label": record["label"],
                "endpoint": record["endpoint"],
                "policy": (decision.get("pad") or {}).get("policy"),
                "build_id": decision.get("build_id"),
                "action": challenge.get("action"),
                "params": challenge.get("params"),
                # Relative to frame 0; validate() uses frames[0] as its origin.
                "ts_rel_ms": [f["ts_ms"] - origin for f in record["frame_meta"]],
                "yaw": None if recorded is None else recorded.get("yaw"),
                "recorded": (
                    None
                    if recorded is None
                    else {k: recorded.get(k) for k in VERDICT_FIELDS}
                ),
            }
        )
    return traces


def build(source: Path = SOURCE, output: Path = OUTPUT) -> dict:
    traces = _traces(source)
    document = {
        "source": "read-only VPS capture metadata, 2026-09-20 pull",
        "contains": "timestamps, recorded yaw ratio pairs and recorded verdicts only",
        "excludes": "frames, scan payloads, embeddings, templates, user agents, "
        "real subject identifiers",
        "warning": "Nine v2-slow traces from four people are development data. "
        "They establish nothing about recognition accuracy or spoof resistance.",
        "traces": traces,
    }
    body = json.dumps(document, indent=2, sort_keys=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8", newline="\n")
    return {
        "output": str(output),
        "traces": len(traces),
        "replayable": sum(t["recorded"] is not None for t in traces),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
