"use strict";
// Runs the REAL capture SDK (frozen source) through apps/verify/bridge.js with a fake
// camera, clock and gateway: camera ownership, account binding on the wire,
// duplicate-submission safety and cancellation cleanup.
const test = require("node:test");
const assert = require("node:assert/strict");

const SID = "0123456789abcdef0123456789abcdef"; // the signed-in account's own subject
const CSRF = "csrf-token-from-session";
const LIVE = { live: true, score: 0.9, enforced: true, failed_signals: [] };
const CHALLENGE = {
  nonce: "9f2c41ab7e05d8630b1a4c77e2f9aa10", action: "HEAD_SEQUENCE",
  params: { settle_ms: 3200, switch_ms: 9000, first_sign: 1, target: 0.2 },
  issued_ms: 1_757_745_600_000, expires_ms: 1_757_745_630_000,
};
const VERIFIED = {
  operation: "verify", operation_id: "op-1", accepted: true,
  decision: { match: true, score: 0.71, threshold: 0.55, liveness: LIVE, pad: { outcome: "live", policy: "p" } },
  recording: { status: "skipped", reason: "NO_EVALUATION_CONSENT" },
};

async function rig() {
  const { createFaceSession } = await import("../../packages/face-sdk/src/index.ts");
  const { testEnv } = await import("../../packages/face-sdk/tests/helpers.ts");
  const { createBridge, createCameraOwner } = await import("../verify/bridge.js");
  const { classifyReply } = await import("../verify/outcome.js");
  const { poseOf } = await import("../verify/cues.js");

  const t = testEnv();
  let opened = 0;
  // The SDK's fake stream (frozen helpers) has no clone(); give it one with its own tracks,
  // so "the SDK stopped its clone" and "the page's stream is still live" are distinguishable.
  const clones = [];
  const cloneOf = (s) => {
    const tracks = s.getTracks().map((x) => ({ kind: x.kind, readyState: 'live', stopped: false,
      stop() { this.stopped = true; this.readyState = 'ended'; }, addEventListener() {}, removeEventListener() {} }));
    const c = { getTracks: () => tracks, get allStopped() { return tracks.every((x) => x.stopped); } };
    clones.push(c);
    return c;
  };
  const mediaDevices = { getUserMedia: async (c) => { opened++; const s = await t.env.getUserMedia(c); s.clone = () => cloneOf(s); return s; } };
  const owner = createCameraOwner({ mediaDevices, video: t.video });
  let keys = 0;
  const bridge = createBridge({
    fetch: t.fetch, subjectId: SID, csrfToken: CSRF,
    newKey: () => "idem-key-" + String(++keys).padStart(12, "0"),
  });
  const events = [];
  const session = createFaceSession({
    videoElement: t.video, onEvent: (e) => events.push(e),
    env: { ...t.env, fetch: bridge.fetch, getUserMedia: () => owner.handoff() },
  });
  t.fetch.reply(`/v2/subjects/${SID}/challenge`, { body: CHALLENGE });
  return { t, owner, bridge, session, events, classifyReply, poseOf, opened: () => opened, clones };
}

const header = (call, name) => new Headers(call.init.headers).get(name);

test("one camera stream: Enable camera opens it, the SDK gets a clone and stops only that", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: VERIFIED });
  await r.owner.open();
  assert.equal(r.owner.live, true);
  await r.session.verify(SID);
  assert.equal(r.opened(), 1, "getUserMedia must be called once, not once per owner");
  assert.equal(r.t.streams.length, 1);
  assert.equal(r.clones.length, 1);
  assert.equal(r.clones[0].allStopped, true, "the SDK stops the clone it was given");
  assert.equal(r.t.streams[0].allStopped, false, "the page's own stream is still live for the preview");
  assert.equal(r.owner.live, true);
  // the page releases it when the attempt is over (main.js) or on Close
  r.owner.stop();
  assert.equal(r.t.streams[0].allStopped, true);
  assert.equal(r.t.video.srcObject, null);
  assert.equal(r.owner.live, false);
});

