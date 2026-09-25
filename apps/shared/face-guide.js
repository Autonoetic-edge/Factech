export const MIN_SCORE = 0.80;

export const MAX_TILT_DEG = 25;

export const EYE_DIST_MIN = 0.25, EYE_DIST_MAX = 0.60;

export const NOSE_OFFSET_MAX = 0.35;

export const START_MIN_H = 0.35, START_MAX_H = 0.62;

export const RECORD_MAX_H = 0.92;

export const GOOD_HOLD_MS = 800;

export const TICK_HZ = 15;

export const SHAKE_STEP = 0.04, SHAKE_TICKS = 5;

export const EARLY_GROWTH = 0.08, EARLY_TICKS = 2;

export const DETECTOR_MAX_ERRORS = 3;

export const DET_STALE_MS = 1000;

export const SHARP_MIN = null;

export const KP_JUMP_MAX = null;

export const SCORE_JUMP_MAX = null;

export const LUMA_MIN = null, LUMA_MAX = null;

export const SHARP_CROP = Object.freeze({ x: 0.2, y: 0.25, w: 0.6, h: 0.6 });
export const SHARP_SIZE = 64;

export const OVAL = Object.freeze({ cx: 0.5, cy: 0.4625, rx: 0.3267, ry: 0.33 });
export const OFFCENTRE = 0.6;

export const KP = Object.freeze({ EYE_1: 0, EYE_2: 1, NOSE: 2, MOUTH: 3 });

export const CUES = Object.freeze([
  'none', 'notface', 'multi', 'hidden', 'far', 'close', 'offcentre', 'dark', 'bright', 'hold', 'shaky', 'good',
]);

export const LOSS_CUES = Object.freeze(['none', 'multi', 'hidden', 'offcentre']);

export function coverMap(vw, vh, bw, bh) {
  const s = Math.max(bw / vw, bh / vh);
  return { s, ox: (bw - vw * s) / 2, oy: (bh - vh * s) / 2 };
}

function scoreOf(det) {
  const c = det && det.categories && det.categories[0];
  return c && typeof c.score === 'number' ? c.score : null;
}

export function place(det, frame) {
  const bb = det && det.boundingBox;
  if (!bb || !(bb.width > 0) || !(bb.height > 0)) return null;
  const vw = frame.videoW || 1, vh = frame.videoH || 1;
  const m = coverMap(vw, vh, frame.viewW, frame.viewH);
  return {
    cx: (m.ox + (bb.originX + bb.width / 2) * m.s) / frame.viewW,
    cy: (m.oy + (bb.originY + bb.height / 2) * m.s) / frame.viewH,
    h: (bb.height * m.s) / frame.viewH,
  };
}

export function visible(dets, frame) {
  return (dets || []).filter((d) => {
    const p = place(d, frame);
    return p !== null && p.cx >= 0 && p.cx <= 1 && p.cy >= 0 && p.cy <= 1;
  });
}

export function faceShape(det, frame) {
  const score = scoreOf(det);
  const out = { ok: false, reason: '', score, tiltDeg: null, eyeRatio: null, noseOffset: null };
  if (score === null || score < MIN_SCORE) { out.reason = 'score'; return out; }
  const kp = det.keypoints, bb = det.boundingBox;
  if (!kp || kp.length < 4 || !bb) { out.reason = 'keypoints'; return out; }
  const vw = frame.videoW || 1, vh = frame.videoH || 1;
  const pt = (i) => ({ x: kp[i].x * vw, y: kp[i].y * vh });
  const e1 = pt(KP.EYE_1), e2 = pt(KP.EYE_2), nose = pt(KP.NOSE), mouth = pt(KP.MOUTH);
  const eyeY = (e1.y + e2.y) / 2, eyeX = (e1.x + e2.x) / 2;
  const dx = e2.x - e1.x, dy = e2.y - e1.y, eyeDist = Math.hypot(dx, dy);
  out.tiltDeg = Math.atan2(Math.abs(dy), Math.abs(dx)) * 180 / Math.PI;
  out.eyeRatio = eyeDist / bb.width;
  out.noseOffset = eyeDist > 0 ? Math.abs(nose.x - eyeX) / eyeDist : null;

  if (!(eyeY < nose.y && nose.y < mouth.y)) { out.reason = 'order'; return out; }
  if (out.tiltDeg >= MAX_TILT_DEG) { out.reason = 'tilt'; return out; }
  if (out.eyeRatio < EYE_DIST_MIN || out.eyeRatio > EYE_DIST_MAX) { out.reason = 'eyes'; return out; }
  if (out.noseOffset === null || out.noseOffset > NOSE_OFFSET_MAX) { out.reason = 'nose'; return out; }
  out.ok = true;
  return out;
}

