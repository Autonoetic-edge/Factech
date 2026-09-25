"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadApp, jsonResponse } = require("./harness");

const ROSTER = (ids) => ({
  "facetech.console.subjects.v1": JSON.stringify(ids.map((id) => ({
    id, name: id, enrolled: "2026-09-17", quality: 0.8, template: "t", revoked: false }))),
});
const LIVE = { live: true, score: 0.8, enforced: true, failed_signals: [] };
const OK_VERIFY = { match: true, score: 0.91, threshold: 0.55, liveness: LIVE };
const OK_ENROLL = { template_id: "t-9", quality: { score: 0.8, frames_embedded: 3 }, liveness: LIVE };
const never = () => new Promise(() => {});

async function consoleApp(routes, storage) {
  const app = loadApp("console", {
    storage: storage || ROSTER(["alice", "bob"]),
    routes: Object.assign({
      "POST /v1/verify": () => jsonResponse(200, OK_VERIFY),
      "POST /v1/enroll": () => jsonResponse(200, OK_ENROLL),
    }, routes || {}),
  });
  await app.clock.advance(10);
  return app;
}
const go = (app, screen) => app.click("", { go: screen });

function frameBytesIn(root) {
  const seen = new Set();
  const found = [];
  const walk = (v, where) => {
    if (typeof v === "string") {
      if (v.startsWith("data:image") || (v.length > 256 && /^[A-Za-z0-9+/=]+$/.test(v))) found.push(where);
      return;
    }
    if (v === null || typeof v !== "object" || seen.has(v)) return;
    seen.add(v);
    if (ArrayBuffer.isView(v) || Object.prototype.toString.call(v) === "[object ArrayBuffer]") {
      found.push(where); return;
    }
    if (typeof v.toDataURL === "function" || typeof v.getContext === "function") { found.push(where + " (canvas)"); return; }
    for (const k of Object.keys(v)) walk(v[k], where + "." + k);
  };
  walk(root, "S");
  return found;
}

test("f11 a challenge that never answers times out, releases the camera and sends nothing", async () => {
  const app = await consoleApp({ "GET /v1/challenge": never });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(9000);
  assert.ok(app.S.result && app.S.result.ok === false, "the run never ended");
  assert.match(app.S.result.msg, /TIMEOUT/);
  const ch = app.calls.filter((c) => c.path === "/v1/challenge").pop();
  assert.equal(ch.signal && ch.signal.aborted, true, "the stalled request was not aborted");
  assert.equal(app.posts().length, 0);
  assert.ok(app.camera.allEnded(), "camera left on");
  assert.notEqual(app.S.run.state, "running");
});

test("f11 an upload that never answers times out at the scan deadline and releases the camera", async () => {
  const app = await consoleApp({ "POST /v1/verify": never });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(9000);
  assert.equal(app.posts().length, 1);
  assert.equal(app.S.result, null, "gave up before the scan deadline");
  await app.clock.advance(21000);
  assert.ok(app.S.result && app.S.result.ok === false, "the run never ended");
  assert.match(app.S.result.msg, /TIMEOUT/);
  assert.equal(app.posts()[0].signal.aborted, true);
  assert.ok(app.camera.allEnded());
  assert.notEqual(app.S.run.state, "running");
});

test("f11 navigating away mid-upload aborts the request, releases the camera and says it cannot undo", async () => {
  const app = await consoleApp({ "POST /v1/enroll": never });
  await go(app, "enrol");
  app.dom.el("#eId").value = "carol";
  await app.click("doCapture");
  await app.clock.advance(9000);
  assert.equal(app.posts().length, 1);
  await go(app, "overview");
  await app.clock.advance(100);
  assert.equal(app.posts()[0].signal.aborted, true, "the upload was left running");
  assert.ok(app.camera.allEnded());
  assert.equal(app.S.result, null);
  assert.ok(app.dom.toasts.some((t) => /already sent/.test(t) && /does not undo/.test(t)),
    "no warning that the engine may already have acted: " + JSON.stringify(app.dom.toasts));
  assert.equal(app.S.subjects.some((s) => s.id === "carol"), false);
});

