import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import * as sdk from '../src/index.ts';
import { ERROR_CODES, ERROR_TEXT } from '../src/errors.ts';

// The retry sets session.ts carried before FIX_PLAN 2A.3, frozen here so the shared map
// provably changes none of them.
const ENGINE_RETRYABLE = new Set([
  'NO_FACE', 'MULTI_FACE', 'LIVENESS_FAIL', 'CHALLENGE_FAIL', 'LOW_QUALITY', 'BUSY',
  'ENGINE_UNREACHABLE',
]);
const ENGINE_COUNTS_AS_ATTEMPT = new Set(['NO_FACE', 'MULTI_FACE', 'LIVENESS_FAIL', 'CHALLENGE_FAIL']);
const SDK_RETRYABLE = new Set([
  'CAMERA_ENDED', 'FACE_LOST', 'CHALLENGE_UNAVAILABLE', 'CHALLENGE_EXPIRED',
  'NETWORK', 'TIMEOUT', 'SCAN_TOO_LARGE',
]);

test('ERROR_CODES and ERROR_TEXT are public SDK exports', () => {
  assert.equal(sdk.ERROR_CODES, ERROR_CODES);
  assert.equal(sdk.ERROR_TEXT, ERROR_TEXT);
  assert.ok(Object.isFrozen(ERROR_CODES) && Object.isFrozen(ERROR_TEXT));
});

test('the map keeps exactly today\'s retryable and counts-as-attempt codes', () => {
  for (const [code, info] of Object.entries(ERROR_CODES)) {
    const was = info.origin === 'sdk' ? SDK_RETRYABLE.has(code) : ENGINE_RETRYABLE.has(code);
    assert.equal(info.retry, was, code + ' retry');
    assert.equal(info.attempt, info.origin !== 'sdk' && ENGINE_COUNTS_AS_ATTEMPT.has(code), code + ' attempt');
  }
  for (const c of [...ENGINE_RETRYABLE, ...ENGINE_COUNTS_AS_ATTEMPT, ...SDK_RETRYABLE]) {
    assert.ok(Object.hasOwn(ERROR_CODES, c), c);
  }
});

test('session.ts reads retry and attempt from the map, not its own sets', () => {
  const session = readFileSync(new URL('../src/workflow/session.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(session, /ENGINE_RETRYABLE|ENGINE_COUNTS_AS_ATTEMPT|SDK_RETRYABLE/);
  assert.match(session, /from '\.\.\/errors\.ts'/);
});

test('every code has a well-formed entry', () => {
  for (const [code, info] of Object.entries(ERROR_CODES)) {
    assert.match(code, /^[A-Z][A-Z0-9_]{2,}$/);
    assert.equal(info.http === null, info.origin === 'sdk', code);
    assert.ok(!info.attempt || info.retry, code + ': an attempt that counts is also retryable');
  }
  for (const code of Object.keys(ERROR_TEXT)) assert.ok(Object.hasOwn(ERROR_CODES, code), code);
});
