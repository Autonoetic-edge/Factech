import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ENGINE_MESSAGE_MAX, canonicalUserId, engineMessage, getChallenge, isValidUserId, livenessVerdict,
  postScan, resolveGatewayUrl,
} from '../src/transport/gateway.ts';
import type { EnrollBody, GatewayConfig, VerifyBody } from '../src/transport/gateway.ts';
import {
  CHALLENGE_TIMEOUT_MS, DEFAULT_CHALLENGE_LIFE_MS, MAX_RETRY_AFTER_MS, SCAN_TIMEOUT_MS,
} from '../src/constants.ts';
import { testEnv } from './helpers.ts';

const NONCE = '9f2c41ab7e05d8630b1a4c77e2f9aa10';
const OK_CHALLENGE = {
  nonce: NONCE, action: 'MOVE_CLOSER',
  params: { settle_ms: 1450, target: 0.32 },
  issued_ms: 1757745600000, expires_ms: 1757745630000,
};

function harness() {
  const t = testEnv();
  const cfg: GatewayConfig = { base: '', env: t.env };
  return { ...t, cfg, signal: new AbortController().signal };
}

test('same-origin is the default and a path is kept', () => {
  assert.equal(resolveGatewayUrl(undefined), '');
  assert.equal(resolveGatewayUrl(''), '');
  assert.equal(resolveGatewayUrl('/api'), '/api');
  assert.equal(resolveGatewayUrl('/api/'), '/api');
});

test('https is accepted; http is accepted only against loopback', () => {
  assert.equal(resolveGatewayUrl('https://gw.example.com'), 'https://gw.example.com');
  assert.equal(resolveGatewayUrl('https://gw.example.com/edge/'), 'https://gw.example.com/edge');
  assert.equal(resolveGatewayUrl('http://localhost:8080'), 'http://localhost:8080');
  assert.equal(resolveGatewayUrl('http://127.0.0.1:8080/'), 'http://127.0.0.1:8080');
  assert.throws(() => resolveGatewayUrl('http://gw.example.com'), /localhost only/);
});

test('a URL that would leak a credential, drop a query, or change scheme is refused', () => {

  assert.throws(() => resolveGatewayUrl('https://user:pw@gw.example.com'), /credentials/);
  assert.throws(() => resolveGatewayUrl('https://gw.example.com?k=1'), /query or fragment/);
  assert.throws(() => resolveGatewayUrl('https://gw.example.com#f'), /query or fragment/);
  assert.throws(() => resolveGatewayUrl('/api?k=1'), /query or fragment/);
  assert.throws(() => resolveGatewayUrl('//gw.example.com'), /protocol-relative/);
  assert.throws(() => resolveGatewayUrl('ws://gw.example.com'), /must be https/);
  assert.throws(() => resolveGatewayUrl('javascript:alert(1)'), /must be https/);
  assert.throws(() => resolveGatewayUrl('gw.example.com'), /not a URL/);
});

test('user ids are canonicalised and checked against contract 2.1 before anything is sent', () => {
  assert.equal(canonicalUserId('  S-01 '), 's-01');
  assert.equal(isValidUserId('s-01'), true);
  assert.equal(isValidUserId('s_01'), true);
  assert.equal(isValidUserId('a.b-c_1'), true);
  assert.equal(isValidUserId(''), false);
  assert.equal(isValidUserId('a'.repeat(64)), true);
  assert.equal(isValidUserId('a'.repeat(65)), false);
  assert.equal(isValidUserId('has space'), false);
  assert.equal(isValidUserId('Has-Upper'), false);
  assert.equal(isValidUserId('ünïcode'), false);
});

test('every request is same-origin, uncached, non-redirecting and referrer-free', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE });
  await getChallenge(h.cfg, h.signal);
  const init = h.fetch.calls[0]!.init!;
  assert.equal(init.credentials, 'same-origin');
  assert.equal(init.cache, 'no-store');
  assert.equal(init.redirect, 'error');
  assert.equal(init.referrerPolicy, 'no-referrer');
  assert.ok(init.signal, 'every request carries a signal');
});