export function inFrame(dets, frame) {
  return (dets || []).filter((d) => place(d, frame) !== null);
}

export function classify(dets, frame) {
  const seen = visible(dets, frame);
  if (!seen.length) return { cue: 'none', face: null, shape: null };
  if (seen.length > 1) return { cue: 'multi', face: null, shape: null };
  if (inFrame(dets, frame).length > 1) return { cue: 'hidden', face: null, shape: null };
  const face = place(seen[0], frame), shape = faceShape(seen[0], frame);
  if (!shape.ok) return { cue: 'notface', face, shape };
  if (face.h < START_MIN_H) return { cue: 'far', face, shape };
  if (face.h > (frame.maxH || START_MAX_H)) return { cue: 'close', face, shape };
  if (Math.abs(face.cx - OVAL.cx) > OFFCENTRE * OVAL.rx ||
      Math.abs(face.cy - OVAL.cy) > OFFCENTRE * OVAL.ry) return { cue: 'offcentre', face, shape };
  return { cue: 'good', face, shape };
}

export function boxKeypoints(det, frame) {
  const bb = det && det.boundingBox, kp = det && det.keypoints;
  if (!bb || !kp || kp.length < 4 || !(bb.width > 0) || !(bb.height > 0)) return null;
  const vw = frame.videoW || 1, vh = frame.videoH || 1;
  return kp.slice(0, 4).map((k) => ({
    x: (k.x * vw - bb.originX) / bb.width, y: (k.y * vh - bb.originY) / bb.height,
  }));
}

export function sharpCrop(det, frame) {
  const bb = det && det.boundingBox;
  if (!bb || !(bb.width > 0) || !(bb.height > 0)) return null;
  const vw = frame.videoW || 0, vh = frame.videoH || 0;
  const x0 = Math.max(0, bb.originX + SHARP_CROP.x * bb.width);
  const y0 = Math.max(0, bb.originY + SHARP_CROP.y * bb.height);
  const x1 = Math.min(vw, bb.originX + (SHARP_CROP.x + SHARP_CROP.w) * bb.width);
  const y1 = Math.min(vh, bb.originY + (SHARP_CROP.y + SHARP_CROP.h) * bb.height);
  if (x1 - x0 < 16 || y1 - y0 < 16) return null;
  return { sx: x0, sy: y0, sw: x1 - x0, sh: y1 - y0 };
}

export function grayOf(rgba, n) {
  const g = new Float32Array(n);
  for (let i = 0; i < n; i++) g[i] = 0.299 * rgba[4 * i] + 0.587 * rgba[4 * i + 1] + 0.114 * rgba[4 * i + 2];
  return g;
}

export function meanLuma(data, w, h, rect) {
  let x0 = 0, y0 = 0, x1 = w, y1 = h;
  if (rect) {
    x0 = Math.max(0, Math.floor(rect.x)); y0 = Math.max(0, Math.floor(rect.y));
    x1 = Math.min(w, Math.ceil(rect.x + rect.w)); y1 = Math.min(h, Math.ceil(rect.y + rect.h));
  }
  let sum = 0, n = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * w + x) * 4;
      sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]; n++;
    }
  }
  return n ? sum / n : null;
}

