"use strict";
// The AmFatec verification page's rules, tested without a browser:
// state transitions, cancellation, duplicate starts, server-authoritative results.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const load = (name) => import("../verify/" + name);

function run(flow, events, state = flow.initial()) {
  const effects = [];
  for (const e of events) {
    const r = flow.step(state, e);
    state = r.state;
    if (r.effect) effects.push(r.effect);
  }
  return { state, effects };
}
const P = { type: "PRIMARY" };
const READY = { type: "PREPARED" };
const GOOD = { type: "GUIDE", line: "good" }; // the face guide says the face is well placed
const toCamera = [{ type: "LOADED", mode: "verify" }, P, { type: "CAMERA_OK" }, GOOD];

test("first visit shows the agreement; returning visit starts at camera preparation", async () => {
  const flow = await load("flow.js");
  assert.equal(run(flow, [{ type: "LOADED", mode: "enroll" }]).state.view, "intro");
  assert.equal(run(flow, [{ type: "LOADED", mode: "verify" }]).state.view, "ready");
});

test("the camera is only requested by the Enable camera click", async () => {
  const flow = await load("flow.js");
  const loaded = run(flow, [{ type: "LOADED", mode: "enroll" }]);
  assert.deepEqual(loaded.effects, []);
  const agreed = run(flow, [P], loaded.state);
  assert.deepEqual(agreed.effects, ["grantConsent"]); // not the camera
  const demo = run(flow, [{ type: "CONSENT_OK" }], agreed.state);
  assert.equal(demo.state.view, "demo"); // first-time set-up: the demo comes by itself, camera off
  const ready = run(flow, [P], demo.state);
  assert.deepEqual([...demo.effects, ...ready.effects], []);
  assert.deepEqual(run(flow, [P], ready.state).effects, ["openCamera"]);
});

test("the demo is optional: automatic once for a first-time set-up, a link otherwise, always skippable", async () => {
  const flow = await load("flow.js");
  const first = run(flow, [{ type: "LOADED", mode: "enroll" }, P, { type: "CONSENT_OK" }]);
  assert.equal(first.state.view, "demo");
  const skipped = run(flow, [{ type: "SKIP" }], first.state);
  assert.deepEqual([skipped.state.view, skipped.effects], ["ready", []]);
  // once: agreeing again later in the same visit goes straight to Prepare
  const again = run(flow, [P, { type: "CONSENT_OK" }], { ...skipped.state, view: "intro" });
  assert.equal(again.state.view, "ready");
  // a returning account is never sent there, but can ask for it
  const back = run(flow, [{ type: "LOADED", mode: "verify" }]);
  assert.equal(back.state.view, "ready");
  const asked = run(flow, [{ type: "DEMO" }], back.state);
  assert.deepEqual([asked.state.view, asked.effects], ["demo", []]);
  // not while the camera is being opened
  assert.equal(run(flow, [P, { type: "DEMO" }], back.state).state.view, "ready");
});

test("the close button on the camera screen turns the camera off and starts nothing", async () => {
  const flow = await load("flow.js");
  const r = run(flow, [...toCamera, { type: "STOP" }]);
  assert.deepEqual([r.state.view, r.state.cameraLive, r.state.cameraError], ["ready", false, null]);
  assert.deepEqual(r.effects, ["openCamera", "startGuide", "prepare", "stopCamera"]); // "good" arms the auto-start; nothing is captured
});

test("consent is only 'granted' after the server says so", async () => {
  const flow = await load("flow.js");
  const r = run(flow, [{ type: "LOADED", mode: "enroll" }, P]);
  assert.equal(r.state.view, "intro"); // still waiting on the server
  assert.equal(run(flow, [{ type: "CONSENT_FAILED", outcome: "expired" }], r.state).state.outcome, "expired");
});

