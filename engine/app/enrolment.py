"""Enrolment polish (docs/LIVENESS_UPGRADE_PLAN.md Phase 5).

Enrolment uses the same capture as verification. Two things differ, both decided here as
plain records; main.py applies them:

- The enrolment photo: under a single-turn CHALLENGE_POLICY the template is made from one
  frame, the sharpest of the most frontal single-face settle frames, instead of the mean
  of the PAD frames. No separate photo step. The frame pool and the sharpness measure are
  the AENet ones (aenet.frontal_pool, aenet.crop, aenet.sharpness).
- A stricter bar: a fake face let in at enrolment unlocks the account for good, so the nose
  check must be conclusive and the glow must be conclusive or the light poor. The
  "inconclusive" shortcut that verification allows is not accepted. Each part is judged
  only when its check runs (PAD_GEOMETRY / FLASH_CHECK log or on) and enforced only when
  its enforcing flag is on: PAD_ENROL_GEOMETRY=on for the nose check (PAD_GEOMETRY itself
  is log-only everywhere, geometry.py), FLASH_CHECK=on for the glow.
"""

from app import aenet, challenge, flags, flash, geometry

PHOTO_POLICIES = (challenge.POLICY_SINGLE_TURN_V1, challenge.POLICY_SINGLE_TURN_GLOW_V1)
FRONTAL_CANDIDATES = 3
CROP_SCALE = 1.0
CROP_SIZE = 112

GEOMETRY = "pad_enrol_geometry"
# Enrolment's own switch for enforcing the nose check. PAD_GEOMETRY keeps one meaning,
# the one geometry.py documents: off, or run and log (`on` = `log`).
ENFORCE_GEOMETRY_ENV = "PAD_ENROL_GEOMETRY"
FLASH = "pad_enrol_flash"


def photo_wanted() -> bool:
    return challenge.selected_policy() in PHOTO_POLICIES


def photo(frames, tracked, settle_ms, decode) -> dict:
    """{index, yaw, sharpness, candidates} of the enrolment photo, or {index: None, reason}."""
    pool = aenet.frontal_pool(tracked, [f["ts_ms"] for f in frames], settle_ms)
    scored = []
    for i, yaw in pool[:FRONTAL_CANDIDATES]:
        image = decode(frames[i]["jpeg_bytes"])
        if image is None:
            continue
        try:
            patch = aenet.crop(image, tracked[i]["bbox"], CROP_SCALE, CROP_SIZE)
        except ValueError:
            continue
        scored.append((aenet.sharpness(patch), -abs(yaw), -i, i, yaw))
    if not scored:
        return {"index": None, "reason": "no_settle_frame", "candidates": len(pool)}
    sharp, _, _, index, yaw = max(scored)
    return {
        "index": index,
        "yaw": round(yaw, 4),
        "sharpness": round(sharp, 1),
        "candidates": len(scored),
    }


def geometry_mode() -> str:
    """off | log | on for the enrolment nose check: off while PAD_GEOMETRY is off (the check
    does not run), else `on` only when PAD_ENROL_GEOMETRY=on, else `log`."""
    if geometry.mode() == "off":
        return "off"
    return "on" if flags.enabled(ENFORCE_GEOMETRY_ENV, False) else "log"


def bar(geometry_record: dict | None, flash_record: dict | None) -> dict | None:
    """What the stricter enrolment bar decides, or None when neither check runs.

    A part is judged only when its check ran (flag log/on); `refuse` lists the failed
    parts whose flag is `on`, `would_refuse` every failed part."""
    parts, would, refuse = {}, [], []
    g_mode = geometry_mode()
    if g_mode != "off":
        outcome = (geometry_record or {}).get("outcome")
        parts["geometry"] = {
            "mode": g_mode,
            "outcome": outcome,
            "ok": outcome == "scored",
        }
    f_mode = flash.mode()
    if f_mode != flash.OFF:
        record = flash_record or {}
        outcome, reason = record.get("outcome"), record.get("reason")
        ok = outcome == "pass" or (outcome, reason) == ("inconclusive", "too_bright")
        parts["flash"] = {
            "mode": f_mode,
            "outcome": outcome,
            "reason": reason,
            "ok": ok,
        }
    if not parts:
        return None
    for name, code in (("geometry", GEOMETRY), ("flash", FLASH)):
        part = parts.get(name)
        if part is not None and not part["ok"]:
            would.append(code)
            if part["mode"] == "on":
                refuse.append(code)
    return {**parts, "would_refuse": would, "refuse": refuse}
