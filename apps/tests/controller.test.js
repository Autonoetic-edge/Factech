"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const { pathToFileURL } = require("url");
const { CONSOLE_HTML, CONSOLE_SCRIPTS } = require("./harness");

const ROOT = path.resolve(__dirname, "..", "..");
const load = (rel) => import(pathToFileURL(path.join(ROOT, rel)).href);

let C, FG;
test.before(async () => {
  const [run, guide, gateway, verdict, presenter, faceGuide] = await Promise.all([
    load("packages/face-sdk/src/workflow/run.ts"), load("packages/face-sdk/src/challenge/guide.ts"),
    load("packages/face-sdk/src/transport/gateway.ts"), load("apps/shared/verdict.js"),
    load("apps/shared/presenter.js"), load("apps/shared/face-guide.js"),
  ]);
  C = {
    createRunController: run.createRunController, assertLive: run.assertLive,
    bindLifecycle: run.bindLifecycle, createGuide: guide.createGuide, guideStep: guide.guideStep,
    GUIDE_CAL: guide.GUIDE_CAL, TARGET_MAX: guide.TARGET_MAX, START_MAX: guide.START_MAX,
    FACE_TOO_CLOSE: guide.FACE_TOO_CLOSE, canonicalUserId: gateway.canonicalUserId,
    validUserId: gateway.isValidUserId, livenessVerdict: gateway.livenessVerdict,
    signalVerdict: verdict.signalVerdict, presenterEnabled: presenter.presenterEnabled,
  };
  FG = faceGuide;
});

const face = (x, y, w, h, vw, vh) => ({
  categories: [{ score: 0.95 }],
  boundingBox: { originX: x, originY: y, width: w, height: h },
  keypoints: [[0.3, 0.38], [0.7, 0.38], [0.5, 0.58], [0.5, 0.78]]
    .map(([fx, fy]) => ({ x: (x + fx * w) / vw, y: (y + fy * h) / vh })),
});

const FRAME = { videoW: 480, videoH: 640, viewW: 300, viewH: 400 };
const centred = (hFrac) => {
  const h = (hFrac * 400) / 0.625, w = h * 0.9;
  return face(240 - w / 2, 296 - h / 2, w, h, 480, 640);
};