test("practice buttons move the guide and never start, advance or pass a check", async () => {
  const flow = await load("flow.js");
  const demo = run(flow, [{ type: "LOADED", mode: "verify" }, { type: "DEMO" }]);
  const r = run(flow, [{ type: "PREVIEW", pose: "left" }, { type: "PREVIEW", pose: "right" }], demo.state);
  assert.equal(r.state.view, "demo");
  assert.equal(r.state.previewPose, "right");
  assert.deepEqual(r.effects, []);
  // and they are dead once the camera is on, before and during a capture
  const cam = run(flow, toCamera);
  assert.deepEqual(run(flow, [{ type: "PREVIEW", pose: "left" }], cam.state).state, cam.state);
  const cap = run(flow, [P], cam.state);
  const during = run(flow, [{ type: "PREVIEW", pose: "left" }], cap.state);
  assert.deepEqual(during.state, cap.state);
});

test("a double click on Start begins exactly one scan", async () => {
  const flow = await load("flow.js");
  const r = run(flow, [...toCamera, P, READY, P, P]);
  assert.deepEqual(r.effects.filter((e) => e === "startScan"), ["startScan"]);
});

test("Start does nothing without a live camera", async () => {
  const flow = await load("flow.js");
  const cam = run(flow, [...toCamera, { type: "CAMERA_STOPPED" }]);
  assert.deepEqual(run(flow, [P], cam.state).effects, []);
});

test("capture progress comes from SDK events only", async () => {
  const flow = await load("flow.js");
  const cap = run(flow, [...toCamera, P, READY]);
  assert.equal(cap.state.cue, null); // nothing invented before the SDK speaks
  const r = run(flow, [
    { type: "SDK_CUE", pose: "forward", text: "a" },
    { type: "SDK_CUE", pose: "forward", text: "a" },
    { type: "SDK_CUE", pose: "right", text: "b" },
    { type: "SDK_FRAME", index: 4, total: 12 },
  ], cap.state);
  assert.deepEqual(r.state.cue, { pose: "right", text: "b", count: 2 });
  assert.deepEqual(r.state.frame, { index: 4, total: 12 });
});

test("the stage line and the frame count are drawn from SDK state only", async () => {
  const { stageText } = await load("view.js");
  const flow = await load("flow.js");
  const cap = run(flow, [...toCamera, P, READY]);
  assert.deepEqual(stageText(cap.state), { line: "starting", sub: "" }); // Start pressed, SDK silent: hold still, nothing counted
  const cue = (pose) => ({ type: "SDK_CUE", pose, text: pose });
  const s1 = run(flow, [cue("forward"), { type: "SDK_FRAME", index: 1, total: 12 }], cap.state).state;
  assert.deepEqual(stageText(s1), { line: "forward", sub: "Keep holding · Frame 1 of 12" });
  const s3 = run(flow, [cue("left"), cue("right"), { type: "SDK_FRAME", index: 11, total: 12 }], s1).state;
  assert.notEqual(s3, s1); // every frame is a new state, so the page repaints the ring
  assert.deepEqual(stageText(s3), { line: "right", sub: "Almost done. Keep holding · Frame 11 of 12" });
  assert.equal(stageText(run(flow, [{ type: "SDK_UPLOADING" }], s3).state).line, "checking");
});

test("Stop and a hidden tab cancel a capture that was not sent", async () => {
  const flow = await load("flow.js");
  const cap = run(flow, [...toCamera, P, READY]);
  assert.deepEqual(run(flow, [{ type: "STOP" }], cap.state).effects, ["cancelScan"]);
  assert.deepEqual(run(flow, [{ type: "HIDDEN" }], cap.state).effects, ["cancelScan"]);
  const done = run(flow, [{ type: "RESULT", outcome: "cancelled" }], cap.state);
  assert.equal(done.state.view, "result");
  assert.equal(done.state.cameraLive, false);
});

test("once sent, nothing but the server's answer changes the screen", async () => {
  const flow = await load("flow.js");
  const sent = run(flow, [...toCamera, P, READY, { type: "SDK_UPLOADING" }]);
  assert.equal(sent.state.view, "processing");
  for (const e of [{ type: "HIDDEN" }, { type: "STOP" }, P, { type: "PREVIEW", pose: "left" }, { type: "SESSION_EXPIRED" }]) {
    const r = run(flow, [e], sent.state);
    assert.deepEqual(r.state, sent.state, e.type);
    assert.deepEqual(r.effects, [], e.type);
  }
});