test("f11 cancelling the liveness run aborts its upload", async () => {
  const app = await consoleApp({ "POST /v1/liveness": never });
  await go(app, "liveness");
  await app.click("doLiveness");
  await app.clock.advance(9000);
  const post = app.posts().find((c) => c.path === "/v1/liveness");
  assert.ok(post);
  app.setHidden(true);
  await app.clock.advance(100);
  assert.equal(post.signal.aborted, true);
  assert.ok(app.camera.allEnded());
});

test("f11 an engine rejection shows its request id in the result, the toast and the log", async () => {
  const app = await consoleApp({ "POST /v1/verify": () => jsonResponse(422,
    { error: { code: "LIVENESS_FAIL", message: "not live" } }, { "x-request-id": "req-abcdef12" }) });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(9000);
  assert.equal(app.S.result.requestId, "req-abcdef12");
  assert.match(app.main(), /req-abcdef12/);
  assert.ok(app.dom.toasts.some((t) => t.includes("req-abcdef12")));
  assert.match(app.S.log[0].d, /req-abcdef12/);
});

test("f11 a refused challenge shows its code and request id", async () => {
  const app = await consoleApp({ "GET /v1/challenge": () => jsonResponse(503,
    { error: { code: "BUSY", message: "busy" } }, { "x-request-id": "req-99887766" }) });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(9000);
  assert.match(app.S.result.msg, /HTTP_503|BUSY/);
  assert.match(app.S.result.msg, /req-99887766/);
  assert.equal(app.posts().length, 0);
});

test("f11 requests keep the transport rules: same origin, no redirects, no cache", async () => {
  let seen = null;
  const app = await consoleApp({ "POST /v1/verify": (call) => { seen = call; return jsonResponse(200, OK_VERIFY); } });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(9000);
  assert.ok(seen);
  assert.equal(seen.headers["Content-Type"], "application/json");
  assert.equal(seen.redirect, "error");
  assert.equal(seen.cache, "no-store");
  assert.equal(seen.credentials, "same-origin");
  assert.equal(app.S.result.ok, true);
});

test("f12 after an enroll the console keeps metadata and frame hashes, never frame bytes", async () => {
  const app = await consoleApp();
  await go(app, "enrol");
  app.dom.el("#eId").value = "carol";
  await app.click("doCapture");
  await app.clock.advance(9000);
  await app.settle();
  assert.equal(app.S.result.ok, true);
  assert.deepEqual(frameBytesIn(app.S), []);
  assert.equal(app.S.lastScan.frames.length, app.S.lastScan.frameCount);
  assert.match(app.S.lastScan.frames[0].h, /^[0-9a-f]{64}$/);
  assert.equal(app.S.lastScan.version, 1);
});

test("f12 after a verify, a failure, a cancel and a revoke no frame bytes are reachable from S", async () => {
  let verify = () => jsonResponse(200, OK_VERIFY);
  const app = await consoleApp({
    "POST /v1/verify": () => verify(),
    "DELETE /v1/templates/alice": () => jsonResponse(200, { deleted: 1 }),
  });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(9000);
  await app.settle();
  assert.equal(app.S.result.ok, true);
  assert.deepEqual(frameBytesIn(app.S), [], "after verify");

  verify = () => jsonResponse(422, { error: { code: "LIVENESS_FAIL", message: "no" } });
  await app.click("doCapture");
  await app.clock.advance(9000);
  await app.settle();
  assert.equal(app.S.result.ok, false);
  assert.deepEqual(frameBytesIn(app.S), [], "after failure");

  verify = never;
  await app.click("doCapture");
  await app.clock.advance(9000);
  await go(app, "subjects");
  await app.settle();
  assert.deepEqual(frameBytesIn(app.S), [], "after cancel");

  await app.click("mGo", { id: "alice" });
  await app.settle();
  assert.equal(app.S.subjects.find((s) => s.id === "alice").revoked, true);
  assert.deepEqual(frameBytesIn(app.S), [], "after revoke");
});

test("f12 the byte scan does see bytes when they are there", () => {
  assert.deepEqual(frameBytesIn({ a: { b: new Uint8Array(3) } }), ["S.a.b"]);
  assert.deepEqual(frameBytesIn({ c: "data:image/jpeg;base64,AAAA" }), ["S.c"]);
});
