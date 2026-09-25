// Turns the face guide's 15-a-second cue into ONE steady line for the camera screen.
// Pure: no DOM, no timers (apps/tests/verify-flow.test.js). The guide itself is
// /shared/face-guide.js, unchanged. It is a guide, never a judge: nothing here is sent
// anywhere, and the server alone decides the check.

export const SETTLE_MS = 600; // a new line must be true for this long before it is shown

// guide cue -> the line shown. Several cues share a line on purpose: fewer, calmer messages.
const LINE = {
  none: 'find', notface: 'find', hidden: 'find', multi: 'one',
  far: 'closer', close: 'back', offcentre: 'centre',
  dark: 'hold', bright: 'hold', hold: 'hold', shaky: 'hold', good: 'hold',
};

// Pre-checks before the automatic start, most important first (LIVENESS_UPGRADE_PLAN Phase 1):
// one face and distance come from the guide's cue above; then frontal, light, blur here.
// The light and blur limits are PROVISIONAL (set 24 Sep 2026, not yet measured on phones);
// the Phase 1 team round sets them from the guide's logged luma/sharp. They are hints: after
// HINT_CAP_MS of the same light or blur hint the start is allowed anyway (plan §9).
export const FRONTAL_MAX = 0.30;   // page yaw units, the engine head gate's "frontal"
export const LUMA_MIN = 55;        // mean luma of the face crop, 0-255
export const BACKLIGHT_GAP = 60;   // the whole frame this much brighter than the face = backlight
export const SHARP_MIN = 12;       // Laplacian variance of the 64x64 face crop
export const HINT_CAP_MS = 10000;

/** @returns 'frontal' | 'light' | 'steady' | null */
export function precheck(guide, { yaw = null, frameLuma = null } = {}) {
  if (Number.isFinite(yaw) && Math.abs(yaw) > FRONTAL_MAX) return 'frontal';
  const luma = guide.luma;
  if (Number.isFinite(luma) && (luma < LUMA_MIN || (Number.isFinite(frameLuma) && frameLuma - luma > BACKLIGHT_GAP))) return 'light';
  if (Number.isFinite(guide.sharp) && guide.sharp < SHARP_MIN) return 'steady';
  return null;
}

// The numbers behind the last pre-check, sent with the capture (X-Facetech-Precheck) so the
// engine trace can set LUMA_MIN / BACKLIGHT_GAP / SHARP_MIN from real phones. Trace data only.
export function precheckReading(guide, { frameLuma = null } = {}, F = null) {
  const r1 = (v) => (Number.isFinite(v) ? Math.round(v * 10) / 10 : null);
  const luma = r1(guide.luma), frame = r1(frameLuma);
  return {
    luma, frame_luma: frame, backlight: luma !== null && frame !== null ? r1(frame - luma) : null,
    sharp: r1(guide.sharp), hint: F?.hint ?? null, capped: F?.hint === 'light' || F?.hint === 'steady', // started under the 10 s hint cap
  };
}

export function createFraming() {
  return { shown: 'find', pending: null, since: 0, hint: null, hintSince: 0 };
}

/** @returns the line to show now: find | one | closer | back | centre | frontal | light | steady | hold | good */
export function framingStep(F, guide, now, extra) {
  let want = guide.armed ? 'good' : LINE[guide.cue] ?? 'find';
  if (want === 'good' || want === 'hold') {
    const pre = precheck(guide, extra);
    if (pre !== F.hint) { F.hint = pre; F.hintSince = now; }
    const capped = (pre === 'light' || pre === 'steady') && now - F.hintSince >= HINT_CAP_MS;
    if (pre && !capped) want = pre;
  } else {
    F.hint = null;
  }
  // A small wobble must not take "Good" away; a real problem (far, off-centre...) still does.
  const next = want === 'hold' && F.shown === 'good' ? 'good' : want;
  if (next === F.shown) { F.pending = null; return F.shown; }
  if (next === 'good') { F.shown = 'good'; F.pending = null; return F.shown; } // the guide already held it 800 ms
  if (F.pending !== next) { F.pending = next; F.since = now; }
  else if (now - F.since >= SETTLE_MS) { F.shown = next; F.pending = null; }
  return F.shown;
}