test("an uncertain submission rechecks the same scan and never starts a new one", async () => {
  const flow = await load("flow.js");
  const sent = run(flow, [...toCamera, P, READY, { type: "SDK_UPLOADING" }, { type: "RESULT", outcome: "uncertain" }]);
  const again = run(flow, [P, P], sent.state);
  assert.deepEqual(again.effects, ["recheck"]); // one, even on a double click
  const pending = run(flow, [{ type: "RECHECKED", outcome: "pending" }], again.state);
  assert.equal(pending.state.outcome, "pending");
  assert.deepEqual(run(flow, [P], pending.state).effects, ["recheck"]);
  assert.equal(run(flow, [{ type: "RECHECKED", outcome: "success" }], again.state).state.outcome, "success");
});

test("recovery actions: success leaves, expired signs in, failures go back to Prepare", async () => {
  const flow = await load("flow.js");
  const at = (outcome) => run(flow, [...toCamera, P, READY, { type: "RESULT", outcome }]).state;
  assert.deepEqual(run(flow, [P], at("success")).effects, ["goWorkspace"]);
  for (const o of ["expired", "expiring", "reauth"]) assert.deepEqual(run(flow, [P], at(o)).effects, ["goSignin"], o);
  for (const o of ["quality", "liveness", "nomatch", "busy", "cancelled", "retry", "server"]) {
    const r = run(flow, [P], at(o));
    assert.equal(r.state.view, "ready", o);
    assert.deepEqual(r.effects, ["forgetScan"], o);
  }
  assert.equal(run(flow, [P], at("consent")).state.view, "intro");
  const ne = run(flow, [P], at("notenrolled")).state;
  assert.deepEqual([ne.view, ne.mode], ["intro", "enroll"]);
});

test("camera failures return to Prepare with the reason, not to a result", async () => {
  const flow = await load("flow.js");
  for (const o of ["denied", "nocamera", "cameralost"]) {
    const s = run(flow, [...toCamera, P, READY, { type: "RESULT", outcome: o }]).state;
    assert.deepEqual([s.view, s.cameraError, s.cameraLive], ["ready", o, false]);
  }
  const denied = run(flow, [{ type: "LOADED", mode: "verify" }, P, { type: "CAMERA_FAILED", error: "denied" }]).state;
  assert.deepEqual([denied.view, denied.cameraError, denied.busy], ["ready", "denied", null]);
});

// ---- server-authoritative results -------------------------------------------------

const LIVE = { live: true, enforced: true };
const PAD = { outcome: "live", policy: "p" };
const okVerify = { operation: "verify", operation_id: "o", accepted: true, decision: { match: true, score: 0.8, threshold: 0.55, liveness: LIVE, pad: PAD } };
const okEnroll = { operation: "enroll", operation_id: "o", accepted: true, template_id: "t1", decision: { liveness: LIVE, pad: PAD } };

test("success needs every server predicate, not just HTTP 200", async () => {
  const { classifyReply } = await load("outcome.js");
  assert.equal(classifyReply("verify", { status: 200, body: okVerify }).outcome, "success");
  assert.equal(classifyReply("enroll", { status: 200, body: okEnroll }).outcome, "success");

  const broken = [
    ["verify", { ...okVerify, accepted: "true" }],
    ["verify", { ...okVerify, operation: "enroll" }],
    ["verify", { ...okVerify, decision: { ...okVerify.decision, match: false } }],           // accepted but no match
    ["verify", { ...okVerify, decision: { ...okVerify.decision, liveness: { live: true, enforced: false } } }],
    ["verify", { ...okVerify, decision: { ...okVerify.decision, liveness: undefined } }],
    ["verify", { ...okVerify, decision: { ...okVerify.decision, pad: { outcome: "unknown" } } }],
    ["enroll", { ...okEnroll, template_id: "" }],
    ["enroll", { ok: true, success: true }],                                                   // a client-style flag
    ["verify", null],
  ];
  for (const [op, body] of broken) {
    assert.notEqual(classifyReply(op, { status: 200, body }).outcome, "success", JSON.stringify(body));
  }
  assert.equal(classifyReply("verify", { status: 500, body: okVerify }).outcome, "server"); // success body, error status
});

