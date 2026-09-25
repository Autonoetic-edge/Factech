"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const ROOT = path.resolve(__dirname, "..", "..");
const load = (rel) => import(pathToFileURL(path.join(ROOT, rel)).href);

let G, M;
test.before(async () => {
  G = await load("apps/shared/face-guide.js");
  M = await load("apps/shared/messages.js");
});

const FRAME = { videoW: 480, videoH: 640, viewW: 300, viewH: 400 };
const REC = { ...FRAME, maxH: 0.92 };

function face({ h = 0.5, cx = 0.5, cy = 0.4625, score = 0.95, eyeRatio = 0.4,
  tiltDeg = 0, noseOff = 0, order = "ok", aspect = 0.9 } = {}) {
  const H = h * FRAME.videoH, W = H * aspect;
  const x0 = cx * FRAME.videoW - W / 2, y0 = cy * FRAME.videoH - H / 2;
  const mx = x0 + W / 2, eyeY = y0 + 0.38 * H, d = eyeRatio * W;
  const t = (tiltDeg * Math.PI) / 180;
  const e1 = { x: mx - (d / 2) * Math.cos(t), y: eyeY - (d / 2) * Math.sin(t) };
  const e2 = { x: mx + (d / 2) * Math.cos(t), y: eyeY + (d / 2) * Math.sin(t) };
  let noseY = y0 + 0.58 * H, mouthY = y0 + 0.78 * H;
  if (order === "mouth-up") [noseY, mouthY] = [y0 + 0.58 * H, y0 + 0.50 * H];
  if (order === "nose-up") noseY = y0 + 0.30 * H;
  const nose = { x: mx + noseOff * d, y: noseY }, mouth = { x: mx, y: mouthY };
  const n = (p) => ({ x: p.x / FRAME.videoW, y: p.y / FRAME.videoH });
  return {
    categories: [{ score }],
    boundingBox: { originX: x0, originY: y0, width: W, height: H },
    keypoints: [n(e1), n(e2), n(nose), n(mouth), n({ x: x0, y: eyeY }), n({ x: x0 + W, y: eyeY })],
  };
}

function run(detsAt, ms, frame = FRAME) {
  const g = G.createFaceGuide();
  let armedAt = null;

  for (let i = 0, t = 0; t <= ms; t = Math.round((++i * 1000) / G.TICK_HZ)) {
    G.guideTick(g, detsAt(t), frame, 1000 + t);
    if (g.armed && armedAt === null) armedAt = t;
  }
  return { g, armedAt };
}

test("the starting thresholds are the ones in the brief", () => {

  assert.equal(G.MIN_SCORE, 0.80);
  assert.equal(G.MAX_TILT_DEG, 25);
  assert.deepEqual([G.EYE_DIST_MIN, G.EYE_DIST_MAX], [0.25, 0.60]);
  assert.equal(G.NOSE_OFFSET_MAX, 0.35);
  assert.deepEqual([G.START_MIN_H, G.START_MAX_H], [0.35, 0.62]);
  assert.equal(G.GOOD_HOLD_MS, 800);
  assert.equal(G.TICK_HZ, 15);
  assert.deepEqual([G.SHAKE_STEP, G.SHAKE_TICKS], [0.04, 5]);
  assert.equal(G.EARLY_GROWTH, 0.08);
  assert.equal(G.DETECTOR_MAX_ERRORS, 3);
});

test("a centred upright face in range is good", () => {
  const r = G.classify([face()], FRAME);
  assert.equal(r.cue, "good");
  assert.ok(Math.abs(r.face.h - 0.5) < 1e-9);
  assert.equal(r.shape.ok, true);
});

test("nothing in view is none; two faces in view is multi", () => {
  assert.equal(G.classify([], FRAME).cue, "none");
  assert.equal(G.classify(null, FRAME).cue, "none");
  assert.equal(G.classify([face({ cx: 0.3 }), face({ cx: 0.7 })], FRAME).cue, "multi");
});