test('the base URL is prefixed to the path', async () => {
  const h = harness();
  const cfg: GatewayConfig = { base: 'https://gw.example.com/edge', env: h.env };
  h.fetch.reply('/edge/v1/challenge', { body: OK_CHALLENGE });
  const got = await getChallenge(cfg, h.signal);
  assert.equal(got.ok, true);
  assert.equal(h.fetch.calls[0]!.url, 'https://gw.example.com/edge/v1/challenge');
});

test('a well-formed challenge is accepted and its life comes from the server pair', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE, headers: { 'X-Request-Id': 'req-12345678' } });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, true);
  if (!got.ok) return;
  assert.equal(got.value.nonce, NONCE);
  assert.equal(got.value.action, 'MOVE_CLOSER');
  assert.deepEqual(got.value.params, { settle_ms: 1450, target: 0.32 });
  assert.equal(got.value.lifeMs, 30_000);
  assert.equal(got.requestId, 'req-12345678');
});

test('a nonce that is not 32 lowercase hex is BAD_RESPONSE', async () => {
  const bad = [
    NONCE.toUpperCase(), NONCE.slice(0, 31), NONCE + 'a', '', 'g'.repeat(32), 12345,
    null, { nonce: 1 },
  ];
  for (const nonce of bad) {
    const h = harness();
    h.fetch.reply('/v1/challenge', { body: { ...OK_CHALLENGE, nonce } });
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, false, JSON.stringify(nonce));
    if (!got.ok) assert.equal(got.kind, 'BAD_RESPONSE');
  }
});

test('an action outside the contract 1.4 vocabulary is BAD_RESPONSE', async () => {
  for (const action of ['SMILE', 'move_closer', '', 42, null]) {
    const h = harness();
    h.fetch.reply('/v1/challenge', { body: { ...OK_CHALLENGE, action } });
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, false, String(action));
  }
});

test('every action in the vocabulary is accepted, including the one never issued', async () => {

  for (const action of ['MOVE_CLOSER', 'LOOK_LEFT', 'LOOK_RIGHT', 'BLINK_TWICE']) {
    const h = harness();
    h.fetch.reply('/v1/challenge', { body: { ...OK_CHALLENGE, action } });
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, true, action);
  }
});

test('params must be a map of finite numbers, and a bad member refuses the whole map', async () => {
  const bad = [
    { settle_ms: 1450, target: 'x' }, { settle_ms: Number.NaN, target: 0.3 },
    { settle_ms: 1450, target: null }, { settle_ms: 1450, target: Infinity },
    [1450, 0.3], 'params', 7,
  ];
  for (const params of bad) {
    const h = harness();
    h.fetch.reply('/v1/challenge', { body: { ...OK_CHALLENGE, params } });
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, false, JSON.stringify(params));
  }
});

test('an absent params map is null, not a refusal: a pre-S7 engine still works', async () => {
  const h = harness();
  const { params, ...noParams } = OK_CHALLENGE;
  void params;
  h.fetch.reply('/v1/challenge', { body: noParams });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, true);
  if (got.ok) assert.equal(got.value.params, null);
});

test('an unusable issued/expires pair falls back to the contract 2.5 window', async () => {
  for (const pair of [{}, { issued_ms: 5, expires_ms: 5 }, { issued_ms: 10, expires_ms: 5 },
    { issued_ms: 'a', expires_ms: 9 }]) {
    const h = harness();
    h.fetch.reply('/v1/challenge', { body: { ...OK_CHALLENGE, issued_ms: undefined, expires_ms: undefined, ...pair } });
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, true);
    if (got.ok) assert.equal(got.value.lifeMs, DEFAULT_CHALLENGE_LIFE_MS);
  }
});

test('a server claiming a longer life than the contract allows does not get one', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { body: { ...OK_CHALLENGE, issued_ms: 0, expires_ms: 3_600_000 } });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, true);
  if (got.ok) assert.equal(got.value.lifeMs, DEFAULT_CHALLENGE_LIFE_MS);
});

