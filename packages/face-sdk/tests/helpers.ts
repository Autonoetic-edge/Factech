import { readFileSync } from 'node:fs';
import type { DeviceInfo, Environment } from '../src/types.ts';

export interface FakeClock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;

  skip(ms: number): void;
  readonly pending: number;
}

export function fakeClock(start = 10_000): FakeClock {
  let t = start;
  let seq = 0;
  let pumping = false;
  const timers = new Map<number, { due: number; fn: () => void }>();

  function pump(): void {
    if (pumping) return;
    pumping = true;
    setImmediate(() => {
      pumping = false;
      let bestId = -1;
      let best: { due: number; fn: () => void } | undefined;
      for (const [id, timer] of timers) {
        if (!best || timer.due < best.due || (timer.due === best.due && id < bestId)) { best = timer; bestId = id; }
      }
      if (!best) return;
      timers.delete(bestId);
      if (best.due > t) t = best.due;
      best.fn();
      if (timers.size) pump();
    });
  }

  return {
    now: () => t,
    setTimeout(fn, ms) {
      const id = ++seq;
      timers.set(id, { due: t + Math.max(0, ms), fn });
      pump();
      return id;
    },
    clearTimeout(id) { timers.delete(id as number); },
    skip(ms) { t += ms; },
    get pending() { return timers.size; },
  };
}

export interface FakeCanvas {
  width: number;
  height: number;

  readonly released: boolean;
  readonly draws: number;
}

export interface CanvasFactory {
  (): HTMLCanvasElement;
  readonly all: FakeCanvas[];

  bytesAt: (quality: number, w: number, h: number) => number;
}

export function canvasFactory(bytesAt?: (quality: number, w: number, h: number) => number): CanvasFactory {
  const all: FakeCanvas[] = [];
  const factory = (): HTMLCanvasElement => {
    const cv = {
      width: 0,
      height: 0,
      draws: 0,
      get released(): boolean { return cv.width === 0 && cv.height === 0 && cv.draws > 0; },
      getContext(): unknown {
        return { drawImage(): void { cv.draws++; } };
      },
      toDataURL(_type: string, quality: number): string {
        const n = Math.max(4, factory.bytesAt(quality, cv.width, cv.height));
        const bytes = new Uint8Array(n);
        bytes[0] = 0xff; bytes[1] = 0xd8; bytes[n - 2] = 0xff; bytes[n - 1] = 0xd9;
        return 'data:image/jpeg;base64,' + Buffer.from(bytes).toString('base64');
      },
    };
    all.push(cv as unknown as FakeCanvas);
    return cv as unknown as HTMLCanvasElement;
  };
  factory.all = all;
  factory.bytesAt = bytesAt ?? ((quality: number) => Math.round(4000 * quality));
  return factory as CanvasFactory;
}

export interface FakeVideo {
  videoWidth: number;
  videoHeight: number;
  srcObject: unknown;
  readonly plays: number;
  playRejects: boolean;
}

export function fakeVideo(width = 640, height = 480): FakeVideo & HTMLVideoElement {
  const v = {
    videoWidth: width,
    videoHeight: height,
    srcObject: null as unknown,
    plays: 0,
    playRejects: false,
    play(): Promise<void> {
      v.plays++;
      return v.playRejects ? Promise.reject(new Error('NotAllowedError')) : Promise.resolve();
    },
  };
  return v as unknown as FakeVideo & HTMLVideoElement;
}

export interface FakeTrack {
  readonly stopped: boolean;

  end(): void;
}
export interface FakeStream {
  readonly tracks: FakeTrack[];
  readonly allStopped: boolean;
  readonly liveCount: number;
}

export function fakeStream(count = 1): FakeStream & MediaStream {
  const tracks = Array.from({ length: count }, () => {
    const listeners = new Set<() => void>();
    const track = {
      stopped: false,
      stop(): void { track.stopped = true; },
      addEventListener(_type: string, fn: () => void): void { listeners.add(fn); },
      removeEventListener(_type: string, fn: () => void): void { listeners.delete(fn); },
      end(): void { for (const fn of [...listeners]) fn(); },
    };
    return track;
  });
  const stream = {
    tracks,
    getTracks: () => tracks,
    get allStopped(): boolean { return tracks.every((t) => t.stopped); },
    get liveCount(): number { return tracks.filter((t) => !t.stopped).length; },
  };
  return stream as unknown as FakeStream & MediaStream;
}

export interface FakeReply {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;