test("a bystander cut off by object-fit:cover is not visible, but is still in the uploaded frame (f47, finding 9)", () => {

  const land = { videoW: 640, videoH: 480, viewW: 300, viewH: 400 };
  const offscreen = { categories: [{ score: 0.9 }], keypoints: [],
    boundingBox: { originX: 10, originY: 200, width: 80, height: 90 } };
  assert.equal(G.visible([offscreen], land).length, 0);
  assert.equal(G.inFrame([offscreen], land).length, 1);

  const user = face({ h: 0.5 });
  const userLand = { ...user, boundingBox: { ...user.boundingBox,
    originX: user.boundingBox.originX + 80, originY: user.boundingBox.originY - 80 } };
  userLand.keypoints = user.keypoints.map((k) => ({ x: (k.x * 480 + 80) / 640, y: (k.y * 640 - 80) / 480 }));
  assert.equal(G.visible([userLand], land).length, 1);
  assert.notEqual(G.classify([userLand], land).cue, "hidden");
  const r = G.classify([userLand, offscreen], land);
  assert.equal(r.cue, "hidden", "the engine sees the whole frame, so the guide must not say Ready");
  assert.ok(G.LOSS_CUES.includes("hidden"), "a hidden bystander during recording stops the scan");
  assert.equal(G.probeOf({ face: r.face, cue: r.cue }).lost, true);
});

test("a low detector score is not a face, whatever its shape", () => {
  assert.equal(G.classify([face({ score: 0.79 })], FRAME).cue, "notface");
  assert.equal(G.classify([face({ score: 0.79 })], FRAME).shape.reason, "score");
  assert.equal(G.classify([face({ score: 0.80 })], FRAME).cue, "good");
});

test("problem D: each broken face rule on its own makes it not a face", () => {
  const cases = [
    [{ order: "mouth-up" }, "order"],
    [{ order: "nose-up" }, "order"],
    [{ tiltDeg: 25 }, "tilt"],
    [{ tiltDeg: -40 }, "tilt"],
    [{ eyeRatio: 0.2 }, "eyes"],
    [{ eyeRatio: 0.65 }, "eyes"],
    [{ noseOff: 0.4 }, "nose"],
    [{ noseOff: -0.4 }, "nose"],
  ];
  for (const [opts, reason] of cases) {
    const r = G.classify([face(opts)], FRAME);
    assert.equal(r.cue, "notface", JSON.stringify(opts));
    assert.equal(r.shape.reason, reason, JSON.stringify(opts));
  }

  for (const opts of [{ tiltDeg: 24 }, { tiltDeg: -24 }, { eyeRatio: 0.26 }, { eyeRatio: 0.59 },
    { noseOff: 0.34 }, { noseOff: -0.34 }]) {
    assert.equal(G.classify([face(opts)], FRAME).cue, "good", JSON.stringify(opts));
  }
});

test("a detection with no keypoints is not a face", () => {
  const d = face();
  d.keypoints = [];
  assert.equal(G.classify([d], FRAME).shape.reason, "keypoints");
});

test("too far, too close and off centre", () => {
  assert.equal(G.classify([face({ h: 0.34 })], FRAME).cue, "far");
  assert.equal(G.classify([face({ h: 0.36 })], FRAME).cue, "good");
  assert.equal(G.classify([face({ h: 0.61 })], FRAME).cue, "good");
  assert.equal(G.classify([face({ h: 0.63 })], FRAME).cue, "close");

  assert.equal(G.classify([face({ h: 0.8 })], REC).cue, "good");
  assert.equal(G.classify([face({ h: 0.93 })], REC).cue, "close");
  assert.equal(G.classify([face({ cx: 0.5 + 0.6 * G.OVAL.rx + 0.01 })], FRAME).cue, "offcentre");
  assert.equal(G.classify([face({ cx: 0.5 - 0.6 * G.OVAL.rx - 0.01 })], FRAME).cue, "offcentre");
  assert.equal(G.classify([face({ cy: G.OVAL.cy + 0.6 * G.OVAL.ry + 0.01, h: 0.4 })], FRAME).cue, "offcentre");
});

test("distance is judged before position, and shape before both", () => {
  assert.equal(G.classify([face({ h: 0.3, cx: 0.8 })], FRAME).cue, "far");
  assert.equal(G.classify([face({ h: 0.3, order: "mouth-up" })], FRAME).cue, "notface");
});

test("good arms only after GOOD_HOLD_MS of uninterrupted good", () => {
  const { armedAt } = run(() => [face()], 2000);
  assert.ok(armedAt !== null && armedAt >= G.GOOD_HOLD_MS && armedAt < G.GOOD_HOLD_MS + 1000 / G.TICK_HZ + 1,
    String(armedAt));
});

