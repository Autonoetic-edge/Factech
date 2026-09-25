import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ENGINE_MAX_BYTES, FRAME_COUNT, JPEG_STEPS, TARGET_BYTES } from '../src/constants.ts';
import { buildFaceScan, packScan } from '../src/encoding/scan.ts';
import { mpEncode } from '../src/encoding/msgpack.ts';
import { isCodedError } from '../src/types.ts';
import type { EncodedFrame } from '../src/types.ts';
import { TEST_DEVICE, mpDecode } from './helpers.ts';

const jpeg = (n: number, fill = 0): Uint8Array => {
  const b = new Uint8Array(Math.max(4, n));
  b[0] = 0xff; b[1] = 0xd8; b[2] = fill; b[b.length - 1] = 0xd9;
  return b;
};
const frames = (n = FRAME_COUNT, bytes = 100): EncodedFrame[] =>
  Array.from({ length: n }, (_, i) => ({ bytes: jpeg(bytes, i), ts: 1000 + i * 500 }));

test('a FaceScan has exactly 12 frames', () => {
  assert.throws(() => buildFaceScan(frames(11), TEST_DEVICE, 'id'), RangeError);
  assert.throws(() => buildFaceScan(frames(13), TEST_DEVICE, 'id'), RangeError);
  assert.ok(buildFaceScan(frames(12), TEST_DEVICE, 'id'));
});

test('a frame that is not a JPEG is refused before it can become a MALFORMED_SCAN', () => {
  const bad = frames();
  bad[3] = { bytes: Uint8Array.from([0x89, 0x50, 0x4e, 0x47]), ts: 2500 };
  assert.throws(() => buildFaceScan(bad, TEST_DEVICE, 'id'), /not a JPEG/);
});

test('timestamps must be finite and ordered (contract 1.1: strictly ascending)', () => {
  const back = frames();
  back[5] = { bytes: jpeg(100), ts: 0 };
  assert.throws(() => buildFaceScan(back, TEST_DEVICE, 'id'), RangeError);
  const nan = frames();
  nan[2] = { bytes: jpeg(100), ts: Number.NaN };
  assert.throws(() => buildFaceScan(nan, TEST_DEVICE, 'id'), RangeError);
});

test('ts_ms is rounded to an integer, and pose is null (the SDK estimates none)', () => {
  const f = frames().map((x, i) => ({ ...x, ts: 1000.6 + i }));
  const scan = mpDecode(mpEncode(buildFaceScan(f, TEST_DEVICE, 'id'))) as {
    frames: { ts_ms: number; pose: null }[];
  };
  assert.equal(scan.frames[0]!.ts_ms, 1001);
  assert.equal(scan.frames[0]!.pose, null);
});

test('the challenge map carries nonce, action and params verbatim', () => {
  const params = { settle_ms: 1450, target: 0.319 };
  const scan = mpDecode(mpEncode(buildFaceScan(frames(), TEST_DEVICE, 'cid', {
    nonce: 'b'.repeat(32), action: 'MOVE_CLOSER', params,
  }))) as { challenge: Record<string, unknown> };
  assert.deepEqual(scan.challenge, {
    id: 'cid', results: [], nonce: 'b'.repeat(32), action: 'MOVE_CLOSER', params,
  });
});

test('a challenge with no nonce contributes nothing: there is no half-challenge shape', () => {
  const scan = mpDecode(mpEncode(buildFaceScan(frames(), TEST_DEVICE, 'cid', {
    nonce: '', action: 'MOVE_CLOSER', params: { settle_ms: 900, target: 0.22 },
  }))) as { challenge: Record<string, unknown> };
  assert.deepEqual(Object.keys(scan.challenge), ['id', 'results']);
});

function ladder(perFrame: (quality: number) => number) {
  const steps: { quality: number; longEdge: number; step: number }[] = [];
  const packed = packScan((quality, longEdge, step) => {
    steps.push({ quality, longEdge, step });
    return frames(FRAME_COUNT, perFrame(quality));
  }, TEST_DEVICE, 'cid', null);
  return { packed, steps };
}

test('a small scan is packed at step 0 and the ladder stops there', () => {
  const { packed, steps } = ladder(() => 8000);
  assert.equal(steps.length, 1);
  assert.equal(steps[0]!.step, 0);
  assert.equal(packed.quality, JPEG_STEPS[0]![0]);
  assert.ok(packed.size <= TARGET_BYTES);
  assert.equal(Buffer.from(packed.b64, 'base64').length, packed.size);
});

test('the ladder walks down until the scan fits the 330 KB target', () => {

  const { packed, steps } = ladder((q) => Math.round(40_000 * q / JPEG_STEPS[0]![0]));
  assert.ok(steps.length > 1, 'it should have taken more than one step');
  assert.ok(packed.size <= TARGET_BYTES);
  assert.equal(packed.quality, JPEG_STEPS[steps.length - 1]![0]);
  assert.deepEqual(steps.map((s) => s.step), steps.map((_, i) => i));
});

test('step 0 is the only step allowed to reuse pre-encoded bytes', () => {
  const { steps } = ladder((q) => Math.round(40_000 * q / JPEG_STEPS[0]![0]));

  assert.deepEqual(steps.filter((s) => s.step === 0).length, 1);
});

test('a scan past the target but under the engine limit at the last step is still sent', () => {
  const between = Math.floor((TARGET_BYTES + ENGINE_MAX_BYTES) / 2 / FRAME_COUNT);
  const { packed } = ladder(() => between);
  assert.ok(packed.size > TARGET_BYTES && packed.size <= ENGINE_MAX_BYTES, 'size was ' + packed.size);
});

test('a scan over the engine limit is SCAN_TOO_LARGE and is never sent', () => {
  try {
    ladder(() => 60_000);
    assert.fail('the ladder should have refused');
  } catch (e) {
    assert.ok(isCodedError(e));
    assert.equal(e.sdkCode, 'SCAN_TOO_LARGE');

    assert.match(e.message, /^scan is \d+ bytes/);
    assert.doesNotMatch(e.message, /[A-Za-z0-9+/]{40}/);
  }
});