test("server failures map to the right recovery screen", async () => {
  const { classifyReply } = await load("outcome.js");
  const c = (status, body, op = "verify") => classifyReply(op, { status, body }).outcome;
  assert.equal(c(200, { operation: "verify", accepted: false, decision: { match: false, liveness: LIVE, pad: PAD } }), "nomatch");
  assert.equal(c(200, { operation: "verify", accepted: false, decision: { error: "LIVENESS_FAIL" } }), "liveness");
  assert.equal(c(422, { error: "LOW_QUALITY" }), "quality");
  assert.equal(c(422, { error: "NO_FACE" }), "quality");
  assert.equal(c(401, { error: "AUTHENTICATION_REQUIRED" }), "expired");
  assert.equal(c(403, { error: "CONSENT_REQUIRED" }), "consent");
  assert.equal(c(503, { error: "BUSY" }), "busy");
  assert.equal(c(409, { error: "OPERATION_IN_PROGRESS" }), "pending");
  assert.equal(c(404, { error: "USER_NOT_FOUND" }), "notenrolled");
  assert.equal(c(502, "<html>"), "server");
  assert.equal(classifyReply("verify", null).outcome, "uncertain");
});

test("a failed recording never changes a passed face check", async () => {
  const { classifyReply } = await load("outcome.js");
  const r = classifyReply("verify", { status: 200, body: { ...okVerify, recording: { status: "failed", reason: "RECORDING_UNAVAILABLE" } } });
  assert.deepEqual([r.outcome, r.recording], ["success", "failed"]);
  assert.equal(classifyReply("verify", { status: 200, body: { ...okVerify, recording: { status: "skipped" } } }).recording, "off");
  assert.equal(classifyReply("verify", { status: 200, body: { ...okVerify, recording: { status: "stored" } } }).recording, "saved");
});

// ---- typed cues --------------------------------------------------------------------

test("cue map matches the SDK source word for word, and guesses nothing else", async () => {
  const { poseOf, CUE_FORWARD, cueTurn } = await load("cues.js");
  const sdk = fs.readFileSync(path.join(__dirname, "../../packages/face-sdk/src/workflow/session.ts"), "utf8");
  assert.ok(sdk.includes("'" + CUE_FORWARD + "'"), "forward sentence changed in the SDK");
  assert.ok(sdk.includes("`" + cueTurn("${direction}") + "`"), "turn sentence changed in the SDK");
  assert.ok(sdk.includes("'LEFT' : 'RIGHT'"), "direction words changed in the SDK");

  assert.equal(poseOf(CUE_FORWARD), "forward");
  assert.equal(poseOf(cueTurn("LEFT")), "left");
  assert.equal(poseOf(cueTurn("RIGHT")), "right");
  for (const t of ["turn left", "Please look to your LEFT", cueTurn("left"), "", "Move closer"]) {
    assert.equal(poseOf(t), null, t);
  }
});


test("account entry separates registration from sign-in and ignores double clicks", async () => {
  const flow = await load("flow.js");
  const signedOut = run(flow, [{ type: "SIGNED_OUT", registration: true }]).state;
  assert.deepEqual(run(flow, [P], signedOut).effects, ["goSignin"]);
  assert.deepEqual(run(flow, [{type: "REGISTER"}, {type: "REGISTER"}, P], signedOut).effects, ["goRegister"]);
  assert.deepEqual(run(flow, [{type: "REGISTER"}]).effects, []);
});