// Page yaw from the detector's keypoints: 4 x nose offset / interocular, the same formula as
// the engine head gate. Positive = nose towards image-right. null without exactly one face.
export function yawOf(dets, frame) {
  if (!dets || dets.length !== 1) return null;
  const k = dets[0].keypoints;
  if (!k || k.length < 3) return null;
  const dx = (k[1].x - k[0].x) * frame.videoW;
  const dy = (k[1].y - k[0].y) * frame.videoH;
  const d2 = dx * dx + dy * dy;
  if (!(d2 > 64)) return null;
  const nx = (k[2].x - (k[0].x + k[1].x) / 2) * frame.videoW;
  const ny = (k[2].y - (k[0].y + k[1].y) / 2) * frame.videoH;
  return 4 * (nx * dx + ny * dy) / d2;
}

// Browser guidance only. Server landmarks and PAD still decide verification.
export const READY_MS = 800; // placed and still this long: the ring fills and the check starts itself
export const STALE_MS = 1000;
export function sampleOf(g, dets, frame) {
  if (!g.face || !g.shape?.ok || !['good', 'hold', 'shaky'].includes(g.cue) || dets.length !== 1) return null;
  const yaw = yawOf(dets, frame);
  return yaw === null ? null : { ...g.face, yaw };
}
const frontal = s => s && Number.isFinite(s.yaw) && Math.abs(s.yaw) <= FRONTAL_MAX;
const near = (a, b) => Math.abs(a.yaw - b.yaw) <= 0.20
  && Math.hypot(a.cx - b.cx, a.cy - b.cy) <= 0.06 && Math.abs(a.h / b.h - 1) <= 0.10;
export function createPreparation() {
  let samples = [], lastAt = -Infinity, current = null, baseline = null, badSince = null;
  return {
    reset() { samples = []; baseline = null; badSince = null; lastAt = -Infinity; current = null; },
    tick(sample, now) {
      if (now - lastAt > STALE_MS) samples = [];
      lastAt = now; current = sample;
      if (!frontal(sample)) { samples = []; return false; }
      if (samples.some(x => !near(sample, x.sample))) samples = [];
      samples.push({ sample, now });
      // Retain the sample just before the readiness boundary.
      while (samples.length > 1 && now - samples[1].now >= READY_MS) samples.shift();
      return now - samples[0].now >= READY_MS;
    },
    begin(now) { baseline = now - lastAt <= STALE_MS && frontal(current) ? { ...current } : null; badSince = null; },
    disturbed(now) {
      if (now - lastAt > STALE_MS || !baseline) return true;
      if (frontal(current) && near(current, baseline)) { badSince = null; return false; }
      badSince ??= now;
      return now - badSince >= 250;
    },
  };
}

// Turn meter for the single-turn screen (docs/SINGLE_TURN_PLAN.md 1.4). DISPLAY ONLY: it never
// cancels the scan, never changes the SDK's phase and is never sent; the engine decides the turn.
// Baseline = median page yaw during the hold; delta = (yaw - baseline) x direction sign.
export const TURN_MORE = 0.35, TURN_FAR = 0.80; // page units: < 9 deg more, 9-20 deg good, > 20 deg far
export const ZONE_TICKS = 3;                    // a new zone must repeat ~200 ms before it is shown
export function createTurnMeter() {
  return { settle: [], baseline: null, zone: null, pending: null, count: 0 };
}
const median = (xs) => { const s = [...xs].sort((a, b) => a - b), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
/** @returns the zone to show: null (hold) | 'more' | 'good' | 'far' | 'lost' */
export function turnStep(M, yaw, phase, sign) {
  const ok = Number.isFinite(yaw);
  if (phase !== 'move') { if (ok) M.settle.push(yaw); return M.zone; }
  if (M.baseline === null && ok) M.baseline = M.settle.length ? median(M.settle) : yaw;
  let zone = 'lost';
  if (ok && M.baseline !== null) {
    const delta = (yaw - M.baseline) * sign;
    zone = delta < TURN_MORE ? 'more' : delta <= TURN_FAR ? 'good' : 'far';
  }
  if (M.zone === null) { M.zone = zone; return zone; }
  if (zone === M.zone) { M.pending = null; M.count = 0; return M.zone; }
  if (zone !== M.pending) { M.pending = zone; M.count = 1; } else M.count++;
  if (M.count >= ZONE_TICKS) { M.zone = zone; M.pending = null; M.count = 0; }
  return M.zone;
}
