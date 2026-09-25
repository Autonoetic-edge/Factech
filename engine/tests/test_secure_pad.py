"""Security wiring uses mocks explicitly; real model tests are marked by name."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import anti_spoof as pad
from app import challenge, head_sequence, liveness, main, store
from helpers import AUTH, build_scan, scan_to_b64


def vector(index=0):
    v = np.zeros(512, dtype=np.float32)
    v[index] = 1
    return v


def detection(offset=0):
    return {
        "bbox": [10, 10, 54, 58],
        "det_score": 0.99,
        "landmarks_5": [[20, 24], [44, 24], [32 + offset, 35], [25, 46], [39, 46]],
    }


class FakePAD:
    def __init__(self, scores=(0.01, 0.98, 0.01)):
        self.scores = np.asarray(scores)

    def predict(self, image, bbox):
        return [self.scores, self.scores], self.scores


@pytest.fixture
def samples(monkeypatch):
    monkeypatch.setattr(pad, "get_pad", lambda: FakePAD())
    frames = build_scan()["frames"]
    for i, f in enumerate(frames):
        f["ts_ms"] = i * 1400
    return frames, [[detection()] for _ in frames]


def evaluate(samples, embedding=lambda f, d: vector()):
    return pad.evaluate(*samples, embedding, lambda b: np.zeros((64, 64, 3), np.uint8))[
        0
    ]


def test_all_frames_and_coverage(samples):
    result = evaluate(samples)
    assert result["outcome"] == "live"
    assert result["usable"] == 12 and result["coverage"] == [4, 4, 4]


@pytest.mark.parametrize(
    "scores",
    [
        (0.8, 0.1, 0.1),
        (0.1, 0.1, 0.8),
        (float("nan"), 0.9, 0.1),
        (-1, 1, 1),
        (0.1, 0.1, 0.1),
    ],
)
def test_attack_and_invalid_scores_fail_closed(samples, monkeypatch, scores):
    monkeypatch.setattr(pad, "get_pad", lambda: FakePAD(scores))
    assert evaluate(samples)["outcome"] in {"spoof", "inference_error"}


def test_one_bad_frame_is_not_cherry_picked(samples, monkeypatch):
    class Alternating(FakePAD):
        n = 0

        def predict(self, image, bbox):
            self.n += 1
            self.scores = np.array(
                [0.9, 0.05, 0.05] if self.n == 6 else [0.01, 0.98, 0.01]
            )
            return super().predict(image, bbox)

    model = Alternating()
    monkeypatch.setattr(pad, "get_pad", lambda: model)
    result = evaluate(samples)
    assert result["outcome"] == "spoof" and result["usable"] == 12


@pytest.mark.parametrize("missing", [[0, 1, 2, 3], [0, 1], [10, 11], [4, 5]])
def test_insufficient_temporal_coverage(samples, missing):
    for i in missing:
        samples[1][i] = []
    assert evaluate(samples)["outcome"] == "insufficient_evidence"


def test_face_switch_including_return_to_original(samples):
    count = 0

    def switched(frame, det):
        nonlocal count
        count += 1
        return vector(1 if count == 6 else 0)

    assert evaluate(samples, switched)["reason"] == "face_discontinuity"


def test_inference_exception(samples, monkeypatch):
    def broken():
        raise RuntimeError("broken")

    monkeypatch.setattr(pad, "get_pad", broken)
    assert evaluate(samples)["outcome"] == "inference_error"


def test_timeout(samples, monkeypatch):
    monkeypatch.setattr(pad, "SCAN_TIMEOUT_S", -1)
    assert evaluate(samples)["reason"] == "deadline"


@pytest.mark.models
def test_real_models_load_and_predict():
    model = pad.MiniFASNetPAD()
    members, ensemble = model.predict(
        np.zeros((160, 120, 3), np.uint8), [30, 40, 90, 120]
    )
    assert len(members) == 2
    pad.checked_scores(ensemble[None])


def test_missing_and_corrupt_models(tmp_path):
    with pytest.raises(FileNotFoundError):
        pad.MiniFASNetPAD(tmp_path)
    manifest = json.loads((pad.MODEL_DIR / "manifest.json").read_text())
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / manifest["models"][0]["onnx"]).write_bytes(b"not a model")
    with pytest.raises(ValueError, match="checksum"):
        pad.MiniFASNetPAD(tmp_path)


def issued_scan(monkeypatch, operation="enroll", user_id="test"):
    issued = challenge.issue(operation, user_id)
    challenge._issued[issued["nonce"]]["issued_ms"] -= 16000
    scan = build_scan()
    scan["challenge"] = issued
    for i, f in enumerate(scan["frames"]):
        f["ts_ms"] = i * 1400
    return scan


@pytest.mark.parametrize("operation", ["enroll", "verify", "liveness"])
@pytest.mark.parametrize(
    "outcome", ["spoof", "insufficient_evidence", "inference_error"]
)
def test_pad_blocks_every_acceptance_despite_perfect_identity_and_motion(
    monkeypatch, operation, outcome
):
    store.reset()
    store.enroll("test", vector())
    before = len(store.get_templates("test"))
    scan = issued_scan(
        monkeypatch, operation, "" if operation == "liveness" else "test"
    )
    monkeypatch.setenv("LIVENESS_ENFORCE", "0")  # Cannot disable secure acceptance.
    monkeypatch.setattr(
        main, "_scan_detections", lambda s: [[detection()] for _ in range(12)]
    )
    monkeypatch.setattr(
        pad,
        "evaluate",
        lambda *args: ({"outcome": outcome, "reason": "test", "usable": 12}, vector()),
    )
    good = liveness.LivenessResult(
        1, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *args: good)
    with TestClient(main.app, headers=AUTH) as client:
        response = client.post(
            "/v1/" + operation, json={"user_id": "test", "facescan": scan_to_b64(scan)}
        )
    assert response.status_code in (422, 503)
    assert len(store.get_templates("test")) == before
    assert (
        "template_id" not in response.json()
        and response.json().get("match") is not True
    )


def test_success_contract_with_explicit_mocked_biometric_evidence(monkeypatch):
    monkeypatch.setattr(
        main, "_scan_detections", lambda s: [[detection()] for _ in range(12)]
    )
    monkeypatch.setattr(
        pad,
        "evaluate",
        lambda *args: (
            {"outcome": "live", "usable": 12, "minimum_detection_score": 0.99},
            vector(),
        ),
    )
    good = liveness.LivenessResult(
        0.8, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *args: good)
    with TestClient(main.app, headers=AUTH) as client:
        for op in ("enroll", "verify", "liveness"):
            scan = issued_scan(monkeypatch, op, "" if op == "liveness" else "test")
            response = client.post(
                "/v1/" + op, json={"user_id": "test", "facescan": scan_to_b64(scan)}
            )
            assert response.status_code == 200, response.text
            assert response.json()["pad"]["outcome"] == "live"
            if op == "verify":
                assert (
                    response.json()["match"] is True
                    and response.json()["score"] > 0.999
                )
            repeated = client.post(
                "/v1/" + op, json={"user_id": "test", "facescan": scan_to_b64(scan)}
            )
            assert repeated.status_code == 422 and "reused_nonce" in repeated.text


@pytest.mark.parametrize(
    "change", ["operation", "identity", "expired", "params", "too_early"]
)
def test_nonce_binding_expiry_and_timing(monkeypatch, change):
    scan = issued_scan(monkeypatch)
    binding = ("enroll", "test")
    if change == "operation":
        binding = ("verify", "test")
    if change == "identity":
        binding = ("enroll", "other")
    if change == "expired":
        challenge._issued[scan["challenge"]["nonce"]]["expires_ms"] = 0
    if change == "params":
        scan["challenge"]["params"]["first_sign"] *= -1
    if change == "too_early":
        challenge._issued[scan["challenge"]["nonce"]]["issued_ms"] = challenge._now_ms()
    assert not challenge.consume_from_scan(scan, binding).ok
    assert not challenge.consume_from_scan(scan, binding).ok


@pytest.mark.parametrize("sign", [-1, 1])
def test_ordered_actual_landmark_rotation(sign):
    frames = [{"ts_ms": i * 1400} for i in range(12)]
    params = {"settle_ms": 3200, "switch_ms": 9000, "first_sign": sign, "target": 0.2}
    detections = [
        detection(0 if i < 3 else 3 * sign if i < 7 else -3 * sign) for i in range(12)
    ]
    assert head_sequence.validate(frames, detections, params)["ok"]
    assert not head_sequence.validate(frames, list(reversed(detections)), params)["ok"]
    flat = [detection() for _ in frames]
    for i, det in enumerate(flat):
        det["landmarks_5"] = (np.array(det["landmarks_5"]) * (1 + i / 20) + i).tolist()
    assert not head_sequence.validate(frames, flat, params)["ok"]


def test_secure_startup_and_readiness_fail_closed(monkeypatch):
    monkeypatch.setattr(pad, "_model", None)

    def absent():
        raise FileNotFoundError("PAD missing")

    monkeypatch.setattr(pad, "get_pad", absent)
    assert main.health().status_code == 503
    with pytest.raises(FileNotFoundError), TestClient(main.app):
        pass


@pytest.fixture
def wired_client(monkeypatch):
    """Real PAD aggregation/continuity wiring, fake model outputs and heuristic."""
    store.reset()
    monkeypatch.setattr(pad, "get_pad", lambda: FakePAD())
    monkeypatch.setattr(
        main, "_scan_detections", lambda s: [[detection()] for _ in range(12)]
    )
    monkeypatch.setattr(main, "embed_aligned", lambda a: vector())
    monkeypatch.setattr(
        main, "align_largest_face", lambda *a, **k: np.zeros((112, 112, 3), np.uint8)
    )
    good = liveness.LivenessResult(
        0.8, {"challenge": liveness.Signal("challenge", 1, {"ok": True})}
    )
    monkeypatch.setattr(liveness, "check_liveness", lambda *a: good)
    monkeypatch.setenv("FACETECH_DIAGNOSTIC_HEADER", "1")
    with TestClient(main.app, headers=AUTH) as client:
        yield client
    store.reset()


def send_bound(client, op, user="test", transport="json"):
    issued = client.get(
        "/v1/challenge", headers={"X-Facetech-Operation": op, "X-User-Id": user}
    ).json()
    challenge._issued[issued["nonce"]]["issued_ms"] -= 16000
    scan = build_scan(challenge=issued)
    for i, f in enumerate(scan["frames"]):
        f["ts_ms"] = i * 1400
    if transport == "json":
        return client.post(
            "/v1/" + op, json={"user_id": user, "facescan": scan_to_b64(scan)}
        )
    from helpers import scan_to_legacy_msgpack, scan_to_msgpack

    encoder = scan_to_msgpack if transport == "msgpack" else scan_to_legacy_msgpack
    return client.post(
        "/v1/" + op,
        content=encoder(scan),
        headers={"Content-Type": "application/msgpack", "X-User-Id": user},
    )


@pytest.mark.parametrize("transport", ["json", "msgpack", "legacy_msgpack"])
def test_contract_transport_canonical_identity_diagnostics_and_revoke(
    wired_client, transport
):
    enrollment = send_bound(wired_client, "enroll", "  Test  ", transport)
    assert enrollment.status_code == 200, enrollment.text
    assert enrollment.json()["quality"]["frames_embedded"] == 12
    assert len(store.get_templates("test")) == 1
    assert np.linalg.norm(store.get_templates("test")[0]["embedding"]) == pytest.approx(
        1
    )
    verification = send_bound(wired_client, "verify", "TEST", transport)
    assert verification.status_code == 200 and verification.json()["match"]
    diag = json.loads(verification.headers["X-Facetech-Decision"])
    assert diag["pad"]["usable"] == 12 and len(diag["pad"]["frames"]) == 12
    assert diag["head_sequence"]["ok"] and diag["match"]["similarity"] == 1
    assert diag["liveness"]["score"] == 0.8
    assert diag["thresholds"] == {"match": 0.55, "liveness": 0.5}
    assert len(verification.headers["X-Facetech-Decision"]) < 16384
    assert "jpeg" not in verification.headers["X-Facetech-Decision"]
    assert wired_client.delete("/v1/templates/TEST").status_code == 200
    assert send_bound(wired_client, "verify").status_code == 404


def test_wrong_person_is_no_match_after_required_gates(wired_client):
    store.enroll("test", vector(1))
    result = send_bound(wired_client, "verify")
    assert result.status_code == 200 and not result.json()["match"]


@pytest.mark.parametrize("mode", ["none", "multi", "swap"])
def test_endpoints_reject_bad_face_evidence_before_enrollment(
    wired_client, monkeypatch, mode
):
    if mode == "none":
        monkeypatch.setattr(main, "_scan_detections", lambda s: [[] for _ in range(12)])
    if mode == "multi":
        monkeypatch.setattr(
            main,
            "_scan_detections",
            lambda s: [[detection(), detection()] for _ in range(12)],
        )
    if mode == "swap":
        n = [0]

        def switched(a):
            n[0] += 1
            return vector(1 if n[0] == 5 else 0)

        monkeypatch.setattr(main, "embed_aligned", switched)
    response = send_bound(wired_client, "enroll")
    assert response.status_code == 422 and not store.has_user("test")
    assert (
        json.loads(response.headers["X-Facetech-Decision"])["pad"]["outcome"]
        == "insufficient_evidence"
    )


@pytest.mark.models
def test_actual_pad_decision_cannot_be_overridden_in_wired_pipeline(
    wired_client, monkeypatch
):
    # Real ONNX on synthetic uniform pixels; this proves enforcement, not efficacy.
    model = pad.MiniFASNetPAD()
    monkeypatch.setattr(pad, "get_pad", lambda: model)
    store.enroll("test", vector())
    for op in ("enroll", "verify", "liveness"):
        response = send_bound(wired_client, op)
        assert response.status_code == 422, response.text
        diag = json.loads(response.headers["X-Facetech-Decision"])
        assert diag["pad"]["outcome"] == "spoof"
        assert diag.get("match") is None
        assert diag["liveness"]["live"]
    assert len(store.get_templates("test")) == 1

@pytest.mark.parametrize('sign', [-1, 1])
@pytest.mark.parametrize('switch', [9000, 9200, 9400])
@pytest.mark.parametrize('cadence', [1400, 1490, 1700])
def test_slow_sequence_accepts_order_and_phone_timing_jitter(sign, switch, cadence):
    frames = [{'ts_ms': i * cadence} for i in range(12)]
    params = {'settle_ms': 3200, 'switch_ms': switch, 'first_sign': sign, 'target': .2}
    detections = [detection(0 if f['ts_ms'] <= 3200 else 3 * sign if f['ts_ms'] < switch else -3 * sign) for f in frames]
    assert head_sequence.validate(frames, detections, params)['ok']
    verdict = challenge.ChallengeVerdict(True, None, challenge.HEAD_SEQUENCE, challenge.HEAD_SEQUENCE, params, 'test', True)
    timing = liveness._timing_signal(frames, verdict)
    assert timing.score >= .5
    assert timing.detail['expected_cadence_ms'] == 1400

@pytest.mark.parametrize('cadence', [700, 1000, 1950])
def test_slow_sequence_rejects_fast_old_or_overlong_capture(cadence):
    params = {'settle_ms': 3200, 'switch_ms': 9000, 'first_sign': 1, 'target': .2}
    frames = [{'ts_ms': i * cadence} for i in range(12)]
    assert head_sequence.validate(frames, [detection()] * 12, params)['reason'] == 'timing'

def test_slow_pad_allows_one_unavailable_sample_but_not_two(samples):
    samples[1][5] = []
    assert evaluate(samples)['outcome'] == 'live'
    samples[1][6] = []
    assert evaluate(samples)['reason'] == 'coverage_gap'
