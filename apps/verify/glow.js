// Soft colour glow around the oval (docs/LIVENESS_UPGRADE_PLAN.md Phase 4). DISPLAY ONLY:
// the engine issued the schedule and judges the reflection; the page just shows it.
//
// Timing contract (engine/app/flash.py): every *_ms is an offset from the capture time of
// frame 0. Neutral before the first step; at each step's start_ms a linear fade over fade_ms
// to its rgb; at end_ms the same fade back to neutral. main.js anchors t = 0 on the SDK's
// first frame event, which the SDK fires right after grabbing frame 0, so frame 0 is always
// captured before the first colour (the engine starts it 300-500 ms later).

const rgbOk = (c) => Array.isArray(c) && c.length === 3 && c.every((v) => Number.isInteger(v) && v >= 0 && v <= 255);
const msOk = (v) => Number.isFinite(v) && v >= 0 && v <= 60000;

// The challenge body's `glow` key, or null when absent or malformed (then the page is unchanged).
export function glowOf(body) {
  const g = body?.glow;
  if (!g || typeof g !== 'object' || !rgbOk(g.neutral) || !msOk(g.fade_ms) || !msOk(g.end_ms)) return null;
  if (!Array.isArray(g.steps) || !g.steps.length || g.steps.length > 8) return null;
  let last = -1;
  for (const s of g.steps) {
    if (!rgbOk(s?.rgb) || !msOk(s.start_ms) || s.start_ms <= last || s.start_ms >= g.end_ms) return null;
    last = s.start_ms;
  }
  return {
    neutral: [...g.neutral], fadeMs: g.fade_ms, endMs: g.end_ms,
    steps: g.steps.map((s) => ({ rgb: [...s.rgb], startMs: s.start_ms })),
  };
}

// The colour shown at offset t (ms from frame 0); same arithmetic as flash.displayed().
export function glowColour(glow, t) {
  const keys = [...glow.steps.map((s) => [s.startMs, s.rgb]), [glow.endMs, glow.neutral]];
  let colour = glow.neutral, previous = glow.neutral;
  for (const [start, rgb] of keys) {
    if (t < start) break;
    const w = glow.fadeMs <= 0 ? 1 : Math.min(1, (t - start) / glow.fadeMs);
    colour = previous.map((p, i) => p + (rgb[i] - p) * w);
    previous = rgb;
  }
  return colour.map(Math.round);
}

// prefers-reduced-motion: one slow change to the first issued colour, on the schedule's own
// clock: neutral until the first step, then one linear fade that reaches it at end_ms.
export function reducedGlowColour(glow, t) {
  const { startMs, rgb } = glow.steps[0];
  const w = Math.max(0, Math.min(1, (t - startMs) / Math.max(1, glow.endMs - startMs)));
  return glow.neutral.map((n, i) => Math.round(n + (rgb[i] - n) * w));
}
