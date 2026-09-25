import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { b64ToBytes, bytesToB64, mpEncode } from '../src/encoding/msgpack.ts';
import { buildFaceScan } from '../src/encoding/scan.ts';
import { legacyFixture, legacySdk, mpDecode, rng } from './helpers.ts';

const GOLDEN = new URL('../../../engine/tests/fixtures/app_encoder_golden.txt', import.meta.url);
const legacy = legacySdk();

const hex = (u8: Uint8Array): string => Buffer.from(u8).toString('hex');

test('the frozen legacy encoder is byte-for-byte the block it was copied from', () => {

  const { recorded, body } = legacyFixture();
  assert.equal(createHash('sha256').update(body, 'utf8').digest('hex'), recorded);
  assert.equal(body.includes('\r'), false, 'LF only, as committed');
  assert.match(body, /function mpEncode\(/);
});

test('the golden FaceScan re-encodes to itself, byte for byte', () => {
  const golden = b64ToBytes(readFileSync(GOLDEN, 'utf8').trim());
  const decoded = mpDecode(golden) as {
    version: number;
    device: { user_agent: string; screen: { w: number; h: number }; tz_offset: number };
    frames: { jpeg_bytes: Uint8Array; ts_ms: number; pose: null }[];
    challenge: { id: string; results: unknown[] };
  };

  assert.equal(decoded.version, 1);
  assert.equal(decoded.frames.length, 12);
  assert.equal(decoded.challenge.id, 'ch-app-1');
  assert.deepEqual(decoded.challenge.results, []);
  assert.equal(decoded.device.tz_offset, -420);
  assert.ok(decoded.frames[0]!.jpeg_bytes[0] === 0xff && decoded.frames[0]!.jpeg_bytes[1] === 0xd8);

  const rebuilt = mpEncode(buildFaceScan(
    decoded.frames.map((f) => ({ bytes: f.jpeg_bytes, ts: f.ts_ms })),
    decoded.device,
    decoded.challenge.id,
    null,
  ));
  assert.equal(hex(rebuilt), hex(golden));
});

test('base64 round-trips the golden fixture unchanged', () => {
  const text = readFileSync(GOLDEN, 'utf8').trim();
  assert.equal(bytesToB64(b64ToBytes(text)), text);
});

interface FixedCase { name: string; value: unknown }

const FIXED: FixedCase[] = [
  { name: 'nil', value: null },
  { name: 'booleans', value: [true, false] },
  { name: 'positive fixint edges', value: [0, 1, 127] },
  { name: 'uint8 edges', value: [128, 255] },
  { name: 'uint16 edges', value: [256, 65535] },
  { name: 'uint32 edges', value: [65536, 4294967295] },
  { name: 'uint64 range', value: [4294967296, Number.MAX_SAFE_INTEGER] },
  { name: 'negative fixint edges', value: [-1, -32] },
  { name: 'int8 edges', value: [-33, -128] },
  { name: 'int16 edges', value: [-129, -32768] },
  { name: 'int32 edges', value: [-32769, -2147483648] },
  { name: 'floats', value: [0.5, -0.5, 1e-9, 1e21, 0.1 + 0.2] },
  { name: 'negative zero', value: -0 },
  { name: 'empty containers', value: { a: [], b: {} } },
  { name: 'fixstr edges', value: ['', 'x'.repeat(31)] },
  { name: 'str8 edges', value: ['y'.repeat(32), 'y'.repeat(255)] },
  { name: 'str16 edges', value: ['z'.repeat(256), 'z'.repeat(65535)] },
  { name: 'multibyte string lengths are bytes, not code points', value: ['é'.repeat(16), '🙂'.repeat(8)] },
  { name: 'bin8 edges', value: [new Uint8Array(0), new Uint8Array(255)] },
  { name: 'bin16 edges', value: [new Uint8Array(256), new Uint8Array(65535)] },
  { name: 'bin32', value: new Uint8Array(70000) },
  { name: 'fixarray edge', value: Array.from({ length: 15 }, (_, i) => i) },
  { name: 'array16 edge', value: Array.from({ length: 16 }, (_, i) => i) },
  { name: 'fixmap edge', value: Object.fromEntries(Array.from({ length: 15 }, (_, i) => ['k' + i, i])) },
  { name: 'map16 edge', value: Object.fromEntries(Array.from({ length: 16 }, (_, i) => ['k' + i, i])) },
  { name: 'nested', value: { a: [{ b: [1, null, true, new Uint8Array([1, 2])] }], c: { d: {} } } },
  { name: 'undefined encodes as nil', value: { a: undefined } },
];

for (const c of FIXED) {
  test('parity with the shipped block: ' + c.name, () => {
    assert.equal(hex(mpEncode(c.value as never)), hex(legacy.mpEncode(c.value)));
  });
}

function randomValue(rand: () => number, depth: number): unknown {
  const kinds = depth > 2 ? 6 : 9;
  switch (Math.floor(rand() * kinds)) {
    case 0: return null;
    case 1: return rand() < 0.5;
    case 2: return Math.floor(rand() * 4294967296) - 2147483648;
    case 3: return (rand() - 0.5) * 1e6;
    case 4: return 'sß🙂'.repeat(Math.floor(rand() * 40));
    case 5: return new Uint8Array(Math.floor(rand() * 600)).fill(Math.floor(rand() * 256));
    case 6: return Array.from({ length: Math.floor(rand() * 12) }, () => randomValue(rand, depth + 1));
    case 7: return Object.fromEntries(Array.from({ length: Math.floor(rand() * 10) },
      (_, i) => ['k' + i, randomValue(rand, depth + 1)]));
    default: return Math.floor(rand() * 200) - 100;
  }
}

test('parity with the shipped block: 400 randomised values', () => {
  for (let seed = 1; seed <= 400; seed++) {
    const rand = rng(seed);
    const value = randomValue(rand, 0);
    assert.equal(hex(mpEncode(value as never)), hex(legacy.mpEncode(value)), 'seed ' + seed);
  }
});

test('parity with the shipped block: a whole FaceScan, with and without a challenge', () => {
  const frames = Array.from({ length: 12 }, (_, i) => ({
    bytes: Uint8Array.from([0xff, 0xd8, ...Array.from({ length: 300 + i }, (_, k) => (i * 7 + k) & 255)]),
    ts: 1000 + i * 500,
  }));
  const device = { user_agent: 'facetech-tests/1.0 (é)', screen: { w: 390, h: 844 }, tz_offset: -420 };
  const challenge = {
    nonce: 'a'.repeat(32), action: 'MOVE_CLOSER' as const,
    params: { settle_ms: 1450, target: 0.319 },
  };
  for (const ch of [null, challenge]) {
    const mine = mpEncode(buildFaceScan(frames, device, 'ch-parity', ch));
    const theirs = legacy.mpEncode(legacy.buildFaceScan(frames, device, 'ch-parity', ch));
    assert.equal(hex(mine), hex(theirs), ch ? 'with a challenge' : 'tier-1 shape');
  }
});

test('a scan built with no challenge keeps the pre-S6 shape the golden pins', () => {
  const frames = Array.from({ length: 12 }, (_, i) => ({ bytes: Uint8Array.from([0xff, 0xd8, i, 0xd9]), ts: i }));
  const device = { user_agent: 'u', screen: { w: 1, h: 2 }, tz_offset: 0 };
  const scan = buildFaceScan(frames, device, 'id') as { challenge: Record<string, unknown> };
  assert.deepEqual(Object.keys(scan.challenge), ['id', 'results']);
});

test('a string over 65535 bytes gets a str32 header, where the old block truncated the length', () => {
  const s = 'a'.repeat(70000);
  const mine = mpEncode(s);
  assert.equal(mine[0], 0xdb, 'str32');
  assert.equal(new DataView(mine.buffer, mine.byteOffset).getUint32(1), 70000);

  const old = legacy.mpEncode(s);
  assert.equal(old[0], 0xda);
  assert.notEqual(hex(old), hex(mine));
});

test('an array over 65535 entries gets an array32 header', () => {
  const a = new Array(70000).fill(0);
  const mine = mpEncode(a);
  assert.equal(mine[0], 0xdd);
  assert.equal(new DataView(mine.buffer, mine.byteOffset).getUint32(1), 70000);
  assert.equal(legacy.mpEncode(a)[0], 0xdc, 'the old block emitted array16 and a truncated length');
});

test('a map over 65535 keys gets a map32 header', () => {
  const m: Record<string, number> = {};
  for (let i = 0; i < 70000; i++) m['k' + i] = 0;
  const mine = mpEncode(m);
  assert.equal(mine[0], 0xdf);
  assert.equal(new DataView(mine.buffer, mine.byteOffset).getUint32(1), 70000);
  assert.equal(legacy.mpEncode(m)[0], 0xde, 'the old block emitted map16 and a truncated length');
});

test('an int below -2^31 gets an int64, where the old block wrapped it', () => {
  const v = -2147483649;
  const mine = mpEncode(v);
  assert.equal(mine[0], 0xd3);
  assert.equal(new DataView(mine.buffer, mine.byteOffset).getBigInt64(1), BigInt(v));
  const old = legacy.mpEncode(v);
  assert.equal(old[0], 0xd2);

  assert.equal(new DataView(old.buffer, old.byteOffset).getInt32(1), 2147483647);
});

test('int64 and uint64 round-trip through the decoder at full width', () => {
  for (const v of [-2147483649, -Number.MAX_SAFE_INTEGER, 4294967296, Number.MAX_SAFE_INTEGER]) {
    assert.equal(mpDecode(mpEncode(v)), v);
  }
});

test('values that are not data are refused instead of silently becoming an empty map', () => {
  const cases: [string, unknown][] = [
    ['a Date', new Date()],
    ['a Map', new Map()],
    ['a class instance', new (class Thing { x = 1 })()],
    ['a function', () => 1],
    ['a symbol', Symbol('s')],
    ['a bigint', 1n],
    ['an ArrayBuffer', new ArrayBuffer(4)],
    ['a typed array that is not Uint8Array', new Float32Array(2)],
  ];
  for (const [what, value] of cases) {
    assert.throws(() => mpEncode(value as never), TypeError, what + ' should be refused');
  }

  assert.equal(hex(legacy.mpEncode(new Date())), hex(legacy.mpEncode({})));
});

test('a cycle is refused as a cycle, not followed until the stack blows', () => {
  const a: Record<string, unknown> = {};
  a.self = a;
  assert.throws(() => mpEncode(a as never), /cyclic/);
  const arr: unknown[] = [];
  arr.push(arr);
  assert.throws(() => mpEncode(arr as never), /cyclic/);

  const shared = { n: 1 };
  assert.equal(hex(mpEncode({ a: shared, b: shared })), hex(legacy.mpEncode({ a: shared, b: shared })));
});

test('nesting past the bound is refused', () => {
  let deep: unknown = 1;
  for (let i = 0; i < 40; i++) deep = [deep];
  assert.throws(() => mpEncode(deep as never), /nesting deeper/);
});

test('bytesToB64 handles payloads past the 0x8000 chunk boundary', () => {
  const big = new Uint8Array(0x8000 * 2 + 17);
  for (let i = 0; i < big.length; i++) big[i] = i & 255;
  assert.equal(bytesToB64(big), Buffer.from(big).toString('base64'));
  assert.equal(hex(b64ToBytes(bytesToB64(big))), hex(big));
});