  delayMs?: number;

  networkError?: boolean;

  notJson?: boolean;

  bodyDelayMs?: number;

  bodyError?: boolean;

  onBody?: () => void;
}

export interface FakeFetch {
  (input: string, init?: RequestInit): Promise<Response>;

  reply(path: string, reply: FakeReply): void;
  readonly calls: { url: string; init: RequestInit | undefined }[];
}

export function fakeFetch(clock: FakeClock): FakeFetch {
  const replies = new Map<string, FakeReply>();
  const calls: { url: string; init: RequestInit | undefined }[] = [];

  const fn = (input: string, init?: RequestInit): Promise<Response> => {
    calls.push({ url: input, init });
    const path = input.replace(/^[a-z]+:\/\/[^/]+/, '');
    const spec: FakeReply = replies.get(path) ?? replies.get(path.split('?')[0]!) ?? { status: 404, body: {} };
    const signal = init?.signal;
    return new Promise<Response>((resolve, reject) => {
      const abort = (): void => {
        clock.clearTimeout(timer);
        reject(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }));
      };
      const timer = clock.setTimeout(() => {
        signal?.removeEventListener('abort', abort);
        if (spec.networkError) { reject(Object.assign(new Error('failed'), { name: 'TypeError' })); return; }
        resolve({
          status: spec.status ?? 200,
          headers: new Headers(spec.headers ?? {}),
          json: () => {
            spec.onBody?.();
            if (spec.notJson) return Promise.reject(new SyntaxError('not json'));
            if (spec.bodyError) return Promise.reject(new TypeError('network error'));
            if (spec.bodyDelayMs === undefined) return Promise.resolve(spec.body ?? {});
            return new Promise((resolveBody, rejectBody) => {
              const abortBody = (): void => {
                clock.clearTimeout(bodyTimer);
                rejectBody(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }));
              };
              const bodyTimer = clock.setTimeout(() => {
                signal?.removeEventListener('abort', abortBody);
                resolveBody(spec.body ?? {});
              }, spec.bodyDelayMs ?? 0);
              signal?.addEventListener('abort', abortBody, { once: true });
              if (signal?.aborted) abortBody();
            });
          },
        } as unknown as Response);
      }, spec.delayMs ?? 0);
      signal?.addEventListener('abort', abort, { once: true });
      if (signal?.aborted) abort();
    });
  };
  fn.reply = (path: string, reply: FakeReply): void => { replies.set(path, reply); };
  fn.calls = calls;
  return fn as FakeFetch;
}

export const TEST_DEVICE: DeviceInfo = {
  user_agent: 'facetech-tests',
  screen: { w: 390, h: 844 },
  tz_offset: -420,
};

export interface TestEnv {
  readonly env: Environment;
  readonly clock: FakeClock;
  readonly fetch: FakeFetch;
  readonly canvases: CanvasFactory;
  readonly video: FakeVideo & HTMLVideoElement;

  readonly streams: (FakeStream & MediaStream)[];

  onGetUserMedia(fn: () => Promise<MediaStream>): void;
  readonly doc: FakeDocument;
  readonly win: FakeWindow;
}

export interface FakeDocument {
  hidden: boolean;
  fire(type: string): void;
  addEventListener(type: string, fn: () => void): void;
  removeEventListener(type: string, fn: () => void): void;
  readonly listenerCount: number;
}
export interface FakeWindow extends Omit<FakeDocument, 'hidden'> { hidden?: never }

