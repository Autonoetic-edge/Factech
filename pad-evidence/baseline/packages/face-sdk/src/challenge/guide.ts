import { DEFAULT_SETTLE_MS } from '../constants.ts';
import type { Challenge, Instruction } from '../types.ts';

type Params = Pick<Challenge, 'params'> | null | undefined;

export function settleMsOf(ch: Params): number {
  const v = ch?.params?.settle_ms;
  return typeof v === 'number' && v > 0 ? v : DEFAULT_SETTLE_MS;
}
export function targetOf(ch: Params): number | null {
  const t = ch?.params?.target;
  return typeof t === 'number' && t > 0 ? t : null;
}

export const TARGET_MAX = 0.345;

export const OVAL_END = 1.35, OVAL_MARGIN = 1.10;
export const OVAL_START = OVAL_END / (Math.exp(TARGET_MAX) * OVAL_MARGIN);
export function ovalPlan(ch: Params): { from: number; to: number } {
  const t = targetOf(ch);
  if (t === null) return { from: OVAL_START, to: OVAL_START * 1.3 };
  return { from: OVAL_START, to: OVAL_START * Math.exp(t) * OVAL_MARGIN };
}

export function instructionOf(ch: Challenge): Instruction {
  return { action: ch.action, settleMs: settleMsOf(ch), target: targetOf(ch), oval: ovalPlan(ch) };
}

export const GUIDE_CAL = 1.15;
export const FACE_FAR = 0.35, FACE_TOO_CLOSE = 0.92;

export const START_MAX = FACE_TOO_CLOSE / Math.exp(TARGET_MAX * GUIDE_CAL);

export function median(xs: readonly number[]): number | null {
  const s = [...xs].sort((a, b) => a - b), n = s.length;
  if (!n) return null;
  return n % 2 ? s[(n - 1) / 2]! : (s[n / 2 - 1]! + s[n / 2]!) / 2;
}

export interface Guide {
  readonly settle: number;
  readonly target: number | null;
  readonly hold: number[];
  baseline: number | null;
  done: boolean;
  phase: 'hold' | 'move' | 'done';
  progress: number | null;
}
export function createGuide(ch: Params): Guide {
  return { settle: settleMsOf(ch), target: targetOf(ch), hold: [], baseline: null, done: false, phase: 'hold', progress: null };
}

export function guideStep(G: Guide, elapsed: number, faceH: number | null | undefined): Guide {
  const h = typeof faceH === 'number' && faceH > 0 ? faceH : null;
  if (elapsed < G.settle) {
    if (h !== null) G.hold.push(h);
    G.phase = 'hold'; G.progress = null;
    return G;
  }
  if (G.baseline === null && G.hold.length) G.baseline = median(G.hold);
  G.progress = G.baseline && h !== null && G.target ? Math.log(h / G.baseline) / (G.target * GUIDE_CAL) : null;
  if (G.progress !== null && G.progress >= 1) G.done = true;
  G.phase = G.done ? 'done' : 'move';
  return G;
}
