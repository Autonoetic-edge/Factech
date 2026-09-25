"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadApp, jsonResponse, deferred } = require("./harness");

const ROSTER = (ids) => ({
  "facetech.console.subjects.v1": JSON.stringify(ids.map((id) => ({
    id, name: id, enrolled: "2026-09-17", quality: 0.8, template: "t", revoked: false }))),
});
const OK_VERIFY = { match: true, score: 0.91, threshold: 0.55,
  liveness: { live: true, score: 0.8, enforced: true, failed_signals: [] } };

async function consoleApp(opts) {
  opts = opts || {};
  const app = loadApp("console", Object.assign({ storage: ROSTER(["alice", "bob"]) }, opts, {
    routes: Object.assign({
      "POST /v1/verify": () => jsonResponse(200, OK_VERIFY),
      "POST /v1/enroll": () => jsonResponse(200, { template_id: "t-9", quality: { score: 0.8, frames_embedded: 3 },
        liveness: { live: true, score: 0.8, enforced: true, failed_signals: [] } }),
    }, opts.routes || {}),
  }));
  await app.clock.advance(10);
  return app;
}
const go = (app, screen) => app.click("", { go: screen });
async function runCapture(app) {
  await app.click("doCapture");
  await app.clock.advance(9000);
}

test("f05 navigating mid-capture cannot turn a verify into an enroll", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  app.run("S.pendingId='carol'");
  await app.click("doCapture");
  await app.clock.advance(2000);
  await go(app, "enrol");
  await app.clock.advance(9000);
  assert.equal(app.posts().filter((c) => c.path === "/v1/enroll").length, 0,
    "a verify capture was sent as an enroll");
  assert.equal(app.posts().length, 0, "the cancelled capture was still sent");
  assert.ok(app.camera.allEnded(), "camera left on after navigation");
});

test("f05 a reply for a capture the user navigated away from is dropped", async () => {
  const d = deferred();
  const app = await consoleApp({ routes: { "POST /v1/verify": () => d.promise } });
  await go(app, "verify");
  await runCapture(app);
  assert.equal(app.posts().length, 1);
  await go(app, "enrol");
  d.resolve(jsonResponse(200, OK_VERIFY));
  await app.clock.advance(500);
  assert.equal(app.S.result, null, "stale verify result was applied");
  assert.equal(app.S.log.filter((e) => e.op === "Verify").length, 0);
});

test("f05 the subject is read once, at the click", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(1000);
  app.run("S.target='bob'");
  await app.clock.advance(9000);
  assert.equal(app.posts()[0].body.user_id, "alice");
});

test("f-stale-cleanup a cancelled capture that finishes late leaves the new capture alone", async () => {
  const d = deferred();
  let first = true;
  const app = await consoleApp({ routes: { "GET /v1/challenge": () => {
    if (first) { first = false; return d.promise; }
    return jsonResponse(200, { nonce: "b".repeat(32), action: "MOVE_CLOSER",
      params: { settle_ms: 1500, target: 0.3 }, issued_ms: 1, expires_ms: 30001 });
  } } });
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(100);
  await go(app, "enrol");
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(1000);
  assert.equal(app.S.capture.state, "running");
  const bTrack = app.camera.tracks[app.camera.tracks.length - 1];
  assert.equal(bTrack.readyState, "live");
  d.resolve(jsonResponse(200, { nonce: "a".repeat(32), action: "MOVE_CLOSER",
    params: { settle_ms: 1500, target: 0.3 }, issued_ms: 1, expires_ms: 30001 }));
  await app.clock.advance(10);
  assert.equal(bTrack.readyState, "live", "A's cleanup stopped B's camera");
  assert.equal(app.S.run.state, "running", "A's cleanup reset B's run state");
  assert.equal(app.S.capture.state, "running", "A's cleanup reset B's capture state");
  assert.ok(app.S.capture.guide, "A's cleanup cleared B's guide");
  await app.clock.advance(9000);
  assert.equal(app.posts().filter((c) => c.path === "/v1/verify").length, 1);
  assert.equal(app.S.result.ok, true);
  assert.ok(app.camera.allEnded());
});