test("a restart in place: the clone is stopped, reattach() restores the preview, a second handoff needs no getUserMedia", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: VERIFIED });
  await r.owner.open();
  const running = r.session.verify(SID);
  await new Promise((resolve) => { const tick = () => (r.events.some((e) => e.type === "frame") ? resolve() : setImmediate(tick)); tick(); });
  r.session.cancel("preparation-moved");
  assert.equal((await running).code, "CANCELLED");
  assert.equal(r.clones[0].allStopped, true);
  assert.equal(r.t.video.srcObject, null, "the SDK cleared the preview it had set to its clone");
  r.owner.reattach();
  assert.equal(r.t.video.srcObject, r.t.streams[0]);
  assert.equal(r.owner.live, true);
  const again = await r.owner.handoff();
  assert.equal(r.opened(), 1);
  assert.equal(again, r.clones[1]);
  assert.equal(r.bridge.sent, false);
});

test("requests are bound to the session's own subject and carry CSRF + one idempotency key", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: VERIFIED });
  await r.owner.open();
  const result = await r.session.verify(SID);
  assert.equal(result.ok, true);

  const [challenge, scan] = r.t.fetch.calls;
  assert.equal(r.t.fetch.calls.length, 2);
  assert.equal(challenge.url, `/v2/subjects/${SID}/challenge?operation=verify`);
  assert.equal(scan.url, `/v2/subjects/${SID}/verify`);
  for (const call of r.t.fetch.calls) {
    assert.equal(header(call, "X-CSRF-Token"), CSRF);
    assert.equal(header(call, "X-User-Id"), null, "the browser must not assert an identity");
  }
  assert.match(header(scan, "Idempotency-Key"), /^[A-Za-z0-9_-]{16,128}$/);
  assert.equal(header(scan, "X-Facetech-Precheck"), null, "no reading, no header");
  assert.deepEqual(Object.keys(JSON.parse(scan.init.body)).sort(), ["facescan", "subject_id"]);
  assert.equal(JSON.parse(scan.init.body).subject_id, SID);

  // what the page shows comes from the raw server reply
  assert.deepEqual(r.classifyReply("verify", r.bridge.reply), { outcome: "success", recording: "off", requestId: null, scores: { match: 0.71, threshold: 0.55, liveness: 0.9 } });
});

test("the pre-check reading rides with the scan (and its recheck) as one header, for the trace only", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: VERIFIED });
  await r.owner.open();
  const reading = { luma: 81.5, frame_luma: 140.2, backlight: 58.7, sharp: 33.1, hint: null, capped: false };
  r.bridge.precheck = reading;
  await r.session.verify(SID);
  await r.bridge.recheck();
  const [challenge, scan, again] = r.t.fetch.calls;
  assert.equal(header(challenge, "X-Facetech-Precheck"), null);
  assert.deepEqual(JSON.parse(header(scan, "X-Facetech-Precheck")), reading);
  assert.equal(header(again, "X-Facetech-Precheck"), header(scan, "X-Facetech-Precheck"));
  assert.ok(header(scan, "X-Facetech-Precheck").length <= 256, "the gateway drops longer values");
  assert.deepEqual(Object.keys(JSON.parse(scan.init.body)).sort(), ["facescan", "subject_id"], "the scan body is unchanged");
});

test("head poses come from SDK events: forward, then the server's first direction, then the other", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: VERIFIED });
  await r.owner.open();
  await r.session.verify(SID);
  const poses = r.events.filter((e) => e.type === "action").map((e) => r.poseOf(e.text));
  assert.deepEqual(poses, ["forward", "left", "right"]);
  assert.ok(r.events.some((e) => e.type === "frame" && e.index === e.total - 1 || e.index === e.total));
});

test("a lost reply is rechecked with the SAME key and bytes; no second scan is created", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { networkError: true });
  await r.owner.open();
  const result = await r.session.verify(SID);
  assert.equal(result.ok, false);
  assert.equal(r.bridge.sent, true);
  assert.equal(r.bridge.reply, null);
  assert.equal(r.classifyReply("verify", r.bridge.reply).outcome, "uncertain");

  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: { ...VERIFIED, replayed: true } });
  const reply = await r.bridge.recheck();
  assert.equal(r.classifyReply("verify", reply).outcome, "success");

  const posts = r.t.fetch.calls.filter((c) => c.init.method === "POST");
  assert.equal(posts.length, 2);
  assert.equal(header(posts[0], "Idempotency-Key"), header(posts[1], "Idempotency-Key"));
  assert.equal(posts[0].init.body, posts[1].init.body);

  r.bridge.forget();
  assert.equal(await r.bridge.recheck(), null, "after forget() the scan bytes are gone");
});

test("a recheck that is still running reports pending, not success or failure", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { networkError: true });
  await r.owner.open();
  await r.session.verify(SID);
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { status: 409, body: { error: "OPERATION_IN_PROGRESS" } });
  assert.equal(r.classifyReply("verify", await r.bridge.recheck()).outcome, "pending");
});

