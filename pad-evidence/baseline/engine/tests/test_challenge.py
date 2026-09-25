import time

import pytest
from fastapi.testclient import TestClient

from app import challenge, liveness, store
from app.main import app
from helpers import AUTH, live_scan, renonce, scan_to_b64
from model_guard import requires_models

client = TestClient(app, headers=AUTH)


@pytest.fixture(autouse=True)
def _clean():
    store.reset()
    challenge.reset()
    yield
    store.reset()
    challenge.reset()


@pytest.fixture
def low_det_thresh(monkeypatch):
    from app.detect import get_detector

    monkeypatch.setattr(get_detector(), "det_thresh", 0.2)


@pytest.fixture
def enrolled(low_det_thresh):
    r = client.post(
        "/v1/enroll",
        json={"user_id": "alice", "facescan": scan_to_b64(live_scan("face_like.jpg"))},
    )
    assert r.status_code == 200, r.json()
    return "alice"


def frozen_clock(monkeypatch, at_ms: int) -> None:
    monkeypatch.setattr(challenge, "_now_ms", lambda: at_ms)


def just_expired() -> int:
    return int(time.time() * 1000) + challenge.EXPIRY_MS + 1


def test_challenge_endpoint_shape():
    body = client.get("/v1/challenge").json()
    assert set(body) == {"nonce", "action", "params", "issued_ms", "expires_ms"}
    assert len(body["nonce"]) == 2 * challenge.NONCE_BYTES
    int(body["nonce"], 16)
    assert body["action"] in challenge.ISSUABLE_ACTIONS
    assert body["expires_ms"] - body["issued_ms"] == challenge.EXPIRY_MS


def test_every_challenge_is_a_new_nonce():
    nonces = {client.get("/v1/challenge").json()["nonce"] for _ in range(25)}
    assert len(nonces) == 25
    assert challenge.outstanding() == 25


def test_challenge_requires_the_engine_key():
    r = TestClient(app).get("/v1/challenge")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_blink_twice_is_never_issued():
    assert challenge.BLINK_TWICE in challenge.ACTIONS
    assert challenge.BLINK_TWICE not in challenge.ISSUABLE_ACTIONS
    actions = {client.get("/v1/challenge").json()["action"] for _ in range(60)}
    assert challenge.BLINK_TWICE not in actions
    assert actions == set(challenge.ISSUABLE_ACTIONS), (
        "every issuable action must occur"
    )


def test_move_closer_is_what_the_engine_asks_for():
    assert challenge.ISSUABLE_ACTIONS == (challenge.MOVE_CLOSER,)
    assert {challenge.LOOK_LEFT, challenge.LOOK_RIGHT} <= set(challenge.ACTIONS)
    assert client.get("/v1/challenge").json()["action"] == challenge.MOVE_CLOSER


def test_a_freshly_issued_nonce_checks_out():
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is True
    assert verdict.reason is None
    assert verdict.action == issued["action"]
    assert verdict.consumed is True


def test_a_nonce_is_single_use():
    issued = challenge.issue()
    assert challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    ).ok
    second = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert second.ok is False
    assert second.reason == challenge.REUSED_NONCE


def test_an_unknown_nonce_is_rejected():
    verdict = challenge.check(
        "00" * challenge.NONCE_BYTES, challenge.LOOK_LEFT, spend=True
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.UNKNOWN_NONCE
    assert verdict.action is None, "no record, so nothing to say about the action"


def test_a_missing_nonce_is_rejected_not_waved_through():
    verdict = challenge.check("", None, spend=True)
    assert verdict.ok is False
    assert verdict.reason == challenge.MISSING_NONCE


def test_an_expired_nonce_is_rejected(monkeypatch):
    issued = challenge.issue()
    frozen_clock(monkeypatch, issued["expires_ms"] + 1)
    verdict = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.EXPIRED_NONCE


def test_a_nonce_is_still_valid_on_its_last_millisecond(monkeypatch):
    issued = challenge.issue()
    frozen_clock(monkeypatch, issued["expires_ms"])
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=True,
            presented_params=issued["params"],
        ).ok
        is True
    )