test("the camera screen says the guide is loading until its first line arrives", async () => {
  const flow = await load("flow.js");
  const open = [{ type: "LOADED", mode: "verify" }, P, { type: "CAMERA_OK" }];
  assert.equal(run(flow, open).state.guideLoading, true);
  assert.equal(run(flow, [...open, { type: "GUIDE", line: "find" }]).state.guideLoading, false); // same line as the default still counts as arrival
  assert.equal(run(flow, [...open, { type: "GUIDE_OFF" }]).state.guideLoading, false);
  assert.equal(run(flow, [{ type: "LOADED", mode: "verify" }, { type: "GUIDE_OFF" }, P, { type: "CAMERA_OK" }]).state.guideLoading, false);
});

test("the pre-expiry warning is its own outcome and never interrupts a capture", async () => {
  const flow = await load("flow.js");
  const ready = run(flow, [{ type: "LOADED", mode: "verify" }]).state;
  const warned = run(flow, [{ type: "SESSION_EXPIRED", early: true }], ready).state;
  assert.deepEqual([warned.view, warned.outcome], ["result", "expiring"]);
  assert.equal(run(flow, [{ type: "SESSION_EXPIRED" }], ready).state.outcome, "expired");
  const cap = run(flow, [...toCamera, P, READY]).state;
  assert.deepEqual(run(flow, [{ type: "SESSION_EXPIRED", early: true }], cap).state, cap);
  const { errorOutcome } = await load("outcome.js");
  assert.equal(errorOutcome("RECENT_LOGIN_REQUIRED", 403), "reauth");
  assert.equal(errorOutcome("AUTHENTICATION_REQUIRED", 401), "expired");
});

test("coming back from the sign-in page re-enables the buttons (RESUME clears 'leaving' only)", async () => {
  const flow = await load("flow.js");
  const signedOut = run(flow, [{ type: "SIGNED_OUT", registration: true }]).state;
  const r = run(flow, [{ type: "REGISTER" }, { type: "RESUME" }, { type: "REGISTER" }], signedOut);
  assert.deepEqual(r.effects, ["goRegister", "goRegister"]);
  assert.equal(r.state.busy, "leaving");
  const left = run(flow, [{ type: "REGISTER" }], signedOut).state;
  assert.equal(run(flow, [{ type: "RESUME" }], left).state.busy, null);
  // every view that leaves the page: the result screen too
  const expired = run(flow, [...toCamera, P, READY, { type: "RESULT", outcome: "expired" }, P]);
  assert.deepEqual(expired.effects.at(-1), "goSignin");
  assert.deepEqual(run(flow, [{ type: "RESUME" }, P], expired.state).effects, ["goSignin"]);
  // RESUME never touches a real operation in progress
  const scanning = run(flow, [...toCamera, P, READY]).state;
  assert.deepEqual(run(flow, [{ type: "RESUME" }], scanning).state, scanning);
  const consenting = run(flow, [{ type: "LOADED", mode: "enroll" }, P]).state;
  assert.equal(run(flow, [{ type: "RESUME" }], consenting).state.busy, "consent");
});