test("one bad reading restarts the hold", () => {

  const { armedAt } = run((t) => (t === 600 ? [] : [face()]), 3000);
  assert.ok(armedAt >= 600 + G.GOOD_HOLD_MS, String(armedAt));
});

test("problem C: one fast move is 'hold', SHAKE_TICKS in a row is 'shaky'", () => {
  const g = G.createFaceGuide();
  const step = G.SHAKE_STEP + 0.01;
  let t = 1000;
  G.guideTick(g, [face({ cx: 0.45 })], FRAME, t);
  G.guideTick(g, [face({ cx: 0.45 + step })], FRAME, (t += 66));
  assert.equal(g.cue, "hold");
  let x = 0.45 + step;
  for (let i = 2; i <= G.SHAKE_TICKS; i++) {
    x += i % 2 ? step : -step;
    G.guideTick(g, [face({ cx: x })], FRAME, (t += 66));
    assert.equal(g.cue, i >= G.SHAKE_TICKS ? "shaky" : "hold", "tick " + i);
    assert.equal(g.armed, false);
  }
  G.guideTick(g, [face({ cx: x })], FRAME, (t += 66));
  assert.equal(g.cue, "good", "still again");
});

test("a small drift is not movement", () => {
  const { g, armedAt } = run((t) => [face({ cx: 0.5 + (t / 1000) * 0.02 })], 2000);
  assert.equal(g.cue, "good");
  assert.ok(armedAt !== null);
});

test("the SDK probe reports height, and loss only for none, multi and off centre", () => {
  const g = G.createFaceGuide();
  G.guideTick(g, [face({ h: 0.5 })], FRAME, 1000);
  assert.deepEqual(G.probeOf(g), { height: 0.5, lost: false });
  G.guideTick(g, [], FRAME, 1100);
  assert.deepEqual(G.probeOf(g), { height: null, lost: true });
  G.guideTick(g, [face({ cx: 0.9 })], FRAME, 1200);
  assert.equal(G.probeOf(g).lost, true);
  for (const opts of [{ h: 0.3 }, { order: "mouth-up" }]) {
    G.guideTick(g, [face(opts)], FRAME, 1300);
    assert.equal(G.probeOf(g).lost, false, JSON.stringify(opts));
  }
});

test("growth over EARLY_GROWTH for EARLY_TICKS readings trips; less does not", () => {
  const E = G.createEarlyMove();
  G.earlyMoveStep(E, 0.5);
  assert.equal(E.baseline, 0.5);
  G.earlyMoveStep(E, 0.5 * 1.07);
  G.earlyMoveStep(E, 0.5 * 1.07);
  G.earlyMoveStep(E, 0.5 * 1.07);
  assert.equal(E.tripped, false, "7% is inside the noise allowance");
  G.earlyMoveStep(E, 0.5 * 1.09);
  assert.equal(E.tripped, false, "one reading is not enough");
  G.earlyMoveStep(E, 0.5 * 1.09);
  assert.equal(E.tripped, true);
  G.earlyMoveStep(E, 0.5);
  assert.equal(E.tripped, true, "latched");
});

test("a missing reading breaks the run of growth but keeps the baseline", () => {
  const E = G.createEarlyMove();
  G.earlyMoveStep(E, 0.5);
  G.earlyMoveStep(E, 0.56);
  G.earlyMoveStep(E, null);
  G.earlyMoveStep(E, 0.56);
  assert.equal(E.tripped, false);
  assert.equal(E.baseline, 0.5);
  G.earlyMoveStep(E, 0.56);
  assert.equal(E.tripped, true);
});

test("no reading at all never trips", () => {
  const E = G.createEarlyMove();
  for (let i = 0; i < 30; i++) G.earlyMoveStep(E, null);
  assert.equal(E.tripped, false);
  assert.equal(E.baseline, null);
});

test("DETECTOR_MAX_ERRORS failures in a row turn the guide off, for good", () => {
  const H = G.createHealth();
  G.healthStep(H, false); G.healthStep(H, false); G.healthStep(H, true);
  assert.equal(H.available, true, "a success in between resets the count");
  for (let i = 0; i < G.DETECTOR_MAX_ERRORS; i++) G.healthStep(H, false);
  assert.equal(H.available, false);
  G.healthStep(H, true);
  assert.equal(H.available, false, "latched: the page runs without a guide from here");
});

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

