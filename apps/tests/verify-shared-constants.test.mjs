// FIX_PLAN 2A.2: the page's pre-check limits and camera request come from the SDK
// (main.js imports them from /sdk/index.js and hands them in), not from page copies.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { CAMERA_CONSTRAINTS, LUMA_MIN, SHARP_MIN } from '../../packages/face-sdk/src/index.ts';
import { createCameraOwner } from '../verify/bridge.js';
import { createFraming, framingStep, precheck, SETTLE_MS } from '../verify/framing.js';

const LIMITS = { lumaMin: LUMA_MIN, sharpMin: SHARP_MIN };
const source = (rel) => fs.readFileSync(new URL(rel, import.meta.url), 'utf8');

test('the page defines no copy of the SDK limits or camera request', () => {
  assert.doesNotMatch(source('../verify/framing.js'), /\b(LUMA_MIN|SHARP_MIN)\s*=/);
  assert.doesNotMatch(source('../verify/bridge.js'), /\bCAMERA_CONSTRAINTS\s*=/);
  const main = source('../verify/main.js');
  assert.match(main, /import \{[^}]*\bCAMERA_CONSTRAINTS\b[^}]*\} from '\/sdk\/index\.js'/);
  assert.match(main, /import \{[^}]*\bLUMA_MIN\b[^}]*\} from '\/sdk\/index\.js'/);
  assert.match(main, /import \{[^}]*\bSHARP_MIN\b[^}]*\} from '\/sdk\/index\.js'/);
});

test('the pre-check uses the limits it is given, at their edges', () => {
  const g = (o) => ({ luma: 120, sharp: 80, ...o });
  assert.equal(precheck(g({ luma: LUMA_MIN - 1 }), {}, LIMITS), 'light');
  assert.equal(precheck(g({ luma: LUMA_MIN }), {}, LIMITS), null);
  assert.equal(precheck(g({ sharp: SHARP_MIN - 1 }), {}, LIMITS), 'steady');
  assert.equal(precheck(g({ sharp: SHARP_MIN }), {}, LIMITS), null);
  assert.throws(() => precheck(g(), {}), /limits/);
});

test('framing carries its limits into the pre-check', () => {
  assert.throws(() => createFraming(), /limits/);
  const F = createFraming(LIMITS);
  const dark = { armed: true, cue: 'good', luma: LUMA_MIN - 1, sharp: 80 };
  framingStep(F, dark, 0);
  assert.equal(framingStep(F, dark, SETTLE_MS), 'light');
});

test('the camera owner asks for the constraints it is given', async () => {
  let asked = null;
  const track = { readyState: 'live', addEventListener() {}, removeEventListener() {}, stop() {} };
  const stream = { getTracks: () => [track] };
  const mediaDevices = { getUserMedia: async (c) => { asked = c; return stream; } };
  const video = { srcObject: null, async play() {} };
  assert.throws(() => createCameraOwner({ mediaDevices, video }), /constraints/);
  const owner = createCameraOwner({ mediaDevices, video, constraints: CAMERA_CONSTRAINTS });
  await owner.open();
  assert.equal(asked, CAMERA_CONSTRAINTS);
  owner.stop();
});
