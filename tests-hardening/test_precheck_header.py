"""The page's pre-check reading reaches the engine trace (LIVENESS_UPGRADE_PLAN Phase 2).

Trace data only: bounded text through the gateway, parsed and recorded by the engine's
trace, never part of a decision.
"""

import json
from pathlib import Path

import pytest
from auth_fixtures import Harness, run, scan_request
from facetech_auth.http import PRECHECK_HEADER, PRECHECK_MAX

ROOT = Path(__file__).resolve().parents[1]
READING = json.dumps(
    {"luma": 81.5, "frame_luma": 140.2, "backlight": 58.7, "sharp": 33.1}
)


@pytest.fixture
def h():
    return Harness()


@pytest.mark.parametrize(
    ("sent", "received"),
    [
        (None, None),
        (READING, READING),
        ("x" * (PRECHECK_MAX + 1), None),
        ("tab\there", None),
    ],
)
def test_gateway_passes_the_reading_to_the_engine_bounded(h, sent, received):
    browser = run(h.login())
    kwargs = scan_request()
    if sent is not None:
        kwargs["headers"][PRECHECK_HEADER] = sent
    response = run(
        h.request("POST", "/v2/subjects/subject-alice/enroll", browser, **kwargs)
    )
    assert response.status_code == 200
    (_, payload), *_ = h.engine_ops.calls
    assert payload["precheck"] == received


def test_frozen_analyzer_records_the_reading_in_the_decision_trace(monkeypatch):
    from facetech_auth.inference import FrozenAnalyzer

    analyzer = FrozenAnalyzer(ROOT / "engine", warm=False)
    lines = []
    monkeypatch.setattr(
        analyzer.trace.Trace, "emit", lambda self, status: lines.append(self.line(status))
    )
    challenge = {"subject": "s", "action": "LOOK_LEFT", "params": {}, "age_ms": 0}
    run(
        analyzer.analyze(
            "verify", b"not a scan", (), request_id="r" * 16, challenge=challenge,
            precheck=READING,
        )  # fmt: skip
    )
    run(
        analyzer.analyze(
            "verify", b"not a scan", (), request_id="s" * 16, challenge=challenge
        )
    )
    assert lines[0]["precheck"]["luma"] == 81.5
    assert lines[0]["precheck"]["backlight"] == 58.7
    assert "precheck" not in lines[1]