test("f-stale-cleanup a camera that arrives after cancel is still stopped", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  const release = app.camera.hold();
  await app.click("doCapture");
  await app.clock.advance(10);
  await go(app, "enrol");
  release();
  await app.clock.advance(1000);
  assert.equal(app.camera.tracks.length, 1);
  assert.ok(app.camera.allEnded(), "late stream left running");
});

test("f-camera-leak two pending prompts, the older one resolving first, the new capture reuses its stream", async () => {
  const app = await consoleApp();
  const make = app.camera.getUserMedia.bind(app.camera);
  const gates = [];
  app.camera.getUserMedia = () => {
    const g = deferred();
    gates.push(g);
    return g.promise.then(make);
  };
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(10);
  await go(app, "enrol");
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(10);
  assert.equal(gates.length, 2, "both prompts pending");
  gates[0].resolve();
  await app.clock.advance(10);
  gates[1].resolve();
  await app.clock.advance(10);
  assert.equal(app.camera.tracks.length, 2);
  const [aTrack, bTrack] = app.camera.tracks;
  assert.equal(aTrack.readyState, "live", "the stream B reuses was stopped");
  assert.equal(bTrack.readyState, "ended", "the later stream was left running");
  await app.clock.advance(9000);
  assert.equal(app.posts().filter((c) => c.path === "/v1/verify").length, 1);
  assert.equal(app.S.result.ok, true);
  assert.ok(app.camera.allEnded(), "a track was left running after both runs ended");
});

test("f12 the camera is stopped after a completed capture", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  await runCapture(app);
  assert.equal(app.S.result.ok, true);
  assert.ok(app.camera.tracks.length >= 1);
  assert.ok(app.camera.allEnded(), "console never stopped the camera");
});

test("f13 a capture that throws does not strand the liveness console", async () => {
  const app = await consoleApp();
  await go(app, "liveness");
  let n = 0;
  app.env.ctx.drawImage = () => { if (++n === 3) throw new Error("canvas gone"); };
  await app.click("doLiveness");
  await app.clock.advance(9000);
  assert.equal(app.S.run.state, "idle", "run state stuck");
  assert.notEqual(app.S.capture.state, "running", "capture state stuck");
  assert.ok(app.camera.allEnded(), "camera left on after the failure");
  app.env.ctx.drawImage = () => {};
  app.env.ctx.drawImage = () => {};
  await app.click("doLiveness");
  await app.clock.advance(9000);
  assert.equal(app.posts().filter((c) => c.path === "/v1/liveness").length, 1);
});

test("f11 the liveness screen has a preview mount and hold/move guidance", async () => {
  const app = await consoleApp({ routes: { "POST /v1/liveness": () => jsonResponse(200, { live: true,
    score: 0.8, threshold: 0.5, enforced: true, signals: {} }) } });
  await go(app, "liveness");
  assert.match(app.main(), /class="view"/, "no .view for mountVideo");
  await app.click("doLiveness");
  await app.clock.advance(600);
  assert.match(app.main(), /Hold steady/);
  await app.clock.advance(2500);
  assert.doesNotMatch(app.main(), /class="oval live">Hold steady/);
});

test("f09 the capture prompt holds for the issued settle window before asking to move", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(700);
  assert.match(app.main(), /class="oval live">Hold steady/);
  assert.doesNotMatch(app.main(), /class="oval live">Slowly move closer/);
  await app.clock.advance(2000);
  assert.match(app.main(), /class="oval live">Slowly move closer/);
});

