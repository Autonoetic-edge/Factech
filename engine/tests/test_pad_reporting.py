import json

import pytest

from eval import pad


def scan(value):
    return {"signals": {"depth": {"depth_index": value}}}


def report_for(corpus):
    measurement = pad.MEASUREMENTS[0]
    entry = pad.sweep(corpus, measurement)
    point = entry["operating_point"]
    return {
        "generated": "2026-09-19",
        "corpus": {
            name: {"scans": len(scans), "errors": 0} for name, scans in corpus.items()
        },
        "missing": [name for name in pad.ATTACK_TYPES if name not in corpus],
        "measurements": [entry],
        "rules": {
            measurement.name: measurement.rule(point["threshold"])
            if point
            else "no usable operating point"
        },
    }


@pytest.mark.parametrize(
    "invalid", [None, True, "0.3", float("nan"), float("inf"), -float("inf")]
)
def test_unavailable_measurements_are_counted_without_entering_rates(invalid):
    entry = pad.sweep(
        {"bona_fide": [scan(0.8), scan(invalid)], "print": [scan(0.1), scan(invalid)]},
        pad.MEASUREMENTS[0],
    )
    for name in ("bona_fide", "print"):
        assert entry["distributions"][name]["n"] == 1
        assert entry["distributions"][name]["unusable"] == 1
    assert all(row["bpcer"]["total"] == 1 for row in entry["sweep"])
    assert all(row["apcer"]["print"]["total"] == 1 for row in entry["sweep"])
    json.dumps(entry, allow_nan=False)


def test_perfect_small_corpus_is_still_only_an_in_sample_candidate():
    report = report_for({"bona_fide": [scan(0.8)], "print": [scan(0.1)]})
    point = report["measurements"][0]["operating_point"]
    assert point["bpcer"]["rate"] == 0
    assert point["apcer_worst"] == 0
    text = pad.render_report(report)
    for required in (
        "Exploratory only; not deployment validation",
        "In-sample candidate",
        "Denominators include finite measurements only",
        "Keep advisory signals advisory",
        "independent held-out",
        "does not validate nonce freshness or replay protection",
        "repeated scans from the same participant",
        "Missing classes:",
    ):
        assert required in text
    assert "ready to stop being advisory" not in text
    assert "Rates are ISO/IEC 30107-3" not in text


def test_no_valid_attacks_produce_no_candidate_or_movement_claim():
    report = report_for({"bona_fide": [scan(0.8)], "print": [scan(None)]})
    assert report["measurements"][0]["operating_point"] is None
    text = pad.render_report(report)
    assert "No attack scan produced a value" in text
    assert "unusable landmarks" in text
    assert "attacks never approached" not in text


def test_json_report_carries_scope_without_a_real_capture(tmp_path, monkeypatch):
    corpus = {"bona_fide": [scan(0.8)], "print": [scan(0.1)]}
    monkeypatch.setattr(pad, "load_detector", lambda model: None)
    monkeypatch.setattr(pad, "read_corpus", lambda root: (corpus, []))
    out = tmp_path / "report"
    assert pad.main(["--data", str(tmp_path), "--out", str(out)]) == 0
    report = json.loads((out / "pad.json").read_text(encoding="utf-8"))
    scope = report["evaluation_scope"]
    assert scope["deployment_ready"] is False
    assert scope["held_out_validation"] is False
    assert scope["rate_denominator"] == "finite_measurements_only"
    assert scope["threshold_selection"] == "same_corpus_as_reported_rates"
    assert scope["challenge_validation"] == "synthetic_verdict_no_nonce_validation"