function fakeEvents(): FakeDocument {
  const listeners = new Map<string, Set<() => void>>();
  return {
    hidden: false,
    addEventListener(type, fn) {
      const set = listeners.get(type);
      if (set) set.add(fn); else listeners.set(type, new Set([fn]));
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    fire(type) { for (const fn of [...(listeners.get(type) ?? [])]) fn(); },
    get listenerCount() {
      let n = 0;
      for (const set of listeners.values()) n += set.size;
      return n;
    },
  };
}

export function testEnv(options: { canvasBytesAt?: (q: number, w: number, h: number) => number } = {}): TestEnv {
  const clock = fakeClock();
  const fetchFn = fakeFetch(clock);
  const canvases = canvasFactory(options.canvasBytesAt);
  const video = fakeVideo();
  const streams: (FakeStream & MediaStream)[] = [];
  const doc = fakeEvents();
  const win = fakeEvents() as unknown as FakeWindow;
  let gum: (() => Promise<MediaStream>) | null = null;

  const env: Environment = {
    now: () => clock.now(),
    setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
    clearTimeout: (id) => clock.clearTimeout(id),
    fetch: fetchFn as unknown as typeof fetch,
    getUserMedia: () => {
      if (gum) return gum();
      const s = fakeStream();
      streams.push(s);
      return Promise.resolve(s);
    },
    createCanvas: canvases,
    deviceInfo: () => TEST_DEVICE,
    randomId: () => 'ch-test-1',
    document: doc as unknown as Document,
    window: win as unknown as Window,
  };

  return {
    env, clock, fetch: fetchFn, canvases, video, streams, doc,
    win: win as FakeWindow,
    onGetUserMedia(fn) { gum = fn; },
  };
}

const LEGACY_FIXTURE = new URL('./fixtures/legacy-sdk-block.js', import.meta.url);

export interface LegacySdk {
  mpEncode(value: unknown): Uint8Array;
  buildFaceScan(frames: unknown, device: unknown, challengeId: string, challenge?: unknown): unknown;
  bytesToB64(u8: Uint8Array): string;
  b64ToBytes(b64: string): Uint8Array;
}

export function legacyFixture(): { recorded: string; body: string } {
  const body = readFileSync(LEGACY_FIXTURE, 'utf8');
  const checksum = new URL('./fixtures/legacy-sdk-block.sha256', import.meta.url);
  const recorded = readFileSync(checksum, 'utf8').trim();
  if (!/^[0-9a-f]{64}$/.test(recorded)) throw new Error('invalid fixture checksum');
  return { recorded, body };
}

export function legacySdk(): LegacySdk {
  const factory = new Function(
    legacyFixture().body + '\nreturn {mpEncode, buildFaceScan, bytesToB64, b64ToBytes};',
  ) as () => LegacySdk;
  return factory();
}

export function mpDecode(bytes: Uint8Array): unknown {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let i = 0;
  const utf8 = new TextDecoder('utf-8', { fatal: true });

  function value(): unknown {
    const b = bytes[i++]!;
    if (b <= 0x7f) return b;
    if (b >= 0xe0) return b - 256;
    if ((b & 0xf0) === 0x80) return map(b & 0x0f);
    if ((b & 0xf0) === 0x90) return array(b & 0x0f);
    if ((b & 0xe0) === 0xa0) return str(b & 0x1f);
    switch (b) {
      case 0xc0: return null;
      case 0xc2: return false;
      case 0xc3: return true;
      case 0xc4: return bin(u8());
      case 0xc5: return bin(u16());
      case 0xc6: return bin(u32());
      case 0xcb: { const v = view.getFloat64(i); i += 8; return v; }
      case 0xcc: return u8();
      case 0xcd: return u16();
      case 0xce: return u32();
      case 0xcf: { const v = view.getBigUint64(i); i += 8; return Number(v); }
      case 0xd0: { const v = view.getInt8(i); i += 1; return v; }
      case 0xd1: { const v = view.getInt16(i); i += 2; return v; }
      case 0xd2: { const v = view.getInt32(i); i += 4; return v; }
      case 0xd3: { const v = view.getBigInt64(i); i += 8; return Number(v); }
      case 0xd9: return str(u8());
      case 0xda: return str(u16());
      case 0xdb: return str(u32());
      case 0xdc: return array(u16());
      case 0xdd: return array(u32());
      case 0xde: return map(u16());
      case 0xdf: return map(u32());
      default: throw new Error('mpDecode: unsupported byte 0x' + b.toString(16));
    }
  }
  const u8 = (): number => bytes[i++]!;
  const u16 = (): number => { const v = view.getUint16(i); i += 2; return v; };
  const u32 = (): number => { const v = view.getUint32(i); i += 4; return v; };
  const str = (n: number): string => { const s = utf8.decode(bytes.subarray(i, i + n)); i += n; return s; };
  const bin = (n: number): Uint8Array => { const s = bytes.slice(i, i + n); i += n; return s; };
  function array(n: number): unknown[] {
    const out: unknown[] = [];
    for (let k = 0; k < n; k++) out.push(value());
    return out;
  }
  function map(n: number): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (let k = 0; k < n; k++) {
      const key = value();
      if (typeof key !== 'string') throw new Error('mpDecode: non-string map key');
      out[key] = value();
    }
    return out;
  }
  const v = value();
  if (i !== bytes.length) throw new Error('mpDecode: ' + (bytes.length - i) + ' trailing bytes');
  return v;
}

export function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