test("f16 advisory signals are not shown as failed", async () => {
  const app = await consoleApp({ routes: { "POST /v1/liveness": () => jsonResponse(200, {
    live: true, score: 0.8, threshold: 0.5, enforced: true,
    signals: {
      motion: { score: 0.9, ok: true },
      depth: { score: null, ok: null, advisory: true, depth_index: 0.1 },
    } }) } });
  await go(app, "liveness");
  await app.click("doLiveness");
  await app.clock.advance(9000);
  const html = app.main();
  const depthRow = html.slice(html.indexOf(">depth<"), html.indexOf("</tr>", html.indexOf(">depth<")));
  assert.doesNotMatch(depthRow, /Failed/);
  assert.match(depthRow, /Advisory/);
  assert.equal(app.S.log[0].ok, true);
});

test("f17 an unknown code is not shown as a match-stage failure after passed stages", async () => {
  const app = await consoleApp({ routes: { "POST /v1/verify": () =>
    jsonResponse(503, { error: { code: "SOMETHING_NEW", message: "x" } }) } });
  await go(app, "verify");
  await runCapture(app);
  const R = app.S.run.results;
  assert.ok(!["ok", "passed"].includes(R.nonce), "nonce claimed passed: " + R.nonce);
  assert.ok(!["ok", "passed"].includes(R.liveness), "liveness claimed passed: " + R.liveness);
  assert.ok(!["fail", "failed"].includes(R.match), "blamed on match: " + R.match);
  assert.equal(app.run("STAGE_OF['SOMETHING_NEW']"), undefined);
  assert.equal(app.run("stageOf('SOMETHING_NEW')"), "unknown");
});

test("f17 contract codes map to the stage that decided them", async () => {
  const app = await consoleApp();
  const stage = (c) => app.run("stageOf(" + JSON.stringify(c) + ")");
  assert.equal(stage("ENGINE_UNREACHABLE"), "transport");
  assert.equal(stage("PAYLOAD_TOO_LARGE"), "capture");
  assert.equal(stage("MODEL_UNAVAILABLE"), "unknown");
  assert.equal(stage("USER_NOT_FOUND"), "match");
  const out = app.run("pipelineOutcome({ok:false,status:404,data:{error:{code:'USER_NOT_FOUND'}}})");
  assert.equal(out.nonce, "not-reached");
  assert.equal(out.liveness, "not-reached");
  assert.equal(out.match, "failed");
  const unenforced = app.run("pipelineOutcome({ok:true,status:200,data:{match:true," +
    "liveness:{live:false,enforced:false}}})");
  assert.equal(unenforced.liveness, "advisory");
  assert.equal(unenforced.nonce, "advisory");
});

test("f37 the different-subject test needs a second subject", async () => {
  const app = await consoleApp({ storage: ROSTER(["alice"]) });
  await go(app, "verify");
  app.run("S.verifyAsOther=true");
  await runCapture(app);
  assert.equal(app.posts().length, 0, "matched the target against itself as an impostor test");
  assert.equal(app.S.result.ok, false);
  assert.match(app.S.result.msg, /two enrolled subjects/);
  assert.equal(app.run("otherTarget()"), null);
});

test("f53 server metadata is escaped", async () => {
  const app = await consoleApp({ routes: { "GET /v1/info": () => jsonResponse(200, {
    engineVersion: "0.1.0", modelVersion: "m",
    thresholds: { match: "<b>x</b>", liveness: null },
    faceScanVersions: ["<img src=x onerror=alert(1)>"] }) } });
  await go(app, "overview");
  assert.doesNotMatch(app.main(), /<img src=x/);
  assert.doesNotMatch(app.main(), /<b>x<\/b>/);
  assert.match(app.main(), /&lt;img/);
});

test("f15 the result card does not say liveness passed when it was not enforced", async () => {
  const app = await consoleApp({ routes: { "POST /v1/verify": () => jsonResponse(200, Object.assign({},
    OK_VERIFY, { liveness: { live: false, score: 0.2, enforced: false, failed_signals: ["motion"] } })) } });
  await go(app, "verify");
  await runCapture(app);
  const html = app.main();
  const row = html.slice(html.indexOf(">Liveness<"), html.indexOf("</tr>", html.indexOf(">Liveness<")));
  assert.doesNotMatch(row, /Passed/);
  assert.match(row, /NOT ENFORCED/);
});

