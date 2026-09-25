import { CAPTURE_MS, FRAME_COUNT, GUIDE_TICK_MS, JPEG_STEPS, LONG_EDGE } from '../constants.ts';
import { codedError } from '../types.ts';
import type { EncodedFrame, Environment, FailureCode } from '../types.ts';
import { b64ToBytes } from '../encoding/msgpack.ts';

export interface RawFrame {
  readonly canvas: HTMLCanvasElement;
  readonly ts: number;
  jpeg0: Uint8Array | null;
}

export interface CaptureDeps {
  readonly intervalMs?: number | undefined;
  readonly env: Environment;
  readonly video: HTMLVideoElement;

  readonly onFrame: (index: number, total: number) => void;

  readonly onTick: (elapsedMs: number) => void;

  readonly shouldAbort: () => FailureCode | null;
}

export const CAPTURE_SPAN_MS = (FRAME_COUNT - 1) * CAPTURE_MS;

export async function captureFrames(deps: CaptureDeps): Promise<RawFrame[]> {
  const { env, video } = deps;
  const frames: RawFrame[] = [];

  const scratch = env.createCanvas();
  let firstTs = 0;
  try {
    for (let i = 0; i < FRAME_COUNT; i++) {
      abortIfAsked(deps);
      const t0 = env.now();
      frames.push(grabFrame(env, video, scratch));
      if (i === 0) firstTs = frames[0]!.ts;
      deps.onFrame(i + 1, FRAME_COUNT);
      deps.onTick(env.now() - firstTs);

      const [q0, edge0] = JPEG_STEPS[0]!;
      try { frames[i]!.jpeg0 = jpegOf(env, frames[i]!.canvas, q0, edge0); } catch {                        }

      if (i === FRAME_COUNT - 1) break;
      await waitOutGap(deps, firstTs, t0);
    }
  } catch (e) {
    releaseFrames(frames);
    throw e;
  } finally {
    releaseCanvas(scratch);
  }
  return frames;
}

function abortIfAsked(deps: CaptureDeps): void {
  const code = deps.shouldAbort();
  if (code) throw codedError(code, 'capture stopped: ' + code);
}

async function waitOutGap(deps: CaptureDeps, firstTs: number, frameStart: number): Promise<void> {
  const { env } = deps;
  const until = frameStart + (deps.intervalMs ?? CAPTURE_MS);
  for (;;) {
    const left = until - env.now();
    if (left <= 0) return;
    await new Promise<void>((resolve) => { env.setTimeout(resolve, Math.min(left, GUIDE_TICK_MS)); });
    abortIfAsked(deps);
    deps.onTick(env.now() - firstTs);
  }
}

function grabFrame(env: Environment, video: HTMLVideoElement, scratch: HTMLCanvasElement): RawFrame {
  const vw = video.videoWidth || 640, vh = video.videoHeight || 480;
  const scale = Math.min(1, LONG_EDGE / Math.max(vw, vh));
  const w = Math.max(2, Math.round(vw * scale)), h = Math.max(2, Math.round(vh * scale));
  scratch.width = w; scratch.height = h;
  draw2d(scratch).drawImage(video, 0, 0, w, h);
  const keeper = env.createCanvas();
  keeper.width = w; keeper.height = h;
  draw2d(keeper).drawImage(scratch, 0, 0);
  return { canvas: keeper, ts: env.now(), jpeg0: null };
}

function draw2d(cv: HTMLCanvasElement): CanvasRenderingContext2D {
  const ctx = cv.getContext('2d');

  if (!ctx) throw codedError('CAMERA_UNAVAILABLE', 'no 2d canvas context');
  return ctx;
}

export function jpegOf(
  env: Environment,
  source: HTMLCanvasElement,
  quality: number,
  longEdge: number,
): Uint8Array {
  let cv = source;
  let scaled: HTMLCanvasElement | null = null;
  if (Math.max(cv.width, cv.height) > longEdge) {
    const s = longEdge / Math.max(cv.width, cv.height);
    scaled = env.createCanvas();
    scaled.width = Math.max(2, Math.round(cv.width * s));
    scaled.height = Math.max(2, Math.round(cv.height * s));
    draw2d(scaled).drawImage(cv, 0, 0, scaled.width, scaled.height);
    cv = scaled;
  }
  try {
    const url = cv.toDataURL('image/jpeg', quality);
    const comma = url.indexOf(',');
    if (comma < 0) throw codedError('CAMERA_UNAVAILABLE', 'canvas produced no JPEG');
    return b64ToBytes(url.slice(comma + 1));
  } finally {
    if (scaled) releaseCanvas(scaled);
  }
}

export function encodeFrames(
  env: Environment,
  frames: readonly RawFrame[],
  quality: number,
  longEdge: number,
  usePre: boolean,
): EncodedFrame[] {
  return frames.map((f) => (usePre && f.jpeg0
    ? { bytes: f.jpeg0, ts: f.ts }
    : { bytes: jpegOf(env, f.canvas, quality, longEdge), ts: f.ts }));
}

function releaseCanvas(cv: HTMLCanvasElement): void {
  try { cv.width = 0; cv.height = 0; } catch {                                    }
}

export function releaseFrames(frames: readonly RawFrame[]): void {
  for (const f of frames) { f.jpeg0 = null; releaseCanvas(f.canvas); }
}