export function laplacianVariance(gray, w, h) {
  let n = 0, sum = 0, sq = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const v = gray[i - 1] + gray[i + 1] + gray[i - w] + gray[i + w] - 4 * gray[i];
      n++; sum += v; sq += v * v;
    }
  }
  if (!n) return null;
  const mean = sum / n;
  return sq / n - mean * mean;
}

export function createFaceGuide(limits = {}) {
  return {
    cue: 'none', face: null, shape: null, last: null, moving: 0, goodSince: 0, armed: false,
    limits: {
      sharpMin: SHARP_MIN, kpJumpMax: KP_JUMP_MAX, scoreJumpMax: SCORE_JUMP_MAX,
      lumaMin: LUMA_MIN, lumaMax: LUMA_MAX, ...limits,
    },
    kpPrev: null, scorePrev: null, sharp: null, kpJump: null, scoreJump: null, aspect: null,
    luma: null, unsteady: '', recent: [], lastAt: 0,
  };
}

function lightOf(G) {
  const L = G.limits;
  if (G.luma === null) return '';
  if (L.lumaMin !== null && G.luma < L.lumaMin) return 'dark';
  if (L.lumaMax !== null && G.luma > L.lumaMax) return 'bright';
  return '';
}

function steadiness(G, det, frame, now, sharp) {
  const kp = det ? boxKeypoints(det, frame) : null;
  const score = det ? scoreOf(det) : null;
  G.kpJump = kp && G.kpPrev
    ? Math.max(...kp.map((p, i) => Math.hypot(p.x - G.kpPrev[i].x, p.y - G.kpPrev[i].y))) : null;
  G.scoreJump = score !== null && G.scorePrev !== null ? Math.abs(score - G.scorePrev) : null;
  G.kpPrev = kp; G.scorePrev = score;
  G.sharp = typeof sharp === 'number' && Number.isFinite(sharp) ? sharp : null;
  G.aspect = det && det.boundingBox && det.boundingBox.width > 0
    ? det.boundingBox.height / det.boundingBox.width : null;
  G.recent.push({ t: now, sharp: G.sharp, kpJump: G.kpJump, scoreJump: G.scoreJump });
  while (G.recent.length && now - G.recent[0].t > GOOD_HOLD_MS) G.recent.shift();
  const L = G.limits;

  if (L.sharpMin !== null && G.sharp !== null && G.sharp < L.sharpMin) return 'blur';
  if (L.kpJumpMax !== null && G.kpJump !== null && G.kpJump > L.kpJumpMax) return 'kp';
  if (L.scoreJumpMax !== null && G.scoreJump !== null && G.scoreJump > L.scoreJumpMax) return 'score';
  return '';
}

export function guideTick(G, dets, frame, now, measure = {}) {

  if (G.lastAt && now - G.lastAt > DET_STALE_MS) {
    G.goodSince = 0; G.last = null; G.moving = 0; G.kpPrev = null; G.scorePrev = null; G.recent = [];
  }
  G.lastAt = now;
  const r = classify(dets, frame);
  G.face = r.face; G.shape = r.shape;
  G.luma = r.face && typeof measure.luma === 'number' && Number.isFinite(measure.luma) ? measure.luma : null;

  const det = r.face ? visible(dets, frame)[0] : null;
  G.unsteady = steadiness(G, det, frame, now, measure.sharp);
  let cue = r.cue;
  if (r.face && G.last) {
    const step = Math.hypot(r.face.cx - G.last.cx, r.face.cy - G.last.cy);
    G.moving = step > SHAKE_STEP ? G.moving + 1 : 0;
  } else {
    G.moving = 0;
  }
  G.last = r.face ? { cx: r.face.cx, cy: r.face.cy } : null;

  const light = cue === 'good' ? lightOf(G) : '';
  if (light) cue = light;
  else if (cue === 'good' && G.moving >= SHAKE_TICKS) cue = 'shaky';
  else if (cue === 'good' && (G.moving > 0 || G.unsteady)) cue = 'hold';
  G.cue = cue;
  if (cue === 'good') { if (!G.goodSince) G.goodSince = now; } else G.goodSince = 0;
  G.armed = cue === 'good' && now - G.goodSince >= GOOD_HOLD_MS;
  return G;
}