test("synthetic: 20 upright faces with detector jitter all arm within 3 s", () => {
  const rand = rng(7);
  let armed = 0;
  for (let i = 0; i < 20; i++) {
    const base = { h: 0.40 + 0.2 * rand(), eyeRatio: 0.32 + 0.16 * rand(),
      tiltDeg: -12 + 24 * rand(), noseOff: -0.2 + 0.4 * rand() };
    const { armedAt } = run(() => [face({ ...base,
      cx: 0.5 + (rand() - 0.5) * 0.02, cy: 0.4625 + (rand() - 0.5) * 0.02,
      h: base.h * (1 + (rand() - 0.5) * 0.03), score: 0.85 + 0.14 * rand() })], 3000);
    if (armedAt !== null) armed++;
  }
  assert.equal(armed, 20);
});

test("synthetic: 20 non-face shapes never reach good", () => {
  const rand = rng(11);
  const broken = [
    () => ({ order: "mouth-up" }), () => ({ order: "nose-up" }),
    () => ({ tiltDeg: 30 + 40 * rand() }), () => ({ eyeRatio: 0.05 + 0.18 * rand() }),
    () => ({ eyeRatio: 0.62 + 0.3 * rand() }), () => ({ noseOff: 0.4 + 0.5 * rand() }),
    () => ({ score: 0.5 + 0.29 * rand() }),
  ];
  for (let i = 0; i < 20; i++) {
    const opts = { h: 0.4 + 0.2 * rand(), ...broken[i % broken.length]() };
    const { g, armedAt } = run(() => [face(opts)], 3000);
    assert.equal(armedAt, null, JSON.stringify(opts));
    assert.notEqual(g.cue, "good", JSON.stringify(opts));
  }
});

test("the steadiness limits are OFF until phone readings set them", () => {
  assert.deepEqual([G.SHARP_MIN, G.KP_JUMP_MAX, G.SCORE_JUMP_MAX], [null, null, null]);

  let i = 0;
  const { armedAt, g } = run(() => [face({ noseOff: (i++ % 2) * 0.3, score: 0.8 + (i % 2) * 0.19 })],
    3000, FRAME);
  assert.notEqual(armedAt, null);
  assert.equal(g.unsteady, "");
});

function runWith(limits, detsAt, sharpAt, ms) {
  const g = G.createFaceGuide(limits);
  let armedAt = null;
  for (let i = 0, t = 0; t <= ms; t = Math.round((++i * 1000) / G.TICK_HZ)) {
    G.guideTick(g, detsAt(t), FRAME, 1000 + t, { sharp: sharpAt(t) });
    if (g.armed && armedAt === null) armedAt = t;
  }
  return { g, armedAt };
}

test("a blurred frame is 'hold' and restarts the hold", () => {
  const L = { sharpMin: 50 };
  const steady = runWith(L, () => [face()], () => 80, 1500);
  assert.equal(steady.armedAt, 800);
  const blurred = runWith(L, () => [face()], () => 20, 3000);
  assert.equal(blurred.armedAt, null);
  assert.equal(blurred.g.cue, "hold");
  assert.equal(blurred.g.unsteady, "blur");

  const once = runWith(L, () => [face()], (t) => (t === 600 ? 20 : 80), 3000);
  assert.ok(once.armedAt >= 1400, String(once.armedAt));

  assert.equal(runWith(L, () => [face()], () => null, 1500).armedAt, 800);
});

test("keypoints jumping ON the face restart the hold; the face moving does not", () => {
  const L = { kpJumpMax: 0.05 };

  const slide = runWith(L, (t) => [face({ cx: 0.46 + t / 100000 })], () => null, 1500);
  assert.ok(slide.g.kpJump < 1e-9, String(slide.g.kpJump));
  assert.equal(slide.armedAt, 800);

  const sweep = runWith(L, (t) => [face({ noseOff: t === 600 ? 0.3 : 0 })], () => null, 3000);
  assert.ok(sweep.armedAt >= 1400, String(sweep.armedAt));
  assert.ok(sweep.g.recent.length > 0);
});

test("a score jump restarts the hold", () => {
  const L = { scoreJumpMax: 0.08 };
  const dip = runWith(L, (t) => [face({ score: t === 600 ? 0.85 : 0.97 })], () => null, 3000);
  assert.ok(dip.armedAt >= 1400, String(dip.armedAt));
  const small = runWith(L, (t) => [face({ score: t % 2 ? 0.95 : 0.97 })], () => null, 1500);
  assert.equal(small.armedAt, 800);
});