def test_a_mismatched_action_is_rejected():
    issued = challenge.issue()
    other = (
        challenge.LOOK_LEFT
        if issued["action"] != challenge.LOOK_LEFT
        else challenge.LOOK_RIGHT
    )
    verdict = challenge.check(
        issued["nonce"], other, spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.ACTION_MISMATCH
    assert verdict.action == issued["action"]
    assert verdict.presented_action == other


def test_an_action_outside_the_vocabulary_is_rejected():
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"], "LOOK_UP", spend=True, presented_params=issued["params"]
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.UNKNOWN_ACTION


def test_a_failed_check_still_spends_the_nonce():
    issued = challenge.issue()
    assert (
        challenge.check(
            issued["nonce"], "LOOK_UP", spend=True, presented_params=issued["params"]
        ).ok
        is False
    )
    again = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert again.reason == challenge.REUSED_NONCE


def test_inspect_does_not_spend():
    issued = challenge.issue()
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=False,
            presented_params=issued["params"],
        ).ok
        is True
    )
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=False,
            presented_params=issued["params"],
        ).ok
        is True
    )
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=True,
            presented_params=issued["params"],
        ).ok
        is True
    )
    assert (
        challenge.check(
            issued["nonce"],
            issued["action"],
            spend=True,
            presented_params=issued["params"],
        ).reason
        == challenge.REUSED_NONCE
    )


def test_long_expired_records_are_evicted(monkeypatch):
    issued = challenge.issue()
    assert challenge.outstanding() == 1
    frozen_clock(
        monkeypatch, issued["expires_ms"] + challenge.RETAIN_AFTER_EXPIRY_MS + 1
    )
    challenge.issue()
    assert challenge.outstanding() == 1
    verdict = challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=issued["params"]
    )
    assert verdict.reason == challenge.UNKNOWN_NONCE, "evicted, so unknown now"


def test_the_store_is_capped(monkeypatch):
    monkeypatch.setattr(challenge, "MAX_ISSUED_NONCES", 8)
    issued = [challenge.issue() for _ in range(50)]
    assert challenge.outstanding() <= 8

    assert challenge.check(
        issued[-1]["nonce"],
        issued[-1]["action"],
        spend=True,
        presented_params=issued[-1]["params"],
    ).ok
    assert (
        challenge.check(
            issued[0]["nonce"],
            issued[0]["action"],
            spend=True,
            presented_params=issued[0]["params"],
        ).reason
        == challenge.UNKNOWN_NONCE
    )


def test_issued_params_are_in_band():
    ceiling = (
        liveness.SCALE_DELTA_FLOOR
        + (liveness.SCALE_DELTA_LIVE - liveness.SCALE_DELTA_FLOOR)
        * challenge.TARGET_BAND_FRACTION
    )
    for _ in range(50):
        params = challenge.issue()["params"]
        assert challenge.SETTLE_MS_MIN <= params["settle_ms"] <= challenge.SETTLE_MS_MAX
        assert liveness.SCALE_DELTA_FLOOR <= params["target"] <= ceiling


def test_issued_params_vary_between_nonces():
    drawn = {
        (p["settle_ms"], p["target"])
        for p in (challenge.issue()["params"] for _ in range(60))
    }
    assert len(drawn) > 10, "params are not being redrawn per nonce"


def test_params_are_stored_and_echoed_in_the_verdict():
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"],
        issued["action"],
        spend=True,
        presented_params=issued["params"],
    )
    assert verdict.ok is True
    assert verdict.params == issued["params"]
    assert verdict.as_dict()["params"] == issued["params"]


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda p: {**p, "settle_ms": p["settle_ms"] + 100}, id="settle"),
        pytest.param(lambda p: {**p, "target": p["target"] + 0.05}, id="target"),
        pytest.param(lambda p: {"settle_ms": p["settle_ms"]}, id="no-target"),
        pytest.param(lambda p: None, id="absent"),
    ],
)
def test_params_that_are_not_the_issued_ones_are_rejected(mangle):
    issued = challenge.issue()
    verdict = challenge.check(
        issued["nonce"],
        issued["action"],
        spend=True,
        presented_params=mangle(issued["params"]),
    )
    assert verdict.ok is False
    assert verdict.reason == challenge.PARAMS_MISMATCH


def test_a_target_echoed_with_a_little_float_noise_is_accepted():
    issued = challenge.issue()
    noisy = {**issued["params"], "target": issued["params"]["target"] + 1e-9}
    assert challenge.check(
        issued["nonce"], issued["action"], spend=True, presented_params=noisy
    ).ok


def test_lowering_the_cap_converges(monkeypatch):
    monkeypatch.setattr(challenge, "MAX_ISSUED_NONCES", 20)
    for _ in range(19):
        challenge.issue()
    monkeypatch.setattr(challenge, "MAX_ISSUED_NONCES", 4)
    challenge.issue()
    assert challenge.outstanding() <= 4


@requires_models
def test_the_valid_flow_passes(enrolled):
    scan = live_scan("face_like.jpg")
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["match"] is True
    assert body["liveness"]["live"] is True
    assert body["liveness"]["failed_signals"] == []