export function probeOf(G) {
  return { height: G.face ? G.face.h : null, lost: LOSS_CUES.includes(G.cue) };
}

export function createEarlyMove() {
  return { baseline: null, over: 0, growth: null, tripped: false };
}
export function earlyMoveStep(E, h) {
  if (E.tripped) return E;
  if (!(typeof h === 'number' && h > 0)) { E.over = 0; return E; }
  if (E.baseline === null) { E.baseline = h; E.growth = 0; return E; }
  E.growth = h / E.baseline - 1;
  E.over = E.growth > EARLY_GROWTH ? E.over + 1 : 0;
  if (E.over >= EARLY_TICKS) E.tripped = true;
  return E;
}

export function createHealth() { return { errors: 0, available: true }; }
export function healthStep(H, ok) {
  if (!H.available) return H;
  H.errors = ok ? 0 : H.errors + 1;
  if (H.errors >= DETECTOR_MAX_ERRORS) H.available = false;
  return H;
}

const r3 = (v) => (typeof v === 'number' ? Math.round(v * 1000) / 1000 : v);

export function debugRecord(dets, frame, G) {
  const d = (dets || [])[0];
  return {
    cue: G.cue,
    n: (dets || []).length,
    score: r3(scoreOf(d)),
    box: d && d.boundingBox ? [r3(d.boundingBox.originX), r3(d.boundingBox.originY),
      r3(d.boundingBox.width), r3(d.boundingBox.height)] : null,
    kp: d && d.keypoints ? d.keypoints.slice(0, 6).map((k) => [r3(k.x), r3(k.y)]) : null,
    video: [frame.videoW, frame.videoH],
    view: [r3(frame.viewW), r3(frame.viewH)],
    h: G.face ? r3(G.face.h) : null,
    tilt: G.shape ? r3(G.shape.tiltDeg) : null,
    eyes: G.shape ? r3(G.shape.eyeRatio) : null,
    nose: G.shape ? r3(G.shape.noseOffset) : null,
    why: G.shape && !G.shape.ok ? G.shape.reason : '',

    sharp: r1(G.sharp),
    sharpLow: r1(worst(G.recent, 'sharp', Math.min)),
    kpJump: r3(G.kpJump),
    kpJumpHi: r3(worst(G.recent, 'kpJump', Math.max)),
    scoreJump: r3(G.scoreJump),
    scoreJumpHi: r3(worst(G.recent, 'scoreJump', Math.max)),
    aspect: r3(G.aspect),
    unsteady: G.unsteady,
    luma: r1(G.luma),
  };
}

const r1 = (v) => (typeof v === 'number' ? Math.round(v * 10) / 10 : v);
function worst(recent, key, pick) {
  const vals = (recent || []).map((e) => e[key]).filter((v) => typeof v === 'number');
  return vals.length ? pick(...vals) : null;
}

export const FULL_RECORD_LABELS = Object.freeze(['fist', 'open-hand']);

export const FACE_SAFE_KEYS = Object.freeze([
  'cue', 'score', 'h', 'tilt', 'eyes', 'nose', 'why',
  'sharp', 'sharpLow', 'kpJump', 'kpJumpHi', 'scoreJump', 'scoreJumpHi', 'aspect', 'unsteady', 'luma',
]);

export function sampleOf(label, rec) {
  if (FULL_RECORD_LABELS.includes(label)) return { label, ...rec };
  const out = { label };
  for (const k of FACE_SAFE_KEYS) out[k] = rec[k];
  return out;
}
