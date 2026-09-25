// FIX_PLAN 2A.3: the page keys its outcomes and camera notices by the SDK's shared
// ERROR_CODES (main.js imports the map from /sdk/index.js and hands it to outcome.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { ERROR_CODES } from '../../packages/face-sdk/src/index.ts';
import { errorOutcome, sdkFailureOutcome, useErrorCodes } from '../verify/outcome.js';
import { COPY } from '../verify/view.js';
import { CAMERA_ERRORS } from '../verify/flow.js';

// outcome.js's own code table before 2A.3, frozen here so the shared map provably keeps
// every page outcome as it was.
const BY_CODE = {
  AUTHENTICATION_REQUIRED: 'expired', CSRF_REQUIRED: 'expired', RECENT_LOGIN_REQUIRED: 'reauth',
  CONSENT_REQUIRED: 'consent', CONSENT_CHANGED: 'consent', CONSENT_VERSION_REQUIRED: 'consent',
  NO_FACE: 'quality', MULTI_FACE: 'quality', LOW_QUALITY: 'quality',
  LIVENESS_FAIL: 'liveness', CHALLENGE_FAIL: 'liveness',
  CHALLENGE_INVALID: 'retry', OPERATION_EXPIRED: 'retry', OPERATION_INTERRUPTED: 'retry',
  USER_NOT_FOUND: 'notenrolled', TEMPLATE_EXPIRED: 'notenrolled',
  BUSY: 'busy', DEPENDENCY_UNAVAILABLE: 'busy', OPERATION_IN_PROGRESS: 'pending',
  CAPACITY_EXCEEDED: 'already', ROUND_CLOSED: 'closed', ROUND_UNCONFIGURED: 'closed',
};
function before(code, status) {
  if (typeof code === 'string' && BY_CODE[code]) return BY_CODE[code];
  if (status === 401) return 'expired';
  if (status === 429 || status === 503) return 'busy';
  return 'server';
}

test('outcome.js holds no code table of its own', () => {
  const src = fs.readFileSync(new URL('../verify/outcome.js', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /\bBY_CODE\b/);
  const main = fs.readFileSync(new URL('../verify/main.js', import.meta.url), 'utf8');
  assert.match(main, /import \{[^}]*\bERROR_CODES\b[^}]*\} from '\/sdk\/index\.js'/);
  assert.match(main, /useErrorCodes\(ERROR_CODES\)/);
});

test('every server code gives the page the same outcome as before', () => {
  useErrorCodes(ERROR_CODES);
  const codes = [...Object.keys(ERROR_CODES), 'NOT_A_CODE', null, undefined];
  const statuses = [200, 400, 401, 403, 404, 408, 409, 413, 415, 422, 429, 500, 502, 503];
  for (const code of codes) {
    for (const status of statuses) {
      if (code && ERROR_CODES[code]?.origin === 'sdk') continue; // never sent by a server
      assert.equal(errorOutcome(code, status), before(code, status), `${code} ${status}`);
    }
  }
});

test('camera failures are keyed by the shared SDK codes', () => {
  assert.deepEqual(Object.keys(COPY.CAMERA_NOTICE).sort(), ['CAMERA_DENIED', 'CAMERA_ENDED', 'CAMERA_UNAVAILABLE']);
  assert.deepEqual([...CAMERA_ERRORS].sort(), Object.keys(COPY.CAMERA_NOTICE).sort());
  for (const code of CAMERA_ERRORS) {
    assert.equal(ERROR_CODES[code]?.stage, 'camera', code);
    assert.equal(sdkFailureOutcome(code), code);
  }
});

test('without the map the page refuses to guess', () => {
  useErrorCodes(null);
  assert.throws(() => errorOutcome('BUSY', 503), /ERROR_CODES/);
  useErrorCodes(ERROR_CODES);
});