test('a body that is not a map, or not JSON at all, is BAD_RESPONSE', async () => {
  for (const spec of [{ body: [1, 2] }, { body: 'hello' }, { body: null }, { notJson: true }]) {
    const h = harness();
    h.fetch.reply('/v1/challenge', spec);
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, false, JSON.stringify(spec));
    if (!got.ok) assert.equal(got.kind, 'BAD_RESPONSE');
  }
});

test('a contract 3.1 envelope passes its code through verbatim', async () => {
  const h = harness();
  h.fetch.reply('/v1/enroll', {
    status: 422, body: { error: { code: 'CHALLENGE_FAIL', message: 'nonce reused' } },
    headers: { 'X-Request-Id': 'req-abcdefgh' },
  });
  const got = await postScan(h.cfg, 'enroll', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, false);
  if (got.ok) return;
  assert.equal(got.kind, 'REJECTED');
  assert.equal(got.engineCode, 'CHALLENGE_FAIL');
  assert.equal(got.httpStatus, 422);
  assert.equal(got.requestId, 'req-abcdefgh');
});

test('the engine\'s message is carried into the failure message, for the log', async () => {

  const h = harness();
  h.fetch.reply('/v1/verify', {
    status: 422, body: { error: { code: 'LIVENESS_FAIL', message: 'liveness check failed: challenge, motion' } },
  });
  const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, false);
  if (got.ok) return;
  assert.equal(got.message, 'gateway returned 422 LIVENESS_FAIL: liveness check failed: challenge, motion');
  assert.equal(got.engineCode, 'LIVENESS_FAIL', 'the code is unchanged');
});

test('a carried message is redacted, cleaned and capped', async () => {
  const nonce = 'ab'.repeat(16);
  const cases: [unknown, string][] = [

    [`nonce ${nonce} reused by s-01`, 'gateway returned 422 CHALLENGE_FAIL: nonce <hex> reused by <user>'],
    ['line one\nline\x07two', 'gateway returned 422 CHALLENGE_FAIL: line one line two'],
    ['x'.repeat(5000), 'gateway returned 422 CHALLENGE_FAIL: ' + 'x'.repeat(ENGINE_MESSAGE_MAX) + '...'],

    [42, 'gateway returned 422 CHALLENGE_FAIL'],
    ['   ', 'gateway returned 422 CHALLENGE_FAIL'],
  ];
  for (const [message, expected] of cases) {
    const h = harness();
    h.fetch.reply('/v1/enroll', { status: 422, body: { error: { code: 'CHALLENGE_FAIL', message } } });
    const got = await postScan(h.cfg, 'enroll', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, false);
    if (!got.ok) assert.equal(got.message, expected, JSON.stringify(message).slice(0, 40));
  }
  assert.equal(engineMessage({ error: { message: 'm' } }), 'm');
});

test('a body that is not the envelope carries no message: a proxy page is not the engine', async () => {
  const h = harness();
  h.fetch.reply('/v1/verify', { status: 502, body: { error: { message: 'upstream said something' } } });
  const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) assert.equal(got.message, 'gateway returned 502');
});

test('an error body that is not the envelope yields no engineCode to branch on', async () => {
  for (const body of [{ detail: 'validation failed' }, { error: 'nope' }, { error: { code: 'lower' } },
    { error: { code: 'x' } }, '<html>502</html>']) {
    const h = harness();
    h.fetch.reply('/v1/verify', { status: 422, body });
    const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, false);
    if (!got.ok) assert.equal(got.engineCode, null, JSON.stringify(body));
  }
});

test('Retry-After is read in seconds and capped', async () => {
  const cases: [string, number | null][] = [
    ['2', 2000], ['0', 0], ['120', MAX_RETRY_AFTER_MS], ['-1', null],
    ['Wed, 21 Oct 2015 07:28:00 GMT', null], ['', null], ['abc', null],
  ];
  for (const [header, expected] of cases) {
    const h = harness();
    h.fetch.reply('/v1/verify', {
      status: 503, body: { error: { code: 'BUSY', message: 'queue full' } },
      headers: { 'Retry-After': header },
    });
    const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, false);
    if (!got.ok) assert.equal(got.retryAfterMs, expected, JSON.stringify(header));
  }
});

