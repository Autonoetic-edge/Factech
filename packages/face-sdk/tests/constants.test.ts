import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import * as sdk from '../src/index.ts';
import { openCamera } from '../src/camera/camera.ts';
import {
  CAMERA_CONSTRAINTS, FRAME_COUNT, HEAD_SEQUENCE_INTERVAL_MS, HEAD_SEQUENCE_SPAN_MS, LUMA_MIN, SHARP_MIN,
} from '../src/constants.ts';
import { testEnv } from './helpers.ts';

const source = (rel: string): string => readFileSync(new URL('../src/' + rel, import.meta.url), 'utf8');

test('the HEAD_SEQUENCE capture numbers are named once (FIX_PLAN 2A.2)', () => {
  assert.equal(HEAD_SEQUENCE_INTERVAL_MS, 1400);
  assert.equal(HEAD_SEQUENCE_SPAN_MS, (FRAME_COUNT - 1) * HEAD_SEQUENCE_INTERVAL_MS);
  assert.equal(HEAD_SEQUENCE_SPAN_MS, 15400);
  const session = source('workflow/session.ts');
  assert.doesNotMatch(session, /\b15400\b/);
  assert.doesNotMatch(session, /intervalMs: sequence \? 1400\b/);
});

test('the page limits and camera request are SDK exports', () => {
  assert.equal(sdk.LUMA_MIN, LUMA_MIN);
  assert.equal(sdk.SHARP_MIN, SHARP_MIN);
  assert.equal(sdk.CAMERA_CONSTRAINTS, CAMERA_CONSTRAINTS);
  assert.deepEqual([LUMA_MIN, SHARP_MIN], [55, 12]);
  assert.doesNotMatch(source('camera/camera.ts'), /CAMERA_CONSTRAINTS[^;]*=\s*\{/, 'defined once, in constants.ts');
});

test('openCamera asks for exactly CAMERA_CONSTRAINTS', async () => {
  const t = testEnv();
  let asked: unknown = null;
  const real = t.env.getUserMedia;
  const env = { ...t.env, getUserMedia: (c: MediaStreamConstraints) => { asked = c; return real(c); } };
  const cam = await openCamera(env, t.video, () => undefined);
  cam.stop();
  assert.equal(asked, CAMERA_CONSTRAINTS);
});