test("distance and position still come before steadiness", () => {
  const { g } = runWith({ sharpMin: 50 }, () => [face({ h: 0.3 })], () => 5, 500);
  assert.equal(g.cue, "far");
});

test("boxKeypoints do not change when the face moves or changes size", () => {
  const a = G.boxKeypoints(face({ h: 0.4, cx: 0.45 }), FRAME);
  const b = G.boxKeypoints(face({ h: 0.55, cx: 0.55 }), FRAME);
  a.forEach((p, i) => {
    assert.ok(Math.abs(p.x - b[i].x) < 1e-9 && Math.abs(p.y - b[i].y) < 1e-9);
  });
  assert.equal(G.boxKeypoints({ boundingBox: null, keypoints: [] }, FRAME), null);
});

test("sharpCrop is the middle of the box, clamped to the video, null when tiny", () => {
  const d = face({ h: 0.5 });
  const bb = d.boundingBox, c = G.sharpCrop(d, FRAME);
  assert.ok(Math.abs(c.sx - (bb.originX + 0.2 * bb.width)) < 1e-9);
  assert.ok(Math.abs(c.sh - 0.6 * bb.height) < 1e-9);

  const edge = G.sharpCrop({ boundingBox: { originX: -100, originY: 400, width: 300, height: 300 } }, FRAME);
  assert.deepEqual([edge.sx, edge.sy + edge.sh], [0, FRAME.videoH]);
  assert.equal(G.sharpCrop({ boundingBox: { originX: 0, originY: 0, width: 10, height: 10 } }, FRAME), null);
});

test("Laplacian variance: flat is 0, edges are high, blur lowers it", () => {
  const N = 32, sharp = new Float32Array(N * N), blur = new Float32Array(N * N);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) sharp[y * N + x] = ((x >> 2) + (y >> 2)) % 2 ? 255 : 0;
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      let s = 0, n = 0;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
        const yy = y + dy, xx = x + dx;
        if (yy >= 0 && yy < N && xx >= 0 && xx < N) { s += sharp[yy * N + xx]; n++; }
      }
      blur[y * N + x] = s / n;
    }
  }
  assert.equal(G.laplacianVariance(new Float32Array(N * N).fill(128), N, N), 0);
  const vs = G.laplacianVariance(sharp, N, N), vb = G.laplacianVariance(blur, N, N);
  assert.ok(vs > 1000 && vb < vs / 4, `sharp ${vs} blur ${vb}`);
  assert.deepEqual([...G.grayOf(new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]), 2)]
    .map(Math.round), [255, 0]);
});

test("the debug record is rounded and carries no image and no user id", () => {
  const g = G.createFaceGuide();
  const d = face({ h: 0.4567891 });
  G.guideTick(g, [d], FRAME, 1000);
  const rec = G.debugRecord([d], FRAME, g);
  assert.deepEqual(Object.keys(rec).sort(),
    ["box", "cue", "eyes", "h", "kp", "n", "nose", "score", "tilt", "video", "view", "why",
      "sharp", "sharpLow", "kpJump", "kpJumpHi", "scoreJump", "scoreJumpHi", "aspect", "unsteady",
      "luma"].sort());
  assert.equal(rec.h, 0.457);
  assert.equal(rec.kp.length, 6);
  for (const [x, y] of rec.kp) {
    assert.equal(Math.round(x * 1000) / 1000, x);
    assert.equal(Math.round(y * 1000) / 1000, y);
  }
});

test("the start limit is the SDK's START_MAX, rounded to 0.62", () => {

  const src = fs.readFileSync(path.join(ROOT, "packages/face-sdk/src/challenge/guide.ts"), "utf8");
  const num = (name) => Number(src.match(new RegExp(`${name}\\s*=\\s*([0-9.]+)`))[1]);
  const growth = Math.exp(num("TARGET_MAX") * num("GUIDE_CAL"));
  const sdkStartMax = num("FACE_TOO_CLOSE") / growth;
  assert.equal(num("FACE_TOO_CLOSE"), G.RECORD_MAX_H);
  assert.ok(Math.abs(G.START_MAX_H - sdkStartMax) < 0.002, String(sdkStartMax));
  assert.ok(G.START_MAX_H < sdkStartMax * 1.01, "past the SDK test's 1% line");
});