test('a malformed X-Request-Id is dropped rather than surfaced', async () => {
  for (const rid of ['short', 'a'.repeat(65), 'has space', 'has_underscore', '<script>']) {
    const h = harness();
    h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE, headers: { 'X-Request-Id': rid } });
    const got = await getChallenge(h.cfg, h.signal);
    assert.equal(got.ok, true);
    if (got.ok) assert.equal(got.requestId, null, rid);
  }
});

test('a request that outlasts its timeout is TIMEOUT, not a hang', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE, delayMs: CHALLENGE_TIMEOUT_MS + 1000 });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) assert.equal(got.kind, 'TIMEOUT');
});

test('a request just inside its timeout still lands', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE, delayMs: CHALLENGE_TIMEOUT_MS - 1 });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, true);
});

test('a dropped connection is NETWORK, told apart from a timeout and a cancel', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { networkError: true });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) assert.equal(got.kind, 'NETWORK');
});

test('an abort mid-flight is CANCELLED', async () => {
  const h = harness();
  const ac = new AbortController();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE, delayMs: 2000 });
  const pending = getChallenge(h.cfg, ac.signal);
  ac.abort();
  const got = await pending;
  assert.equal(got.ok, false);
  if (!got.ok) assert.equal(got.kind, 'CANCELLED');
});

test('f13 a challenge body that stalls past the deadline is TIMEOUT, keeps the request id and leaves no timer', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', {
    body: OK_CHALLENGE, headers: { 'X-Request-Id': 'req-13000001' }, bodyDelayMs: CHALLENGE_TIMEOUT_MS + 1000,
  });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) {
    assert.equal(got.kind, 'TIMEOUT');
    assert.equal(got.requestId, 'req-13000001');
  }
  assert.equal(h.clock.pending, 0);
});

test('f13 a scan body that stalls past the deadline is TIMEOUT, not BAD_RESPONSE', async () => {
  const h = harness();
  h.fetch.reply('/v1/verify', {
    body: { match: true, score: 0.9, threshold: 0.5, liveness: LIVE },
    headers: { 'X-Request-Id': 'req-13000002' }, bodyDelayMs: SCAN_TIMEOUT_MS + 1000,
  });
  const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) {
    assert.equal(got.kind, 'TIMEOUT');
    assert.equal(got.requestId, 'req-13000002');
  }
});

test('f13 a body that arrives just inside the deadline still lands', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE, delayMs: 1000, bodyDelayMs: CHALLENGE_TIMEOUT_MS - 1001 });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, true);
});

test('f13 a user abort while the body is arriving is CANCELLED, with the request id', async () => {
  const h = harness();
  const ac = new AbortController();
  h.fetch.reply('/v1/challenge', {
    body: OK_CHALLENGE, headers: { 'X-Request-Id': 'req-13000003' }, bodyDelayMs: 2000,
    onBody: () => ac.abort(),
  });
  const got = await getChallenge(h.cfg, ac.signal);
  assert.equal(got.ok, false);
  if (!got.ok) {
    assert.equal(got.kind, 'CANCELLED');
    assert.equal(got.requestId, 'req-13000003');
  }
  assert.equal(h.clock.pending, 0);
});

test('f13 a body stream that breaks is NETWORK, not a protocol error', async () => {
  const h = harness();
  h.fetch.reply('/v1/challenge', { headers: { 'X-Request-Id': 'req-13000004' }, bodyError: true });
  const got = await getChallenge(h.cfg, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) {
    assert.equal(got.kind, 'NETWORK');
    assert.equal(got.requestId, 'req-13000004');
  }
});