test("the console builds on the SDK and apps/shared, and carries no copy of either", () => {

  const code = CONSOLE_SCRIPTS.map((x) => x.code).join("\n");
  for (const fn of ["createRunController", "mpEncode", "buildFaceScan", "signalVerdict", "createGuide"]) {
    assert.doesNotMatch(code, new RegExp("function " + fn + "\\("), "console carries a copy of " + fn);
  }
  assert.doesNotMatch(code, /FACETECH-(CONST|SDK|CAPTURE|CONTROLLER)-START|fetch\('\/'/, "console still loads page blocks");
  assert.match(CONSOLE_HTML, /<script type="module" src="\.\/deps\.js"><\/script>/);
  const deps = require("fs").readFileSync(path.join(ROOT, "apps/console/deps.js"), "utf8");
  assert.match(deps, /from '\/sdk\/internal\.js'/);
  assert.match(deps, /from '\/shared\/verdict\.js'/);
});

test("f05 run token snapshots op and user and rejects stale continuations", async () => {
  const cleaned = [];
  const ctl = C.createRunController({ cleanup: (run, why) => cleaned.push([run.id, why]) });
  const a = ctl.start("verify", "alice");
  assert.equal(a.op, "verify");
  assert.equal(a.userId, "alice");
  assert.ok(Object.isFrozen(a));
  const b = ctl.start("enroll", "bob");
  assert.equal(ctl.live(a), false);
  assert.equal(ctl.live(b), true);
  assert.throws(() => C.assertLive(ctl, a), /run-cancelled/);
  assert.deepEqual(cleaned, [[a.id, "superseded"]]);
});

test("f12 f13 guard always cleans up, on success, on throw and when cancelled", async () => {

  const cleaned = [];
  const ctl = C.createRunController({ cleanup: (run, why) => cleaned.push(why) });
  const r1 = ctl.start("verify", "a");
  await ctl.guard(r1, async () => "ok");
  const r2 = ctl.start("verify", "a");
  await assert.rejects(ctl.guard(r2, async () => { throw new Error("capture blew up"); }));
  const r3 = ctl.start("verify", "a");
  await ctl.guard(r3, async () => { ctl.cancel("navigated"); });
  assert.deepEqual(cleaned, ["finished", "finished", "navigated"]);
  assert.equal(ctl.current(), null);
});

test("f-stale-cleanup a stale run's finally does not run the page-wide cleanup", async () => {
  const cleaned = [];
  const ctl = C.createRunController({ cleanup: (run, why) => cleaned.push([run.id, why]) });
  const a = ctl.start("verify", "alice");
  let finishA;
  const bodyA = ctl.guard(a, () => new Promise((r) => (finishA = r)));
  ctl.cancel("navigated");
  const b = ctl.start("verify", "bob");
  finishA();
  await bodyA;
  assert.deepEqual(cleaned, [[a.id, "navigated"]], "A's stale finally cleaned up B");
  assert.equal(ctl.live(b), true);
  await ctl.guard(b, async () => "ok");
  assert.deepEqual(cleaned, [[a.id, "navigated"], [b.id, "finished"]]);
});

test("f-stale-cleanup each run releases only what it acquired", async () => {
  const released = [];
  const ctl = C.createRunController({});
  const a = ctl.start("verify", "alice");
  let finishA;
  const bodyA = ctl.guard(a, () => new Promise((r) => (finishA = r)));
  assert.equal(ctl.own(a, () => released.push("a-stream")), true);
  ctl.cancel("navigated");
  assert.deepEqual(released, ["a-stream"], "a cancelled run keeps its stream");
  const b = ctl.start("verify", "bob");
  assert.equal(ctl.own(b, () => released.push("b-stream")), true);

  assert.equal(ctl.own(a, () => released.push("a-late")), false);
  assert.deepEqual(released, ["a-stream", "a-late"]);
  finishA();
  await bodyA;
  assert.deepEqual(released, ["a-stream", "a-late"], "A's finally released B's stream");
  await ctl.guard(b, async () => "ok");
  assert.deepEqual(released, ["a-stream", "a-late", "b-stream"]);

  const c = ctl.start("verify", "carol");
  ctl.own(c, () => { throw new Error("already gone"); });
  ctl.own(c, () => released.push("c-timer"));
  ctl.cancel("navigated");
  ctl.cancel("navigated");
  assert.deepEqual(released.slice(3), ["c-timer"]);
});

test("f44 hidden page and pagehide both interrupt", () => {
  const L = {};
  const on = (t, f) => { L[t] = f; };
  const doc = { hidden: false, addEventListener: on, removeEventListener() {} };
  const win = { addEventListener: on, removeEventListener() {} };
  const got = [];
  C.bindLifecycle(doc, win, (why) => got.push(why));
  L.visibilitychange();
  doc.hidden = true; L.visibilitychange();
  L.pagehide();
  assert.deepEqual(got, ["hidden", "pagehide"]);
});

test("f08 guidance is hold, then move, then done only on measured growth vs the issued target", () => {
  const G = C.createGuide({ params: { settle_ms: 1500, target: 0.3 } });
  C.guideStep(G, 0, 0.50);
  C.guideStep(G, 500, 0.51);
  C.guideStep(G, 1000, 0.49);
  assert.equal(G.phase, "hold");

  C.guideStep(G, 2000, 0.515);
  assert.equal(G.phase, "move");
  assert.equal(G.baseline, 0.50);
  const needed = 0.50 * Math.exp(0.3 * C.GUIDE_CAL);
  C.guideStep(G, 3000, needed - 0.01);
  assert.equal(G.phase, "move");
  C.guideStep(G, 3500, needed + 0.001);
  assert.equal(G.phase, "done");
  C.guideStep(G, 4000, 0.60);
  assert.equal(G.phase, "done");
});

test("f08 a 69% -> 71% face is nowhere near done", () => {
  const G = C.createGuide({ params: { settle_ms: 900, target: 0.22 } });
  C.guideStep(G, 0, 0.69);
  C.guideStep(G, 1000, 0.71);
  assert.equal(G.phase, "move");
  assert.ok(G.progress < 0.2, String(G.progress));
});

test("f09 f11 with no measurement the guide says hold then move and never done", () => {
  const G = C.createGuide({ params: { settle_ms: 2400, target: 0.25 } });
  for (let t = 0; t <= 5500; t += 500) {
    C.guideStep(G, t, null);
    assert.equal(G.phase, t < 2400 ? "hold" : "move", "at " + t);
  }
});

test("f08 start gate leaves room for the largest target", () => {
  assert.ok(C.START_MAX * Math.exp(C.TARGET_MAX * C.GUIDE_CAL) <= C.FACE_TOO_CLOSE + 1e-9);
  assert.ok(C.START_MAX > 0.55 && C.START_MAX < 0.65, String(C.START_MAX));
  assert.equal(FG.classify([centred(0.69)], FRAME).cue, "close");
  assert.equal(FG.classify([centred(0.55)], FRAME).cue, "good");

  assert.equal(FG.classify([centred(0.69)], { ...FRAME, maxH: FG.RECORD_MAX_H }).cue, "good");
});

test("f47 a bystander outside the visible (object-fit:cover) region is not a second face", () => {

  const LAND = { videoW: 640, videoH: 480, viewW: 300, viewH: 400 };
  const user = face(260, 150, 120, 150, 640, 480);
  const bystander = face(10, 200, 80, 90, 640, 480);
  assert.equal(FG.visible([user, bystander], LAND).length, 1);
  assert.notEqual(FG.classify([user, bystander], LAND).cue, "multi");
  const inView = face(180, 60, 60, 60, 640, 480);
  assert.equal(FG.classify([user, inView], LAND).cue, "multi");
});

test("f45 brightness is measured on the face, not the background", () => {
  const w = 96, h = 128, data = new Uint8ClampedArray(w * h * 4);
  const rect = { x: 30, y: 40, w: 36, h: 48 };
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const inFace = x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h;
    const v = inFace ? 120 : 245, i = (y * w + x) * 4;
    data[i] = data[i + 1] = data[i + 2] = v;
  }
  const whole = FG.meanLuma(data, w, h, null), faceOnly = FG.meanLuma(data, w, h, rect);
  assert.ok(whole > 205, "whole frame " + whole + " would have blocked the start");
  assert.ok(Math.abs(faceOnly - 120) < 1e-6);
  assert.equal(FG.meanLuma(data, w, h, { x: 200, y: 200, w: 5, h: 5 }), null);

  const g = FG.createFaceGuide({ lumaMin: 55, lumaMax: 205 });
  FG.guideTick(g, [centred(0.5)], FRAME, 1000, { luma: faceOnly });
  assert.equal(g.cue, "good");
  FG.guideTick(g, [centred(0.5)], FRAME, 1066, { luma: 20 });
  assert.equal(g.cue, "dark");
});