test("a face sample keeps only the measured numbers; a fist or hand keeps all", () => {
  const g = G.createFaceGuide();
  const d = face({ h: 0.45 });
  G.guideTick(g, [d], FRAME, 1000);
  const rec = G.debugRecord([d], FRAME, g);
  for (const label of ["face", "face-glasses", "far", "close", "shaky", "fist-forehead",
    "hand-sweep", "other", "anything"]) {
    const s = G.sampleOf(label, rec);
    assert.deepEqual(Object.keys(s), ["label", ...G.FACE_SAFE_KEYS], label);
    for (const k of G.FACE_SAFE_KEYS) {
      assert.ok(s[k] === null || ["number", "string"].includes(typeof s[k]), `${label}.${k} is one value`);
    }
    assert.equal("kp" in s || "box" in s, false, label + " leaked face geometry");
    assert.equal(s.h, rec.h);
  }
  for (const label of ["fist", "open-hand"]) {
    const s = G.sampleOf(label, rec);
    assert.deepEqual(s, { label, ...rec });
    assert.equal(s.kp.length, 6);
  }
});

const ALL_TEXT = () => [
  ...Object.values(M.CUE_TEXT), ...Object.values(M.PHASE_TEXT),
  ...Object.values(M.ENGINE_TEXT), ...Object.values(M.SDK_TEXT), M.MOVED_EARLY_TEXT,
];

test("every cue has one sentence", () => {
  assert.deepEqual(Object.keys(M.CUE_TEXT).sort(), [...G.CUES].sort());
});

test("no sentence says left or right: the preview is mirrored", () => {
  for (const s of ALL_TEXT()) assert.doesNotMatch(s, /\b(left|right)\b/i, s);
});

test("every sentence is one plain sentence or two, and not empty", () => {
  for (const s of ALL_TEXT()) {
    assert.ok(s.length > 0 && s.length <= 110, s);
    assert.doesNotMatch(s, /[A-Z]{3,}_/, "a code leaked into copy: " + s);
  }
});

test("the engine table is exactly contract §3.1", () => {
  const md = fs.readFileSync(path.join(ROOT, "README.md"), "utf8");
  const section = md.slice(md.indexOf("### 3.1 Canonical error codes"), md.indexOf("### 3.2"));
  const codes = [...section.matchAll(/^\|\s*`([A-Z][A-Z0-9_]{2,})`\s*\|/gm)].map((m) => m[1]);
  assert.ok(codes.length >= 10, "parsed only " + codes.length + " rows from §3.1");
  assert.deepEqual(Object.keys(M.ENGINE_TEXT).sort(), codes.sort());
});

test("the SDK table is exactly the SDK's FailureCode union", () => {
  const ts = fs.readFileSync(path.join(ROOT, "packages/face-sdk/src/types.ts"), "utf8");
  const union = ts.replace(/\/\/.*$/gm, "").match(/export type FailureCode =([\s\S]*?);/);
  assert.ok(union, "FailureCode not found in types.ts");
  const codes = [...union[1].matchAll(/'([A-Z_]+)'/g)].map((m) => m[1]);
  assert.deepEqual(Object.keys(M.SDK_TEXT).sort(), codes.sort());
});

test("the go-ahead cue says Ready, not Good", () => {

  assert.equal(M.CUE_TEXT.good, "Ready. Hold still.");
  assert.doesNotMatch(Object.values(M.CUE_TEXT).join(" "), /\bGood\b/);
});

test("a LIVENESS_FAIL is said by the signal that failed worst", () => {
  const fail = (message) => ({ ok: false, op: "verify", code: "ENGINE_REJECTED",
    engineCode: "LIVENESS_FAIL", message, reason: null });
  const sdkSays = (tail) => "gateway returned 422 LIVENESS_FAIL: liveness check failed: " + tail;
  assert.equal(M.messageFor(fail(sdkSays("challenge"))), M.LIVENESS_SIGNAL_TEXT.challenge);
  assert.equal(M.messageFor(fail(sdkSays("motion, challenge"))), M.LIVENESS_SIGNAL_TEXT.motion, "worst first");
  for (const s of ["challenge", "landmarks", "motion", "duplicates", "timing"]) {
    assert.equal(M.messageFor(fail(sdkSays(s))), M.LIVENESS_SIGNAL_TEXT[s], s);
  }

  assert.equal(M.messageFor(fail("gateway returned 422 LIVENESS_FAIL")), M.ENGINE_TEXT.LIVENESS_FAIL);
  assert.equal(M.messageFor(fail(sdkSays("depth_2"))), M.ENGINE_TEXT.LIVENESS_FAIL);
  assert.equal(M.messageFor(fail(null)), M.ENGINE_TEXT.LIVENESS_FAIL);

  assert.equal(M.messageFor({ ...fail(sdkSays("challenge")), engineCode: "LOW_QUALITY" }), M.ENGINE_TEXT.LOW_QUALITY);
});

