"use strict";
// outcome.js: the engine's refusal message becomes one reason the result page can act on,
// and a 429/503 Retry-After travels with the outcome. Strings here are the engine's own
// formats (engine/app/main.py: "liveness check failed: <signals>", "PAD <outcome>: <reason>").
const test = require("node:test");
const assert = require("node:assert/strict");

const load = () => import("../verify/outcome.js");
const refused = (classifyReply, message, error = "LIVENESS_FAIL") =>
  classifyReply("verify", { status: 422, body: { error, message, request_id: "r1" } });

test("each failed liveness signal maps to its own reason, in the engine's order", async () => {
  const { classifyReply } = await load();
  const reason = (message) => refused(classifyReply, message).scores?.reason ?? null;
  assert.equal(reason("liveness check failed: challenge"), "movement");
  assert.equal(reason("liveness check failed: landmarks"), "tracking");
  assert.equal(reason("liveness check failed: motion"), "notlive");
  assert.equal(reason("liveness check failed: duplicates"), "notlive");
  assert.equal(reason("liveness check failed: timing"), "slowcamera");
  assert.equal(reason("not enough usable frames to check liveness: landmarks, timing"), "tracking"); // first failed signal wins
  assert.equal(reason("liveness check failed: timing, challenge"), "slowcamera");
  assert.equal(reason("PAD spoof: non_live_frame"), "spoof");
  assert.equal(reason("PAD uncertain: too_few_frames"), "spoof");
  assert.equal(reason("liveness check failed: something_new"), null); // unknown signal: no advice invented
  assert.equal(reason("liveness check failed"), null);
  assert.equal(reason(undefined), null);
  assert.equal(refused(classifyReply, "liveness check failed: challenge", "LOW_QUALITY").scores, null); // reasons only for LIVENESS_FAIL
  assert.equal(refused(classifyReply, "liveness check failed: challenge").outcome, "liveness");
});

test("a refusal inside an accepted=false decision carries the same reason", async () => {
  const { classifyReply } = await load();
  const r = classifyReply("verify", { status: 200, body: { operation: "verify", accepted: false, request_id: "r2",
    decision: { error: "LIVENESS_FAIL", message: "liveness check failed: landmarks" } } });
  assert.deepEqual(r, { outcome: "liveness", recording: null, requestId: "r2", scores: { reason: "tracking" } });
});

test("Retry-After travels with a busy outcome and with nothing else", async () => {
  const { classifyReply } = await load();
  const busy = classifyReply("verify", { status: 429, body: { error: "BUSY", request_id: "r3" }, retryAfter: 20 });
  assert.deepEqual(busy, { outcome: "busy", recording: null, requestId: "r3", scores: null, retryAfter: 20 });
  const noHeader = classifyReply("verify", { status: 429, body: { error: "BUSY" }, retryAfter: null });
  assert.equal("retryAfter" in noHeader, false);
  const ok = classifyReply("verify", { status: 200, body: { operation: "verify", accepted: true, template_id: "t",
    decision: { match: true, score: 0.9, threshold: 0.5, liveness: { live: true, enforced: true, score: 0.8 }, pad: { outcome: "live" } } }, retryAfter: 20 });
  assert.equal("retryAfter" in ok, false);
});

test("pad_* codes (glow, the enrolment bar) share the one calm spoof line; enrol success copy", async () => {
  const { classifyReply } = await load();
  const reason = (message) => refused(classifyReply, message).scores?.reason ?? null;
  assert.equal(reason("liveness check failed: pad_flash"), "spoof");
  assert.equal(reason("liveness check failed: pad_enrol_geometry"), "spoof");
  assert.equal(reason("liveness check failed: pad_enrol_geometry, pad_enrol_flash"), "spoof");
  const { COPY } = await import("../verify/view.js");
  assert.deepEqual(COPY.SUCCESS.enroll, ["Your face<br> <em>is saved.</em>", "Your face template was saved to your account.", "Go to workspace"]);
  assert.equal(COPY.SUCCESS.verify[2], "Return to workspace");
});