test("f10 an enforced liveness pass with a match is the only verify shown as verified", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  await runCapture(app);
  assert.equal(app.S.result.verified, true);
  const html = app.main();
  assert.match(html, /pill solid">Verified</);
  assert.doesNotMatch(html, /Not verified|Face match only/);
  assert.match(app.dom.toasts.at(-1), /^Verified: /);
});

test("f10 a match with liveness failed, unknown, missing or not enforced is not shown as verified", async () => {
  const cases = {
    "not enforced": { live: true, score: 0.8, enforced: false, failed_signals: [] },
    "failed": { live: false, score: 0.2, enforced: true, failed_signals: ["motion"] },
    "unknown": { score: 0.8, enforced: true },
  };
  for (const [name, liveness] of Object.entries(cases)) {
    const app = await consoleApp({ routes: { "POST /v1/verify": () =>
      jsonResponse(200, Object.assign({}, OK_VERIFY, { liveness })) } });
    await go(app, "verify");
    await runCapture(app);
    assert.equal(app.S.result.verified, false, name);
    const html = app.main();
    assert.match(html, /Not verified/, name);
    assert.match(html, /Face match only/, name);
    assert.doesNotMatch(html, /pill solid">Verified</, name);
    assert.doesNotMatch(html, /, verified\./, name);
    assert.match(app.dom.toasts.at(-1), /Not verified\.$/, name);
  }
  const missing = await consoleApp({ routes: { "POST /v1/verify": () =>
    jsonResponse(200, { match: true, score: 0.91, threshold: 0.55 }) } });
  await go(missing, "verify");
  await runCapture(missing);
  assert.equal(missing.S.result.verified, false, "missing liveness");
  assert.match(missing.main(), /Not verified/);
});

test("f29 the repeated-frame toggle and scope note do not claim photo or video rejection", async () => {
  const app = await consoleApp();
  await go(app, "liveness");
  const html = app.main();
  assert.doesNotMatch(html, /photo held to the lens/);
  assert.doesNotMatch(html, /to see it reject/);
  assert.match(html, /duplicate-frame check/);
  assert.match(html, /printed\s+photo, a screen or a playing video/);
  assert.match(html, /does not reliably reject/);
  const toggles = app.dom.el("#demoBody").innerHTML;
  assert.match(toggles, /Send one frame repeated \(replay check only\)/);
  assert.doesNotMatch(toggles, /Present a spoof/);
});

test("f40 the console sends canonical ids and refuses invalid ones", async () => {
  const app = await consoleApp();
  await go(app, "enrol");
  app.dom.el("#eId").value = "S-01";
  await runCapture(app);
  assert.equal(app.posts()[0].body.user_id, "s-01");
  const bad = await consoleApp();
  await go(bad, "enrol");
  bad.dom.el("#eId").value = "has space";
  await runCapture(bad);
  assert.equal(bad.posts().length, 0);
});

test("f40 a roster saved with mixed case is folded to canonical ids", async () => {
  const app = await consoleApp({ storage: ROSTER(["S-01", "s-01", "Bob"]) });
  assert.equal(JSON.stringify(app.S.subjects.map((s) => s.id)), JSON.stringify(["s-01", "bob"]));
});

test("f44 hiding the console mid-capture cancels it", async () => {
  const app = await consoleApp();
  await go(app, "verify");
  await app.click("doCapture");
  await app.clock.advance(1500);
  app.setHidden(true);
  await app.clock.advance(9000);
  assert.equal(app.posts().length, 0);
  assert.ok(app.camera.allEnded());
  assert.equal(app.S.run.state, "idle");
});