test("failedSignals reads the engine's list, for LIVENESS_FAIL and LOW_QUALITY alike", () => {
  assert.deepEqual(M.failedSignals("gateway returned 422 LIVENESS_FAIL: liveness check failed: challenge, motion"),
    ["challenge", "motion"]);
  assert.deepEqual(M.failedSignals(
    "gateway returned 422 LOW_QUALITY: not enough usable frames to check liveness: challenge <hex> capture again"),
    ["challenge"]);
  assert.deepEqual(M.failedSignals("gateway returned 503 BUSY"), []);
  assert.deepEqual(M.failedSignals(undefined), []);
});

test("every liveness signal that can vote has a sentence, and only those", () => {

  const src = fs.readFileSync(path.join(ROOT, "engine/app/liveness.py"), "utf8");
  const names = new Set([...src.matchAll(/name="([a-z_]+)"/g)].map((m) => m[1]));
  const advisory = new Set([...src.matchAll(/Signal\(name="([a-z_]+)",[^)]*advisory=True/g)].map((m) => m[1]));
  for (const n of ["depth", "moire"]) advisory.add(n);
  const voting = [...names].filter((n) => !advisory.has(n)).sort();
  assert.deepEqual(Object.keys(M.LIVENESS_SIGNAL_TEXT).sort(), voting);
});

test("brightness and steadiness limits are all off until tuned from readings", () => {
  assert.deepEqual([G.LUMA_MIN, G.LUMA_MAX, G.SHARP_MIN, G.KP_JUMP_MAX, G.SCORE_JUMP_MAX],
    [null, null, null, null, null]);

  const g = G.createFaceGuide();
  G.guideTick(g, [face()], FRAME, 1000, { luma: 3 });
  assert.equal(g.cue, "good");
  assert.equal(g.luma, 3);

  const L = G.createFaceGuide({ lumaMin: 55, lumaMax: 205 });
  G.guideTick(L, [face()], FRAME, 1000, { luma: 30 });
  assert.equal(L.cue, "dark");
  G.guideTick(L, [face()], FRAME, 1066, { luma: 240 });
  assert.equal(L.cue, "bright");
  G.guideTick(L, [face()], FRAME, 1133, { luma: null });
  assert.equal(L.cue, "good", "no reading is no evidence");
  G.guideTick(L, [face({ h: 0.3 })], FRAME, 1200, { luma: 30 });
  assert.equal(L.cue, "far", "distance before light");
});

test("a gap longer than DET_STALE_MS restarts the good hold (f42)", () => {
  const g = G.createFaceGuide();
  G.guideTick(g, [face()], FRAME, 1000);
  G.guideTick(g, [face()], FRAME, 1500);
  assert.equal(g.goodSince, 1000);
  G.guideTick(g, [face()], FRAME, 1500 + G.DET_STALE_MS + 1);
  assert.equal(g.goodSince, 1500 + G.DET_STALE_MS + 1);
  assert.equal(g.armed, false, "an old good read carried across the gap and armed the capture");
});

test("messageFor: the engine's code wins, then the SDK's, then a fallback", () => {
  const fail = (code, engineCode = null, reason = null) =>
    ({ ok: false, op: "verify", code, engineCode, reason });
  assert.equal(M.messageFor(fail("ENGINE_REJECTED", "LIVENESS_FAIL")), M.ENGINE_TEXT.LIVENESS_FAIL);
  assert.equal(M.messageFor(fail("ENGINE_REJECTED", "NOT_A_CODE")), M.SDK_TEXT.ENGINE_REJECTED);
  assert.equal(M.messageFor(fail("FACE_LOST")), M.SDK_TEXT.FACE_LOST);
  assert.equal(M.messageFor(fail("CANCELLED", null, M.MOVED_EARLY)), M.MOVED_EARLY_TEXT);
  assert.equal(M.messageFor(fail("CANCELLED", null, "user-pressed-cancel")), M.SDK_TEXT.CANCELLED);
  assert.equal(M.messageFor(fail("SOMETHING_NEW")), M.SDK_TEXT.ENGINE_REJECTED);
  assert.equal(M.messageFor({ ok: true, op: "verify", match: false }), "That face did not match.");
  assert.equal(M.messageFor(null), M.SDK_TEXT.BAD_RESPONSE);
});