test("Start arms preparation and requires readiness from an available guide", async () => {
  const flow = await load("flow.js");
  const open = [{ type: "LOADED", mode: "verify" }, P, { type: "CAMERA_OK" }];
  const waiting = run(flow, open);
  assert.deepEqual([waiting.state.framing, waiting.effects.at(-1)], ["find", "startGuide"]);
  for (const line of ["find", "closer", "back", "centre", "one", "hold"]) {
    const r = run(flow, [...open, { type: "GUIDE", line }, P]);
    assert.deepEqual([r.state.view, r.state.framing], ["camera", line], line); // Start stayed asleep
  }
  assert.equal(run(flow, [...open, GOOD, P]).state.view, "camera");
  assert.equal(run(flow, [...open, GOOD, P, READY]).state.view, "capture");
  assert.equal(run(flow, [...open, GOOD, { type: "GUIDE", line: "closer" }, P]).state.view, "camera"); // moved away again
  // Without a guide Start works by itself (the fixed oval); a stale readiness event does nothing.
  assert.equal(run(flow, [...open, { type: "GUIDE_OFF" }, READY]).state.view, "camera");
  const fallback = run(flow, [...open, { type: "GUIDE_OFF" }, P]);
  assert.deepEqual([fallback.state.view, fallback.state.busy, fallback.effects.at(-1)], ["capture", "scan", "startScan"]);
  assert.equal(run(flow, [{ type: "LOADED", mode: "verify" }, { type: "GUIDE_OFF" }, P, { type: "CAMERA_OK" }, READY]).state.view, "camera");
  assert.equal(run(flow, [{ type: "LOADED", mode: "verify" }, { type: "GUIDE_OFF" }, P, { type: "CAMERA_OK" }, P]).state.view, "capture");
  // ...and the guide going away mid-preparation cancels the preparation, never fakes readiness
  const dying = run(flow, [...open, GOOD, P, { type: "GUIDE_OFF" }, READY]).state;
  assert.deepEqual([dying.view, dying.preparing, dying.guideOff], ["camera", false, true]);
  // the guide is only a guide: it cannot produce a result or touch a running scan
  const cap = run(flow, [...toCamera, P, READY]);
  assert.deepEqual(run(flow, [{ type: "GUIDE", line: "find" }], cap.state).state, cap.state);
});

test("the on-screen line is steady: no flicker, no losing Good to a wobble", async () => {
  const { createFraming, framingStep, SETTLE_MS } = await load("framing.js");
  const { LUMA_MIN, SHARP_MIN } = await import("../../packages/face-sdk/src/index.ts");
  const F = createFraming({ lumaMin: LUMA_MIN, sharpMin: SHARP_MIN });
  const at = (cue, now, armed = false) => framingStep(F, { cue, armed }, now);
  assert.equal(at("far", 0), "find"); // a new line is not shown at once...
  assert.equal(at("offcentre", 100), "find"); // ...and a flicker between cues restarts the wait
  assert.equal(at("far", 200), "find");
  assert.equal(at("far", 200 + SETTLE_MS), "closer"); // true for long enough: shown
  assert.equal(at("good", 1000), "closer"); // well placed, but not yet held
  assert.equal(at("good", 1900, true), "good"); // the guide held it 800 ms: shown at once
  assert.equal(at("hold", 2000), "good"); // a small wobble keeps Good...
  assert.equal(at("shaky", 5000), "good");
  assert.equal(at("far", 5100), "good"); // ...a real problem takes it away, after the same wait
  assert.equal(at("far", 5100 + SETTLE_MS), "closer");
  assert.equal(at("something-new", 9000), "closer");
  assert.equal(at("something-new", 9000 + SETTLE_MS), "find"); // an unknown cue never enables Start
});

test("a busy result waits out the server's Retry-After before Try again works", async () => {
  const flow = await load("flow.js");
  const busy = run(flow, [...toCamera, P, READY, { type: "RESULT", outcome: "busy", retryAfter: 20 }]);
  assert.deepEqual(busy.effects.slice(-2), ["startScan", "waitRetry"]);
  assert.equal(busy.state.retryAfter, 20);
  assert.deepEqual(run(flow, [P], busy.state).effects, []); // Try again is asleep
  const ready = run(flow, [{ type: "RETRY_READY" }, P], busy.state);
  assert.equal(ready.state.view, "ready");
  assert.deepEqual(ready.effects, ["forgetScan"]);
  const plain = run(flow, [...toCamera, P, READY, { type: "RESULT", outcome: "busy" }]); // no header: no wait
  assert.equal(plain.state.retryAfter, null);
  assert.deepEqual(plain.effects.slice(-1), ["startScan"]);
});