@requires_models
def test_verify_rejects_a_reused_nonce(enrolled):
    scan = scan_to_b64(live_scan("face_like.jpg"))
    first = client.post("/v1/verify", json={"user_id": enrolled, "facescan": scan})
    assert first.status_code == 200

    second = client.post("/v1/verify", json={"user_id": enrolled, "facescan": scan})
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert "reused" in second.json()["error"]["message"]
    assert "score" not in second.json(), "a rejected replay learns nothing"


@requires_models
def test_verify_rejects_an_unknown_nonce(enrolled):
    scan = live_scan("face_like.jpg")
    scan["challenge"]["nonce"] = "ff" * challenge.NONCE_BYTES
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert "unknown" in r.json()["error"]["message"]


@requires_models
def test_verify_rejects_an_expired_nonce(enrolled, monkeypatch):
    scan = live_scan("face_like.jpg")
    frozen_clock(monkeypatch, just_expired())
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert "expired" in r.json()["error"]["message"]


@requires_models
def test_verify_rejects_a_nonce_issued_for_the_other_action(enrolled):
    scan = live_scan("face_like.jpg", action=challenge.LOOK_LEFT)
    scan["challenge"]["action"] = challenge.LOOK_RIGHT
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert "mismatch" in r.json()["error"]["message"]


@requires_models
def test_verify_rejects_a_scan_that_echoed_the_wrong_params(enrolled):
    scan = live_scan("face_like.jpg")
    scan["challenge"]["params"] = {
        **scan["challenge"]["params"],
        "settle_ms": scan["challenge"]["params"]["settle_ms"] + 250,
    }
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"


@requires_models
def test_verify_rejects_a_scan_with_no_challenge_at_all(enrolled):
    scan = live_scan("face_like.jpg")
    scan["challenge"] = {"id": "ch-1", "results": []}
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert "missing" in r.json()["error"]["message"]


@requires_models
def test_enroll_rejects_a_bad_nonce_and_enrolls_nothing(low_det_thresh):
    scan = live_scan("face_like.jpg")
    scan["challenge"]["nonce"] = "ab" * challenge.NONCE_BYTES
    r = client.post(
        "/v1/enroll", json={"user_id": "mallory", "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CHALLENGE_FAIL"
    assert not store.has_user("mallory")


@requires_models
def test_a_no_face_scan_keeps_its_challenge(low_det_thresh):
    scan = live_scan("blank.jpg")
    nonce = scan["challenge"]["nonce"]
    r = client.post("/v1/enroll", json={"user_id": "u", "facescan": scan_to_b64(scan)})
    assert r.json()["error"]["code"] == "NO_FACE"
    assert challenge.check(
        nonce,
        scan["challenge"]["action"],
        spend=False,
        presented_params=scan["challenge"]["params"],
    ).ok


@requires_models
def test_liveness_endpoint_reports_a_bad_nonce_without_erroring(low_det_thresh):
    scan = live_scan("face_like.jpg")
    scan["challenge"]["nonce"] = "cd" * challenge.NONCE_BYTES
    r = client.post("/v1/liveness", json={"facescan": scan_to_b64(scan)})
    assert r.status_code == 200
    body = r.json()
    assert body["live"] is False
    assert body["signals"]["challenge"]["nonce_ok"] is False
    assert body["signals"]["challenge"]["reason"] == challenge.UNKNOWN_NONCE


@requires_models
def test_liveness_endpoint_leaves_the_nonce_spendable(low_det_thresh, enrolled):
    scan = live_scan("face_like.jpg")
    assert (
        client.post("/v1/liveness", json={"facescan": scan_to_b64(scan)}).json()["live"]
        is True
    )
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 200, r.json()
    assert r.json()["match"] is True


@requires_models
def test_enforcement_off_lets_a_dead_nonce_through_but_still_reports_it(
    low_det_thresh, enrolled, monkeypatch
):
    monkeypatch.setenv(liveness.ENFORCE_ENV, "0")
    scan = live_scan("face_like.jpg")
    scan["challenge"]["nonce"] = "ef" * challenge.NONCE_BYTES
    r = client.post(
        "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
    )
    assert r.status_code == 200
    assert r.json()["liveness"]["live"] is False
    assert r.json()["liveness"]["enforced"] is False
    assert "challenge" in r.json()["liveness"]["failed_signals"]


@requires_models
def test_a_second_attempt_needs_a_second_challenge(enrolled):
    scan = live_scan("face_like.jpg")
    assert (
        client.post(
            "/v1/verify", json={"user_id": enrolled, "facescan": scan_to_b64(scan)}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/verify",
            json={"user_id": enrolled, "facescan": scan_to_b64(renonce(scan))},
        ).status_code
        == 200
    )