test("f42 a detector exception expires stale evidence and repeated ones disable the guide", () => {

  const g = FG.createFaceGuide();
  FG.guideTick(g, [centred(0.5)], FRAME, 1000);
  assert.equal(g.goodSince, 1000);
  FG.guideTick(g, [centred(0.5)], FRAME, 1000 + FG.DET_STALE_MS + 1);
  assert.equal(g.goodSince, 1000 + FG.DET_STALE_MS + 1, "an old good read must not arm the gate");
  assert.equal(g.armed, false);
  const H = FG.createHealth();
  FG.healthStep(H, false); FG.healthStep(H, false);
  assert.equal(H.available, true);
  FG.healthStep(H, false);
  assert.equal(H.available, false);
  assert.equal(H.errors, FG.DETECTOR_MAX_ERRORS);
  FG.healthStep(H, true);
  assert.equal(H.available, false, "once off, off for good");
});

test("f42 a success resets the error count", () => {
  const H = FG.createHealth();
  FG.healthStep(H, false);
  FG.healthStep(H, false);
  FG.healthStep(H, true);
  FG.healthStep(H, false);
  assert.equal(H.available, true);
});

test("f43 a fresh guide carries no loss or good from the attempt before", () => {

  const g = FG.createFaceGuide();
  FG.guideTick(g, [], FRAME, 100);
  assert.equal(FG.probeOf(g).lost, true);
  Object.assign(g, FG.createFaceGuide());
  assert.equal(FG.probeOf(g).lost, true, "a fresh guide has seen nothing yet: none");
  assert.equal(g.goodSince, 0);
  assert.equal(g.armed, false);
  assert.equal(g.lastAt, 0, "and no stale clock");
});

test("f41 presenter mode needs ?presenter=1", () => {
  assert.equal(C.presenterEnabled(""), false);
  assert.equal(C.presenterEnabled("?presenter=0"), false);
  assert.equal(C.presenterEnabled("?x=1&presenter=1"), true);
});

test("f40 canonical user ids", () => {
  assert.equal(C.canonicalUserId("  S-01 "), "s-01");
  assert.equal(C.validUserId("s-01"), true);
  assert.equal(C.validUserId("a".repeat(64)), true);
  for (const bad of ["", "a b", "x@y", "a".repeat(65), "ünï"]) assert.equal(C.validUserId(bad), false, bad);
});

test("f15 f16 verdicts come from what the engine said", () => {
  assert.equal(C.livenessVerdict({ live: true, enforced: true }), "passed");
  assert.equal(C.livenessVerdict({ live: false, enforced: false }), "not-enforced");
  assert.equal(C.livenessVerdict({ live: true, enforced: false }), "not-enforced");
  assert.equal(C.livenessVerdict(null), "unknown");
  assert.equal(C.signalVerdict({ score: null, ok: null, advisory: true }), "advisory");
  assert.equal(C.signalVerdict({ score: 0.2, ok: false }), "failed");
  assert.equal(C.signalVerdict({ score: 0.9, ok: true }), "passed");
});
