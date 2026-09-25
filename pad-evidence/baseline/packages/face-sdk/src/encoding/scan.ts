import { ENGINE_MAX_BYTES, FRAME_COUNT, JPEG_STEPS, TARGET_BYTES } from '../constants.ts';
import { codedError } from '../types.ts';
import type { Challenge, DeviceInfo, EncodedFrame } from '../types.ts';
import { bytesToB64, mpEncode, type MsgpackValue } from './msgpack.ts';

export type ScanChallenge = Pick<Challenge, 'nonce' | 'action' | 'params'>;

export function buildFaceScan(
  frames: readonly EncodedFrame[],
  device: DeviceInfo,
  challengeId: string,
  challenge?: ScanChallenge | null,
): MsgpackValue {
  if (frames.length !== FRAME_COUNT) throw new RangeError(`a FaceScan has ${FRAME_COUNT} frames, got ${frames.length}`);
  let last = -Infinity;
  for (const f of frames) {
    if (f.bytes.length < 4 || f.bytes[0] !== 0xff || f.bytes[1] !== 0xd8) throw new TypeError('frame is not a JPEG');
    if (!Number.isFinite(f.ts) || f.ts < last) throw new RangeError('frame timestamps must be finite and ordered');
    last = f.ts;
  }
  const ch: Record<string, MsgpackValue> = { id: challengeId, results: [] };
  if (challenge?.nonce) {
    ch.nonce = challenge.nonce;
    ch.action = challenge.action;
    if (challenge.params) ch.params = challenge.params;
  }
  return {
    version: 1,
    device: {
      user_agent: device.user_agent,
      screen: { w: device.screen.w, h: device.screen.h },
      tz_offset: device.tz_offset,
    },
    frames: frames.map((f) => ({ jpeg_bytes: f.bytes, ts_ms: Math.round(f.ts), pose: null })),
    challenge: ch,
  };
}

export interface PackedScan {
  readonly b64: string;
  readonly size: number;
  readonly quality: number;
  readonly longEdge: number;
}

export function packScan(
  encodeAt: (quality: number, longEdge: number, step: number) => readonly EncodedFrame[],
  device: DeviceInfo,
  challengeId: string,
  challenge: ScanChallenge | null,
): PackedScan {
  let size = 0;
  for (let i = 0; i < JPEG_STEPS.length; i++) {
    const [quality, longEdge] = JPEG_STEPS[i]!;
    const bytes = mpEncode(buildFaceScan(encodeAt(quality, longEdge, i), device, challengeId, challenge));
    size = bytes.length;
    const fits = size <= TARGET_BYTES || (i === JPEG_STEPS.length - 1 && size <= ENGINE_MAX_BYTES);
    if (fits) return { b64: bytesToB64(bytes), size, quality, longEdge };
  }
  throw codedError('SCAN_TOO_LARGE',
    `scan is ${size} bytes at the lowest ladder step; the engine takes ${ENGINE_MAX_BYTES}`);
}