test("Stop before submission: nothing is posted and the camera is released", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { body: VERIFIED });
  await r.owner.open();
  const running = r.session.verify(SID);
  await new Promise((resolve) => {
    const tick = () => (r.events.some((e) => e.type === "frame") ? resolve() : setImmediate(tick));
    tick();
  });
  r.session.cancel("user-stop");
  const result = await running;
  assert.equal(result.code, "CANCELLED");
  assert.equal(r.bridge.sent, false);
  assert.equal(r.t.fetch.calls.filter((c) => c.init.method === "POST").length, 0);
  assert.equal(r.clones[0].allStopped, true);
  r.session.dispose();
  assert.equal(r.t.doc.listenerCount, 0);
});

test("a server refusal reaches the page as the server's own code", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { status: 422, body: { error: "LIVENESS_FAIL", request_id: "abcd1234" } });
  await r.owner.open();
  const result = await r.session.verify(SID);
  assert.equal(result.engineCode, "LIVENESS_FAIL");
  assert.equal(r.classifyReply("verify", r.bridge.reply).outcome, "liveness");
});

test("a 429's Retry-After is kept with the raw reply, in whole seconds", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/verify`, { status: 429, body: { error: "BUSY" }, headers: { "Retry-After": "20" } });
  await r.owner.open();
  await r.session.verify(SID);
  assert.equal(r.bridge.reply.retryAfter, 20);
  assert.deepEqual(r.classifyReply("verify", r.bridge.reply), { outcome: "busy", recording: null, requestId: null, scores: null, retryAfter: 20 });
});

test("an expired session on the challenge stops before any capture", async () => {
  const r = await rig();
  r.t.fetch.reply(`/v2/subjects/${SID}/challenge`, { status: 401, body: { error: "AUTHENTICATION_REQUIRED" } });
  await r.owner.open();
  const result = await r.session.verify(SID);
  assert.equal(result.engineCode, "AUTHENTICATION_REQUIRED");
  assert.equal(r.bridge.sent, false);
  assert.equal(r.clones[0].allStopped, true);
});

test("the challenge's glow schedule is read beside the SDK; the SDK still gets the reply untouched", async () => {
  const { createBridge } = await import("../verify/bridge.js");
  const GLOW = { version: "glow-v1", neutral: [255, 255, 255], fade_ms: 400, end_ms: 1600,
    steps: [{ colour: "blue", rgb: [111, 168, 255], start_ms: 400 }, { colour: "green", rgb: [111, 224, 160], start_ms: 1000 }] };
  let body = null, status = 200, reads = 0;
  const reply = () => {
    const res = { status, headers: new Headers(), json: async () => { reads++; return body; } };
    res.clone = () => ({ ...res });
    return res;
  };
  const b = createBridge({ fetch: async () => reply(), subjectId: SID, csrfToken: CSRF, newKey: () => "k".repeat(16) });
  const ask = () => b.fetch("https://x/v1/challenge", { headers: { "X-Facetech-Operation": "verify" } });

  body = { ...CHALLENGE, action: "LOOK_LEFT", glow: GLOW };
  const res = await ask();
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), body, "the SDK reads the same reply");
  assert.deepEqual(b.glow.steps.map((s) => s.startMs), [400, 1000]);
  assert.equal(b.glow.endMs, 1600);

  body = CHALLENGE; // no glow key (every policy but single-turn-glow-v1)
  await ask();
  assert.equal(b.glow, null);
  body = { ...CHALLENGE, glow: { ...GLOW, steps: "bad" } };
  await ask();
  assert.equal(b.glow, null, "a malformed schedule is ignored");
  body = { ...CHALLENGE, glow: GLOW }; status = 401;
  await ask();
  assert.equal(b.glow, null, "an error reply never carries a glow");
  reads = 0; status = 200;
  const plain = { status: 200, headers: new Headers(), json: async () => { reads++; return CHALLENGE; } };
  const noClone = createBridge({ fetch: async () => plain, subjectId: SID, csrfToken: CSRF, newKey: () => "k".repeat(16) });
  assert.equal(await noClone.fetch("https://x/v1/challenge", { headers: {} }), plain, "same object handed to the SDK");
  assert.equal(noClone.glow, null); assert.equal(reads, 0, "nothing read when the reply cannot be copied");
});