test('f13 invalid JSON on a 200 is BAD_RESPONSE that says so; on an error status it stays REJECTED', async () => {
  const h = harness();
  h.fetch.reply('/v1/verify', { headers: { 'X-Request-Id': 'req-13000005' }, notJson: true });
  const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, false);
  if (!got.ok) {
    assert.equal(got.kind, 'BAD_RESPONSE');
    assert.match(got.message, /not valid JSON/);
    assert.equal(got.requestId, 'req-13000005');
  }
  const proxy = harness();
  proxy.fetch.reply('/v1/verify', { status: 502, notJson: true });
  const page = await postScan(proxy.cfg, 'verify', 's-01', 'AAAA', null, proxy.signal);
  assert.equal(page.ok, false);
  if (!page.ok) {
    assert.equal(page.kind, 'REJECTED');
    assert.equal(page.httpStatus, 502);
  }
});

test('a request started with an already-aborted signal never reaches the network usefully', async () => {
  const h = harness();
  const ac = new AbortController();
  ac.abort();
  h.fetch.reply('/v1/challenge', { body: OK_CHALLENGE });
  const got = await getChallenge(h.cfg, ac.signal);
  assert.equal(got.ok, false);
  if (!got.ok) assert.equal(got.kind, 'CANCELLED');
});

const LIVE = { live: true, score: 0.94, enforced: true, failed_signals: [] };

test('a 200 enroll is validated into a template id and an advisory quality', async () => {
  const h = harness();
  h.fetch.reply('/v1/enroll', { body: { template_id: 't-1', quality: { score: 0.87 }, liveness: LIVE } });
  const got = await postScan(h.cfg, 'enroll', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, true);
  if (!got.ok) return;
  const body = got.value as EnrollBody;
  assert.equal(body.templateId, 't-1');
  assert.equal(body.quality, 0.87);
  assert.equal(body.liveness, 'passed');
});

test('an enroll with no usable template_id is BAD_RESPONSE', async () => {
  for (const template_id of [undefined, '', null, 7, {}]) {
    const h = harness();
    h.fetch.reply('/v1/enroll', { body: { template_id, quality: { score: 1 }, liveness: LIVE } });
    const got = await postScan(h.cfg, 'enroll', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, false, JSON.stringify(template_id));
    if (!got.ok) assert.equal(got.kind, 'BAD_RESPONSE');
  }
});

test('a missing quality score is null, not a refused enrolment', async () => {
  for (const quality of [undefined, {}, { score: 'x' }, null]) {
    const h = harness();
    h.fetch.reply('/v1/enroll', { body: { template_id: 't-1', quality, liveness: LIVE } });
    const got = await postScan(h.cfg, 'enroll', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, true, JSON.stringify(quality));
    if (got.ok) assert.equal((got.value as EnrollBody).quality, null);
  }
});

test('a verify is accepted only with a strictly boolean match and finite numbers', async () => {
  const h = harness();
  h.fetch.reply('/v1/verify', { body: { match: true, score: 0.612, threshold: 0.55, liveness: LIVE } });
  const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
  assert.equal(got.ok, true);
  if (got.ok) assert.deepEqual(got.value as VerifyBody,
    { match: true, score: 0.612, threshold: 0.55, liveness: 'passed' });
});

test('a truthy non-boolean match is BAD_RESPONSE, never a sign-in', async () => {

  for (const match of [1, 'true', 'yes', {}, [], null, undefined]) {
    const h = harness();
    h.fetch.reply('/v1/verify', { body: { match, score: 0.9, threshold: 0.55, liveness: LIVE } });
    const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, false, JSON.stringify(match));
    if (!got.ok) assert.equal(got.kind, 'BAD_RESPONSE');
  }
});

test('a non-finite score or threshold is BAD_RESPONSE', async () => {
  for (const body of [
    { match: false, score: 'x', threshold: 0.55 },
    { match: false, score: 0.5, threshold: null },
    { match: false, score: Number.NaN, threshold: 0.55 },
    { match: false, threshold: 0.55 },
  ]) {
    const h = harness();
    h.fetch.reply('/v1/verify', { body: { ...body, liveness: LIVE } });
    const got = await postScan(h.cfg, 'verify', 's-01', 'AAAA', null, h.signal);
    assert.equal(got.ok, false, JSON.stringify(body));
  }
});