test("f10 only a match with an enforced liveness pass reads and looks verified", () => {
  const verify = (match, liveness) => ({ ok: true, op: "verify", match, liveness, score: 0.9, threshold: 0.5 });
  assert.equal(M.messageFor(verify(true, "passed")), M.RESULT_TEXT.verified);
  assert.equal(M.resultTone(verify(true, "passed")), "ok");
  assert.equal(M.isAuthenticated(verify(true, "passed")), true);
  for (const liveness of ["failed", "unknown", "not-enforced", undefined, null, "PASSED"]) {
    const r = verify(true, liveness);
    assert.equal(M.messageFor(r), M.RESULT_TEXT.matchNotLive, String(liveness));
    assert.doesNotMatch(M.messageFor(r), /It is you/, String(liveness));
    assert.equal(M.resultTone(r), "alert", String(liveness));
    assert.equal(M.isAuthenticated(r), false, String(liveness));
  }
  for (const liveness of ["passed", "not-enforced"]) {
    assert.equal(M.messageFor(verify(false, liveness)), M.RESULT_TEXT.noMatch);
    assert.equal(M.resultTone(verify(false, liveness)), "alert");
  }
  assert.equal(M.isAuthenticated({ ...verify(true, "passed"), match: "true" }), false, "truthy is not true");
  assert.equal(M.resultTone({ ...verify(true, "passed"), match: 1 }), "alert");
});

test("f10 enrollment is never verification, and says when liveness was not confirmed", () => {
  const enroll = (liveness) => ({ ok: true, op: "enroll", templateId: "t", quality: 0.8, liveness });
  assert.equal(M.messageFor(enroll("passed")), M.RESULT_TEXT.enrolled);
  assert.equal(M.resultTone(enroll("passed")), "ok");
  for (const liveness of ["failed", "unknown", "not-enforced", undefined]) {
    assert.equal(M.messageFor(enroll(liveness)), M.RESULT_TEXT.enrolledNotLive, String(liveness));
    assert.equal(M.resultTone(enroll(liveness)), "alert", String(liveness));
  }
  assert.equal(M.isAuthenticated({ ...enroll("passed"), match: true }), false);
  assert.equal(M.messageFor({ ok: true, op: "something-else", match: true, liveness: "passed" }), M.SDK_TEXT.BAD_RESPONSE);
});

test("f10 required-check errors are never shown as success", () => {
  const fail = (code, engineCode = null) =>
    ({ ok: false, op: "verify", code, engineCode, reason: null, match: true, liveness: "passed" });
  for (const r of [fail("ENGINE_REJECTED", "LIVENESS_FAIL"), fail("ENGINE_REJECTED", "CHALLENGE_FAIL"),
    fail("TIMEOUT"), fail("BAD_RESPONSE"), fail("NETWORK"), fail("CANCELLED"), null, undefined]) {
    assert.equal(M.resultTone(r), "alert", JSON.stringify(r));
    assert.equal(M.isAuthenticated(r), false, JSON.stringify(r));
    assert.notEqual(M.messageFor(r), M.RESULT_TEXT.verified);
  }
});

test("f53 shared/face-detector.js never writes server text as HTML", () => {
  const code = fs.readFileSync(path.join(ROOT, "apps/shared/face-detector.js"), "utf8");
  assert.doesNotMatch(code, /\.(innerHTML|outerHTML)\s*[+]?=|insertAdjacentHTML|document\.write/);
});

test("face-guide.js and messages.js never touch the browser", () => {
  for (const rel of ["apps/shared/face-guide.js", "apps/shared/messages.js"]) {
    const code = fs.readFileSync(path.join(ROOT, rel), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    assert.doesNotMatch(code, /\b(document|window|navigator|performance|requestAnimationFrame|setTimeout)\b/, rel);
    assert.doesNotMatch(code, /^\s*import\b/m, rel + " imports something");
  }
});