test("participant copy: Part 2 wording is pinned and the retired strings are gone", async () => {
  const { COPY } = await load("view.js");
  assert.equal(COPY.OUTCOMES.expired[1], "Your sign-in timed out. Sign in again to continue; your progress is safe.");
  assert.match(COPY.CAMERA_NOTICE.denied, /Settings > Safari > Camera/);
  assert.equal(COPY.STAGE_CUE.good, "Good.<br> Hold still."); // the check starts itself (auto-start)
  for (const [, lead] of Object.values(COPY.OUTCOMES)) assert.doesNotMatch(lead, /server/i, lead); // no developer words
  const src = (f) => fs.readFileSync(path.join(__dirname, "../verify", f), "utf8");
  for (const gone of ["Face verification", "Self-service", "Your identity", "WORKSPACE"]) assert.equal(src("index.html").includes(gone), false, gone);
  for (const gone of ["Get ready & start", "Skip the demo", "Confirmed by the server", "Not checked", "Decided by the server", "01 / Demo", "Continue as"]) assert.equal(src("view.js").includes(gone), false, gone);
});

test("scores shown on the result are the server's own numbers, and only numbers", async () => {
  const { classifyReply } = await load("outcome.js");
  const live = { live: true, enforced: true, score: 0.88 };
  const verify = (decision, accepted = true) => classifyReply("verify", { status: 200, body: { operation: "verify", accepted, decision } });
  assert.deepEqual(verify({ match: true, score: 0.99, threshold: 0.55, liveness: live, pad: { outcome: "live" } }).scores,
    { match: 0.99, threshold: 0.55, liveness: 0.88 });
  assert.deepEqual(verify({ match: false, score: 0.31, threshold: 0.55 }, false), { outcome: "nomatch", recording: null, requestId: null, scores: { match: 0.31, threshold: 0.55 } });
  assert.equal(verify({ match: false, score: "<b>1</b>", threshold: null }, false).scores, null); // never text
  const enrol = classifyReply("enroll", { status: 200, body: { operation: "enroll", accepted: true, template_id: "t", decision: { quality: { score: 0.73 }, liveness: live, pad: { outcome: "live" } } } });
  assert.deepEqual(enrol.scores, { quality: 0.73, liveness: 0.88 });
  const refused = (message) => classifyReply("verify", { status: 422, body: { error: "LIVENESS_FAIL", message } });
  assert.deepEqual(refused("PAD spoof: non_live_frame").scores, { reason: "spoof" });
  assert.deepEqual(refused("liveness check failed: challenge").scores, { reason: "movement" });
  assert.equal(classifyReply("verify", { status: 422, body: { error: "LOW_QUALITY", message: "PAD" } }).scores, null);
  // and the flow keeps them with the result
  const flow = await load("flow.js");
  const r = run(flow, [...toCamera, P, READY, { type: "RESULT", outcome: "nomatch", scores: { match: 0.31, threshold: 0.55 } }]);
  assert.deepEqual(r.state.scores, { match: 0.31, threshold: 0.55 });
  assert.equal(run(flow, [P], r.state).state.view, "ready");
});

test("Phase 0 navigation, after Phase 5: enrol -> Your face is saved -> Go to workspace, and Back", async () => {
  const flow = await load("flow.js");
  const { COPY } = await load("view.js");
  // the same capture as verification, then the server-confirmed pass
  const enrol = [{ type: "LOADED", mode: "enroll" }, P, { type: "CONSENT_OK" }, { type: "SKIP" }, P, { type: "CAMERA_OK" }, GOOD, READY,
    { type: "SDK_INSTRUCTION", pose: "left", settleMs: 1500 }, { type: "SDK_UPLOADING" }, { type: "RESULT", outcome: "success" }];
  const r = run(flow, enrol);
  assert.deepEqual(r.effects, ["grantConsent", "openCamera", "startGuide", "prepare", "startScan"]);
  assert.deepEqual([r.state.view, r.state.mode, r.state.outcome], ["result", "enroll", "success"]);
  assert.equal(COPY.SUCCESS.enroll[2], "Go to workspace");
  // one tap goes to the workspace (already signed in: no sign-in effect), a second tap does nothing
  const go = run(flow, [P, P], r.state);
  assert.deepEqual(go.effects, ["goWorkspace"]);
  // Back from the workspace: RESUME re-enables the button, which still goes to the workspace
  assert.deepEqual(run(flow, [{ type: "RESUME" }, P], go.state).effects, ["goWorkspace"]);
});