test('the liveness verdict distinguishes checked-and-held from not-checked (finding 15)', () => {
  assert.equal(livenessVerdict({ live: true, enforced: true }), 'passed');
  assert.equal(livenessVerdict({ live: false, enforced: true }), 'failed');
  assert.equal(livenessVerdict({ live: true, enforced: false }), 'not-enforced');
  assert.equal(livenessVerdict({ live: true }), 'not-enforced');
  assert.equal(livenessVerdict({ live: 'true', enforced: true }), 'unknown');
  assert.equal(livenessVerdict(undefined), 'unknown');
  assert.equal(livenessVerdict(null), 'unknown');
});

test('the scan body is the contract 2.1 (a) JSON transport, and nothing else', async () => {
  const h = harness();
  h.fetch.reply('/v1/enroll', { body: { template_id: 't-1', quality: { score: 1 }, liveness: LIVE } });
  await postScan(h.cfg, 'enroll', 's-01', 'QUJD', { subject: 'S01', label: 'bona_fide' }, h.signal);
  const init = h.fetch.calls[0]!.init!;
  assert.deepEqual(JSON.parse(String(init.body)), { user_id: 's-01', facescan: 'QUJD' });
  const headers = init.headers as Record<string, string>;
  assert.equal(headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(headers['X-Capture-Meta']!), { subject: 'S01', label: 'bona_fide' });
});

test('with no capture meta the header is absent, not empty', async () => {
  const h = harness();
  h.fetch.reply('/v1/verify', { body: { match: false, score: 0.1, threshold: 0.55, liveness: LIVE } });
  await postScan(h.cfg, 'verify', 's-01', 'QUJD', null, h.signal);
  const headers = h.fetch.calls[0]!.init!.headers as Record<string, string>;
  assert.equal('X-Capture-Meta' in headers, false);
});

test('rawRequest (console transport) returns the raw status, body and request id under the same rules', async () => {
  const { rawRequest } = await import('../src/internal.ts');
  const h = harness();
  h.fetch.reply('/v1/verify', { status: 422, body: { error: { code: 'LIVENESS_FAIL' } },
    headers: { 'X-Request-Id': 'req-55556666' } });
  const got = await rawRequest(h.env, '', 'POST', '/v1/verify', SCAN_TIMEOUT_MS, h.signal, { user_id: 'a' });
  assert.equal(got.ok, true);
  if (!got.ok) return;
  assert.equal(got.value.status, 422);
  assert.deepEqual(got.value.body, { error: { code: 'LIVENESS_FAIL' } });
  assert.equal(got.value.requestId, 'req-55556666');
  const init = h.fetch.calls[0]!.init!;
  assert.equal(init.method, 'POST');
  assert.equal(init.redirect, 'error');
  assert.equal(init.cache, 'no-store');
  assert.equal(init.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(String(init.body)), { user_id: 'a' });
  assert.equal((init.headers as Record<string, string>)['Content-Type'], 'application/json');
});

test('rawRequest times out at its deadline and aborts on the run signal, leaving no timer', async () => {
  const { rawRequest } = await import('../src/internal.ts');
  const h = harness();
  h.fetch.reply('/health', { body: {}, delayMs: CHALLENGE_TIMEOUT_MS + 1000 });
  const slow = await rawRequest(h.env, '', 'GET', '/health', CHALLENGE_TIMEOUT_MS, h.signal);
  assert.equal(slow.ok, false);
  if (!slow.ok) assert.equal(slow.kind, 'TIMEOUT');
  const ac = new AbortController();
  const pending = rawRequest(h.env, '', 'GET', '/health', CHALLENGE_TIMEOUT_MS, ac.signal);
  ac.abort();
  const cut = await pending;
  assert.equal(cut.ok, false);
  if (!cut.ok) assert.equal(cut.kind, 'CANCELLED');
  assert.equal(h.fetch.calls[1]!.init!.body, undefined, 'a GET carries no body');
  await new Promise((r) => setImmediate(r));
  assert.equal(h.clock.pending, 0);
});
