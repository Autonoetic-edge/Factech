import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createFaceSession } from '../src/index.ts';
import {
  CAPTURE_MS, ENGINE_MAX_BYTES, EXPIRY_MARGIN_MS, FACE_LOST_MS, FRAME_COUNT,
  LANDING_MS, READY_TIMEOUT_MS, SCAN_TIMEOUT_MS,
} from '../src/constants.ts';
import type {
  EnrollResult, Failure, FaceProbe, FaceSessionOptions, SessionEvent, VerifyResult,
} from '../src/types.ts';
import { mpDecode } from './helpers.ts';
import { testEnv } from './helpers.ts';

const NONCE = '9f2c41ab7e05d8630b1a4c77e2f9aa10';
const CHALLENGE = {
  nonce: NONCE, action: 'MOVE_CLOSER',
  params: { settle_ms: 1450, target: 0.32 },
  issued_ms: 1_757_745_600_000, expires_ms: 1_757_745_630_000,
};
const LIVE = { live: true, score: 0.94, enforced: true, failed_signals: [] };
const ENROLLED = { template_id: 'tpl-1', quality: { score: 0.87 }, liveness: LIVE };
const MATCHED = { match: true, score: 0.612, threshold: 0.55, liveness: LIVE };

function session(over: Partial<FaceSessionOptions> = {}, envOptions: Parameters<typeof testEnv>[0] = {}) {
  const t = testEnv(envOptions);
  const events: SessionEvent[] = [];
  t.fetch.reply('/v1/challenge', { body: CHALLENGE });
  t.fetch.reply('/v1/enroll', { body: ENROLLED, headers: { 'X-Request-Id': 'req-11112222' } });
  t.fetch.reply('/v1/verify', { body: MATCHED, headers: { 'X-Request-Id': 'req-33334444' } });
  const s = createFaceSession({
    videoElement: t.video,
    onEvent: (e) => events.push(e),
    env: t.env,
    ...over,
  });
  return { ...t, session: s, events };
}

const asFailure = (r: EnrollResult | VerifyResult): Failure => {
  assert.equal(r.ok, false, 'expected a failure, got ' + JSON.stringify(r));
  return r as Failure;
};

test('head sequence binds identity/operation, emits ordered cues and records 1400ms cadence', async () => {
  for (const sign of [-1, 1]) {
    const h = session();
    h.fetch.reply('/v1/challenge', { body: { ...CHALLENGE, action: 'HEAD_SEQUENCE',
      params: { settle_ms: 3200, switch_ms: 9200, first_sign: sign, target: .2 } } });
    const result = await h.session.enroll(' T01 ');
    assert.equal(result.ok, true);
    assert.equal(h.fetch.calls[0]!.url, '/v1/challenge');
    assert.equal(new Headers(h.fetch.calls[0]!.init!.headers).get('X-User-Id'), 't01');
    assert.equal(new Headers(h.fetch.calls[0]!.init!.headers).get('X-Facetech-Operation'), 'enroll');
    const cues = h.events.filter(e => e.type === 'action').map(e => e.text);
    assert.equal(cues.length, 3);
    assert.match(cues[0]!, /forward/);
    assert.match(cues[1]!, sign > 0 ? /LEFT/ : /RIGHT/);
    assert.match(cues[2]!, sign > 0 ? /RIGHT/ : /LEFT/);
    const post = h.fetch.calls.find(c => c.url.endsWith('/v1/enroll'))!;
    const body = JSON.parse(String(post.init!.body));
    const scan = mpDecode(Buffer.from(body.facescan, 'base64')) as { frames: {ts_ms: number}[] };
    assert.equal(scan.frames.at(-1)!.ts_ms - scan.frames[0]!.ts_ms, 15400);
  }
});

test('an enroll runs end to end and reports what the engine said', async () => {
  const h = session();
  const result = await h.session.enroll('  S-01  ');
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.op, 'enroll');
  assert.equal(result.userId, 's-01', 'canonicalised before it was sent (contract 2.1)');
  assert.equal(result.templateId, 'tpl-1');
  assert.equal(result.quality, 0.87);
  assert.equal(result.liveness, 'passed');
  assert.equal(result.requestId, 'req-11112222');
  assert.equal(result.elapsedMs, (FRAME_COUNT - 1) * CAPTURE_MS + LANDING_MS);
  assert.equal(h.session.busy, false);
  assert.equal(h.streams[0]!.allStopped, true, 'the camera is released on the way out');
});

test('a verify runs end to end, and a non-match is a SUCCESS with match:false', async () => {
  const h = session();
  h.fetch.reply('/v1/verify', { body: { match: false, score: 0.2, threshold: 0.55, liveness: LIVE } });
  const result = await h.session.verify('s-01');
  assert.equal(result.ok, true);
  if (!result.ok) return;

  assert.equal(result.match, false);
  assert.equal(result.score, 0.2);
  assert.equal(result.threshold, 0.55);
});

test('the scan on the wire is a FaceScan v1 carrying this challenge verbatim', async () => {
  const h = session();
  await h.session.enroll('s-01');
  const post = h.fetch.calls.find((c) => c.url.endsWith('/v1/enroll'))!;
  const body = JSON.parse(String(post.init!.body)) as { user_id: string; facescan: string };
  assert.equal(body.user_id, 's-01');
  const scan = mpDecode(Buffer.from(body.facescan, 'base64')) as {
    version: number;
    frames: { jpeg_bytes: Uint8Array; ts_ms: number }[];
    challenge: Record<string, unknown>;
  };
  assert.equal(scan.version, 1);
  assert.equal(scan.frames.length, FRAME_COUNT);
  assert.equal(scan.challenge.nonce, NONCE);
  assert.equal(scan.challenge.action, 'MOVE_CLOSER');
  assert.deepEqual(scan.challenge.params, CHALLENGE.params);

  const gaps = scan.frames.slice(1).map((f, i) => f.ts_ms - scan.frames[i]!.ts_ms);
  assert.deepEqual(gaps, new Array(FRAME_COUNT - 1).fill(CAPTURE_MS));
});

test('the events arrive in the order a UI has to draw them', async () => {
  const h = session();
  await h.session.enroll('s-01');
  const types = h.events.map((e) => e.type);

  assert.deepEqual(types.slice(0, 5), ['camera', 'camera', 'instruction', 'phase', 'frame']);
  assert.equal(types.filter((x) => x === 'frame').length, FRAME_COUNT);
  assert.equal(types.filter((x) => x === 'result').length, 1);
  assert.equal(types.at(-1), 'result');
  const phases = h.events.filter((e) => e.type === 'phase').map((e) => e.phase);
  assert.deepEqual([...new Set(phases)], ['hold', 'move', 'landing']);
  const camera = h.events.filter((e) => e.type === 'camera').map((e) => e.state);
  assert.deepEqual(camera, ['opening', 'ready', 'stopped']);
});

test('the hold phase lasts the challenge\'s settle_ms, measured from the first frame', async () => {
  const h = session();
  const marks: { phase: string; at: number }[] = [];
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onEvent: (e) => { if (e.type === 'phase' || e.type === 'frame') marks.push({ phase: e.type === 'phase' ? e.phase : 'frame', at: h.clock.now() }); },
  });
  await s.enroll('s-01');
  const firstFrame = marks.find((m) => m.phase === 'frame')!.at;
  const move = marks.find((m) => m.phase === 'move')!.at;

  assert.ok(move - firstFrame >= CHALLENGE.params.settle_ms, 'moved at ' + (move - firstFrame));
  assert.ok(move - firstFrame < CHALLENGE.params.settle_ms + 100);
});

test('the instruction is awaited before the first frame, so the words are on screen', async () => {
  const h = session();
  let instructionShownAt = 0;
  let firstFrameAt = 0;
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onInstruction: () => new Promise<void>((r) => {
      h.env.setTimeout(() => { instructionShownAt = h.clock.now(); r(); }, 400);
    }),
    onEvent: (e) => { if (e.type === 'frame' && e.index === 1) firstFrameAt = h.clock.now(); },
  });
  await s.enroll('s-01');
  assert.ok(instructionShownAt > 0 && instructionShownAt <= firstFrameAt,
    'instruction at ' + instructionShownAt + ', first frame at ' + firstFrameAt);
});

test('an onInstruction that never resolves is capped, not waited on for ever', async () => {
  const h = session();
  const started = h.clock.now();
  let firstFrameAt = 0;
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onInstruction: () => new Promise<void>(() => undefined),
    onEvent: (e) => { if (e.type === 'frame' && e.index === 1) firstFrameAt = h.clock.now(); },
  });
  const result = await s.enroll('s-01');
  assert.equal(result.ok, true, 'the scan still happens');
  assert.ok(firstFrameAt - started >= READY_TIMEOUT_MS);
  assert.ok(firstFrameAt - started < READY_TIMEOUT_MS + 1000);
});

test('an onEvent or onInstruction that throws does not fail the scan', async () => {
  const h = session({
    onEvent: () => { throw new Error('UI bug'); },
    onInstruction: () => { throw new Error('UI bug'); },
  });
  const result = await h.session.enroll('s-01');
  assert.equal(result.ok, true);
});

test('without a faceProbe the flow still completes: the SDK ships no detector', async () => {
  const h = session();
  const result = await h.session.verify('s-01');
  assert.equal(result.ok, true);
  const guides = h.events.filter((e) => e.type === 'guide');
  assert.ok(guides.length > 0, 'the guide still phases');
  assert.deepEqual([...new Set(guides.map((g) => g.progress))], [null], 'and claims no progress');
});

test('a faceProbe drives the guide to done', async () => {
  const h = session();
  let baseline = 0.4;
  const probe = (): FaceProbe => {
    const elapsed = h.clock.now();
    void elapsed;
    return { height: baseline, lost: false };
  };
  const s = createFaceSession({ videoElement: h.video, env: h.env, faceProbe: probe, onEvent: (e) => h.events.push(e) });
  const pending = s.enroll('s-01');

  h.env.setTimeout(() => { baseline = 0.4 * Math.exp(CHALLENGE.params.target * 1.15) * 1.05; }, 3000);
  const result = await pending;
  assert.equal(result.ok, true);
  const done = h.events.filter((e) => e.type === 'guide' && e.phase === 'done');
  assert.ok(done.length > 0, 'the guide should have latched done');
});

test('a face lost for longer than the tolerance is FACE_LOST, and nothing is sent', async () => {
  const h = session();
  let lost = false;
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    faceProbe: () => ({ height: 0.4, lost }),
  });
  const pending = s.enroll('s-01');
  h.env.setTimeout(() => { lost = true; }, 2000);
  const failure = asFailure(await pending);
  assert.equal(failure.code, 'FACE_LOST');
  assert.equal(failure.retryable, true);

  assert.equal(failure.countsAsAttempt, false);
  assert.equal(failure.engineCode, null);
  assert.equal(h.fetch.calls.filter((c) => c.url.includes('enroll')).length, 0);
  assert.equal(h.streams[0]!.allStopped, true);
});

test('a brief loss inside the tolerance does not abort the attempt', async () => {
  const h = session();
  let lost = false;
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    faceProbe: () => ({ height: 0.4, lost }),
  });
  const pending = s.enroll('s-01');
  h.env.setTimeout(() => { lost = true; }, 1000);
  h.env.setTimeout(() => { lost = false; }, 1000 + FACE_LOST_MS - 200);
  assert.equal((await pending).ok, true);
});

test('a bad user id fails before the camera or the network is touched', async () => {
  for (const id of ['', '   ', 'has space', 'ünïcode', 'a'.repeat(65)]) {
    const h = session();
    const failure = asFailure(await h.session.enroll(id));
    assert.equal(failure.code, 'INVALID_USER_ID', JSON.stringify(id));
    assert.equal(failure.retryable, false);
    assert.equal(h.fetch.calls.length, 0, 'nothing was sent');
    assert.equal(h.streams.length, 0, 'the camera was never opened');
  }
});

async function cancelAt(at: SessionEvent['type'], index = 1): Promise<{ failure: Failure; h: ReturnType<typeof session> }> {
  const h = session();
  let seen = 0;
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onEvent: (e) => {
      h.events.push(e);
      if (e.type === at && ++seen === index) s.cancel('user-left');
    },
  });
  const failure = asFailure(await s.enroll('s-01'));
  return { failure, h };
}

for (const [at, index] of [['camera', 2], ['instruction', 1], ['frame', 1], ['frame', 12], ['uploading', 1]] as const) {
  test(`cancel at the ${at} event (#${index}) resolves CANCELLED and releases the camera`, async () => {
    const { failure, h } = await cancelAt(at, index);
    assert.equal(failure.code, 'CANCELLED');
    assert.equal(failure.reason, 'user-left');
    assert.equal(failure.countsAsAttempt, false);
    assert.equal(h.streams[0]!.allStopped, true);
  });
}

test('a cancel during the camera prompt leaves no stream running', async () => {
  const h = session();
  let release: ((s: MediaStream) => void) | null = null;
  h.onGetUserMedia(() => new Promise<MediaStream>((r) => { release = r; }));
  const s = createFaceSession({ videoElement: h.video, env: h.env });
  const pending = s.enroll('s-01');
  await Promise.resolve();
  s.cancel('user-left');

  const { fakeStream } = await import('./helpers.ts');
  const late = fakeStream();
  release!(late);
  const failure = asFailure(await pending);
  assert.equal(failure.code, 'CANCELLED');
  assert.equal(late.allStopped, true, 'a late stream for a dead run is still stopped');
});

test('a cancel while the challenge is in flight aborts the request', async () => {
  const h = session();
  h.fetch.reply('/v1/challenge', { body: CHALLENGE, delayMs: 4000 });
  const s = createFaceSession({ videoElement: h.video, env: h.env });
  const pending = s.enroll('s-01');
  h.env.setTimeout(() => s.cancel('user-left'), 100);
  const failure = asFailure(await pending);
  assert.equal(failure.code, 'CANCELLED');
});

test('a cancel while the scan is uploading aborts it in flight', async () => {
  const h = session();
  h.fetch.reply('/v1/enroll', { body: ENROLLED, delayMs: 5000 });
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onEvent: (e) => { if (e.type === 'uploading') s.cancel('user-left'); },
  });
  const failure = asFailure(await s.enroll('s-01'));
  assert.equal(failure.code, 'CANCELLED');
});

test('a second scan supersedes the first, and the first resolves CANCELLED', async () => {
  const h = session();
  const first = h.session.verify('s-01');
  const second = h.session.enroll('s-02');
  const failure = asFailure(await first);
  assert.equal(failure.code, 'CANCELLED');
  assert.equal(failure.reason, 'superseded');
  assert.equal(failure.op, 'verify', 'the superseded run keeps its own operation');
  const result = await second;
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.op, 'enroll');
});

test('a hidden page cancels the run, and unbinding stops that', async () => {
  const h = session();
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onEvent: (e) => { if (e.type === 'frame' && e.index === 2) { h.doc.hidden = true; h.doc.fire('visibilitychange'); } },
  });
  const failure = asFailure(await s.enroll('s-01'));
  assert.equal(failure.code, 'CANCELLED');
  assert.equal(failure.reason, 'hidden');

  const h2 = session({ cancelOnHide: false });
  h2.doc.hidden = true;
  const s2 = createFaceSession({
    videoElement: h2.video, env: h2.env, cancelOnHide: false,
    onEvent: (e) => { if (e.type === 'frame' && e.index === 2) h2.doc.fire('visibilitychange'); },
  });
  assert.equal((await s2.enroll('s-01')).ok, true, 'a caller that opted out is not interrupted');
});

test('dispose cancels the run, unbinds the listeners, and closes the session', async () => {
  const h = session();
  const before = h.doc.listenerCount + h.win.listenerCount;
  const s = createFaceSession({ videoElement: h.video, env: h.env });
  assert.ok(h.doc.listenerCount + h.win.listenerCount > before);
  const pending = s.enroll('s-01');
  await Promise.resolve();
  s.dispose();
  assert.equal(asFailure(await pending).code, 'CANCELLED');
  assert.equal(h.doc.listenerCount + h.win.listenerCount, before);
  const after = asFailure(await s.enroll('s-01'));
  assert.equal(after.code, 'CANCELLED');
  assert.equal(after.reason, 'disposed');
});

test('a camera track dying mid-run is CAMERA_ENDED, not a cancel the user made', async () => {
  const h = session();
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onEvent: (e) => { if (e.type === 'frame' && e.index === 3) h.streams[0]!.tracks[0]!.end(); },
  });
  const failure = asFailure(await s.enroll('s-01'));
  assert.equal(failure.code, 'CAMERA_ENDED');
  assert.equal(failure.retryable, true);
  assert.equal(failure.countsAsAttempt, false);
});

test('a nonce with too little life left to record is CHALLENGE_EXPIRED, and nothing is recorded', async () => {
  const h = session();

  h.fetch.reply('/v1/challenge', { body: { ...CHALLENGE, issued_ms: 0, expires_ms: 2000 } });
  const failure = asFailure(await h.session.enroll('s-01'));
  assert.equal(failure.code, 'CHALLENGE_EXPIRED');
  assert.equal(failure.retryable, true);
  assert.equal(h.events.some((e) => e.type === 'frame'), false, 'no frame was taken');
  assert.equal(h.fetch.calls.filter((c) => c.url.includes('enroll')).length, 0);
});

test('a phone that stalls through the recording sends nothing either', async () => {
  const h = session();
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    onEvent: (e) => {
      h.events.push(e);

      if (e.type === 'frame' && e.index === FRAME_COUNT) h.clock.skip(30_000);
    },
  });
  const failure = asFailure(await s.enroll('s-01'));
  assert.equal(failure.code, 'CHALLENGE_EXPIRED');
  assert.equal(h.fetch.calls.filter((c) => c.url.includes('enroll')).length, 0,
    'a dead nonce is never sent: it would spend the nonce and read like an attack');
});

test('a nonce with exactly the margin to spare is still used', async () => {
  const h = session();
  const life = (FRAME_COUNT - 1) * CAPTURE_MS + LANDING_MS + EXPIRY_MARGIN_MS;
  h.fetch.reply('/v1/challenge', { body: { ...CHALLENGE, issued_ms: 0, expires_ms: life } });
  assert.equal((await h.session.enroll('s-01')).ok, true);
});

test('a challenge the gateway will not mint is CHALLENGE_UNAVAILABLE', async () => {
  const h = session();
  h.fetch.reply('/v1/challenge', { status: 503, body: { error: { code: 'MODEL_UNAVAILABLE', message: 'x' } } });
  const failure = asFailure(await h.session.enroll('s-01'));
  assert.equal(failure.code, 'CHALLENGE_UNAVAILABLE');
  assert.equal(failure.engineCode, 'MODEL_UNAVAILABLE', 'the engine\'s own code, verbatim');
  assert.equal(h.events.some((e) => e.type === 'frame'), false);
});

test('a challenge the gateway mangles is BAD_RESPONSE, not a scan sent on a bad nonce', async () => {
  const h = session();
  h.fetch.reply('/v1/challenge', { body: { ...CHALLENGE, nonce: 'nope' } });
  assert.equal(asFailure(await h.session.enroll('s-01')).code, 'BAD_RESPONSE');
});

test('a contract 3.1 rejection passes its code through and says what it means for a retry', async () => {
  const cases: [string, number, boolean, boolean][] = [

    ['NO_FACE', 422, true, true],
    ['MULTI_FACE', 422, true, true],
    ['LIVENESS_FAIL', 422, true, true],
    ['CHALLENGE_FAIL', 422, true, true],
    ['LOW_QUALITY', 422, true, false],
    ['BUSY', 503, true, false],
    ['USER_NOT_FOUND', 404, false, false],
    ['MODEL_UNAVAILABLE', 503, false, false],
    ['ENGINE_UNREACHABLE', 502, true, false],
    ['UNAUTHORIZED', 401, false, false],
    ['MALFORMED_SCAN', 422, false, false],
    ['PAYLOAD_TOO_LARGE', 413, false, false],
  ];
  for (const [code, status, retryable, countsAsAttempt] of cases) {
    const h = session();
    h.fetch.reply('/v1/verify', { status, body: { error: { code, message: 'detail' } } });
    const failure = asFailure(await h.session.verify('s-01'));
    assert.equal(failure.code, 'ENGINE_REJECTED', code);
    assert.equal(failure.engineCode, code, 'never renamed');
    assert.equal(failure.httpStatus, status, code);
    assert.equal(failure.retryable, retryable, code + ' retryable');
    assert.equal(failure.countsAsAttempt, countsAsAttempt, code + ' countsAsAttempt');
  }
});

test('an unrecognised code is passed through, retryable and not counted', async () => {

  const h = session();
  h.fetch.reply('/v1/verify', { status: 422, body: { error: { code: 'SOMETHING_NEW', message: 'x' } } });
  const failure = asFailure(await h.session.verify('s-01'));
  assert.equal(failure.engineCode, 'SOMETHING_NEW');
  assert.equal(failure.retryable, false);
  assert.equal(failure.countsAsAttempt, false);
});

test('BUSY carries its Retry-After through to the caller', async () => {
  const h = session();
  h.fetch.reply('/v1/verify', {
    status: 503, body: { error: { code: 'BUSY', message: 'queue full' } },
    headers: { 'Retry-After': '2', 'X-Request-Id': 'req-55556666' },
  });
  const failure = asFailure(await h.session.verify('s-01'));
  assert.equal(failure.retryAfterMs, 2000);
  assert.equal(failure.requestId, 'req-55556666');
});

test('an engine that never answers in time is TIMEOUT', async () => {
  const h = session();
  h.fetch.reply('/v1/enroll', { body: ENROLLED, delayMs: SCAN_TIMEOUT_MS + 1000 });
  const failure = asFailure(await h.session.enroll('s-01'));
  assert.equal(failure.code, 'TIMEOUT');
  assert.equal(failure.retryable, true);
});

test('f13 headers that arrive but a body that stalls past the deadline is TIMEOUT, with the request id', async () => {
  const h = session();
  h.fetch.reply('/v1/verify', {
    body: MATCHED, headers: { 'X-Request-Id': 'req-13131313' }, bodyDelayMs: SCAN_TIMEOUT_MS + 1000,
  });
  const failure = asFailure(await h.session.verify('s-01'));
  assert.equal(failure.code, 'TIMEOUT');
  assert.equal(failure.retryable, true);
  assert.equal(failure.requestId, 'req-13131313');
  assert.equal(h.streams[0]!.allStopped, true);
});

test('f13 a user cancel while the body is still arriving is CANCELLED, not TIMEOUT or BAD_RESPONSE', async () => {
  const h = session();
  h.fetch.reply('/v1/verify', {
    body: MATCHED, headers: { 'X-Request-Id': 'req-13131314' }, bodyDelayMs: 2000,
    onBody: () => h.session.cancel('user-pressed-cancel'),
  });
  const failure = asFailure(await h.session.verify('s-01'));
  assert.equal(failure.code, 'CANCELLED');
  assert.equal(failure.reason, 'user-pressed-cancel');
  assert.equal(h.session.busy, false);
});

test('a dropped connection is NETWORK', async () => {
  const h = session();
  h.fetch.reply('/v1/enroll', { networkError: true });
  assert.equal(asFailure(await h.session.enroll('s-01')).code, 'NETWORK');
});

test('the camera is released after a liveness failure and after a network failure (f12)', async () => {

  const rejected = session();
  rejected.fetch.reply('/v1/verify', {
    status: 422, body: { error: { code: 'LIVENESS_FAIL', message: 'liveness check failed: challenge' } },
  });
  assert.equal(asFailure(await rejected.session.verify('s-01')).engineCode, 'LIVENESS_FAIL');
  assert.equal(rejected.streams[0]!.allStopped, true, 'after a liveness failure');
  const dropped = session();
  dropped.fetch.reply('/v1/enroll', { networkError: true });
  assert.equal(asFailure(await dropped.session.enroll('s-01')).code, 'NETWORK');
  assert.equal(dropped.streams[0]!.allStopped, true, 'after a network failure');
});

test('a capture that cannot be squeezed under the engine limit is SCAN_TOO_LARGE, unsent', async () => {
  const h = session({}, { canvasBytesAt: () => 60_000 });
  const failure = asFailure(await h.session.enroll('s-01'));
  assert.equal(failure.code, 'SCAN_TOO_LARGE');
  assert.equal(h.fetch.calls.filter((c) => c.url.includes('enroll')).length, 0);
  assert.equal(h.streams[0]!.allStopped, true);
});

test('a capture that needs the ladder still gets sent, under the hard limit', async () => {
  const h = session({}, { canvasBytesAt: (q) => Math.round(40_000 * q / 0.62) });
  const result = await h.session.enroll('s-01');
  assert.equal(result.ok, true);
  const post = h.fetch.calls.find((c) => c.url.endsWith('/v1/enroll'))!;
  const body = JSON.parse(String(post.init!.body)) as { facescan: string };
  assert.ok(Buffer.from(body.facescan, 'base64').length <= ENGINE_MAX_BYTES);
  const uploading = h.events.find((e) => e.type === 'uploading');
  assert.equal(uploading?.type === 'uploading' && uploading.bytes, Buffer.from(body.facescan, 'base64').length);
});

test('a refused camera never fetches a challenge: no nonce is burned', async () => {
  const h = session();
  h.onGetUserMedia(() => Promise.reject(Object.assign(new Error('no'), { name: 'NotAllowedError' })));
  const failure = asFailure(await h.session.enroll('s-01'));
  assert.equal(failure.code, 'CAMERA_DENIED');
  assert.equal(failure.retryable, false, 'a refusal is not fixed by trying the same thing again');
  assert.equal(h.fetch.calls.length, 0);
});

test('no result, event or failure message carries scan bytes, a user id or the nonce', async () => {
  const h = session();
  h.fetch.reply('/v1/verify', { status: 422, body: { error: { code: 'CHALLENGE_FAIL', message: 'nonce ' + NONCE } } });
  await h.session.verify('secret.user');
  const failure = asFailure(h.events.filter((e) => e.type === 'result').at(-1)!.result);

  assert.doesNotMatch(failure.message, /secret\.user/);
  assert.doesNotMatch(failure.message, new RegExp(NONCE));
  const serialised = JSON.stringify(h.events.filter((e) => e.type !== 'result'));
  assert.doesNotMatch(serialised, new RegExp(NONCE), 'the nonce never reaches an event');
  assert.doesNotMatch(serialised, /secret\.user/);
  assert.doesNotMatch(serialised, /facescan/i);
});

test('every canvas the attempt allocated is released by the time it resolves', async () => {
  const h = session();
  await h.session.enroll('s-01');
  const held = h.canvases.all.filter((c) => c.draws > 0 && (c.width > 0 || c.height > 0));
  assert.deepEqual(held, [], held.length + ' canvases still hold pixels');
});

test('busy reflects whether a run is live', async () => {
  const h = session();
  assert.equal(h.session.busy, false);
  const pending = h.session.enroll('s-01');
  assert.equal(h.session.busy, true);
  await pending;
  assert.equal(h.session.busy, false);
});

test('a gatewayUrl that is not allowed fails loudly at construction', () => {
  const h = session();
  assert.throws(() => createFaceSession({
    videoElement: h.video, env: h.env, gatewayUrl: 'http://gateway.example.com',
  }), /localhost only/);
});

test('a faceProbe that throws is treated as no measurement, not as a failed capture', async () => {

  const h = session();
  let calls = 0;
  const s = createFaceSession({
    videoElement: h.video, env: h.env, onEvent: (e) => h.events.push(e),
    faceProbe: () => {
      if (++calls > 5) throw new Error('detector blew up');
      return { height: 0.4, lost: false };
    },
  });
  const result = await s.enroll('s-01');
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.ok(calls > 5, 'the probe kept being called after it threw');
  const guides = h.events.filter((e) => e.type === 'guide');
  assert.ok(guides.length > 0, 'the guide still phased the recording');
});

test('a probe that goes unknown mid-recording never times out as FACE_LOST', async () => {

  const h = session();
  let probe: FaceProbe | null = { height: 0.4, lost: true };
  const s = createFaceSession({
    videoElement: h.video, env: h.env,
    faceProbe: () => probe,
  });
  const pending = s.enroll('s-01');

  h.env.setTimeout(() => { probe = null; }, 900);
  const result = await pending;
  assert.equal(result.ok, true, JSON.stringify(result));
});

test('the capture meta the UI supplies travels verbatim, and the SDK adds nothing to it', async () => {

  const h = session();
  const meta = { consent: 'storage-consent-v1', restarts: 1, subject: 'T07', label: 'print' };
  await h.session.verify('  S-01 ', { captureMeta: meta });
  const post = h.fetch.calls.find((c) => c.url.includes('/v1/verify'));
  assert.ok(post, 'no scan was sent');
  const header = (post.init!.headers as Record<string, string>)['X-Capture-Meta']!;
  assert.deepEqual(JSON.parse(header), meta);
  assert.doesNotMatch(header, /s-01/i);
});

for (const order of ['newer-first', 'older-first'] as const) {
  test(`a superseded camera request never takes the video from the current run (${order})`, async () => {
    const h = session();
    const { fakeStream } = await import('./helpers.ts');
    const gates: ((s: MediaStream) => void)[] = [];
    h.onGetUserMedia(() => new Promise<MediaStream>((r) => { gates.push(r); }));
    const older = fakeStream();
    const newer = fakeStream();
    let attachedAtInstruction: unknown = 'not reached';
    const s = createFaceSession({
      videoElement: h.video, env: h.env,
      onInstruction: () => { attachedAtInstruction = h.video.srcObject; },
    });
    const first = s.enroll('s-01');
    await Promise.resolve();
    const second = s.verify('s-01');
    await Promise.resolve();
    assert.equal(gates.length, 2);
    if (order === 'newer-first') { gates[1]!(newer); await Promise.resolve(); gates[0]!(older); }
    else { gates[0]!(older); await Promise.resolve(); gates[1]!(newer); }

    const failure = asFailure(await first);
    assert.equal(failure.code, 'CANCELLED');
    const result = await second;
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(attachedAtInstruction, newer, 'the current run recorded from its own stream');
    assert.equal(older.allStopped, true, 'the superseded stream was released');
    assert.equal(newer.allStopped, true, 'the current stream is released when its run ends');
  });
}

async function until(cond: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !cond(); i++) await new Promise((r) => setImmediate(r));
  assert.ok(cond(), 'condition never became true');
}

for (const end of ['cancel', 'superseded'] as const) {
  for (const late of ['resolves', 'rejects'] as const) {
    test(`a run ended (${end}) while play() is pending releases its camera at once; a late play that ${late} cannot touch the retry`, { timeout: 5000 }, async () => {
      const h = session();
      const { fakeStream } = await import('./helpers.ts');
      const older = fakeStream();
      const newer = fakeStream();
      const queue = [older, newer];
      h.onGetUserMedia(() => Promise.resolve(queue.shift()!));
      let settleOld: ((ok: boolean) => void) | null = null;
      h.video.play = () => new Promise<void>((res, rej) => {
        settleOld = (ok) => { if (ok) res(); else rej(Object.assign(new Error('interrupted'), { name: 'AbortError' })); };
      });
      let atInstruction: { attached: unknown; newerLive: number } | null = null;
      const s = createFaceSession({
        videoElement: h.video, env: h.env,
        onInstruction: async () => {
          settleOld!(late === 'resolves');
          for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
          atInstruction = { attached: h.video.srcObject, newerLive: newer.liveCount };
        },
      });

      const first = s.enroll('s-01');
      await until(() => settleOld !== null);
      assert.equal(h.video.srcObject, older);
      const oldSettle = settleOld!;
      h.video.play = () => Promise.resolve();

      let second: Promise<VerifyResult> | null = null;
      if (end === 'cancel') s.cancel('user-left');
      else second = s.verify('s-01');
      assert.equal(older.allStopped, true, 'the pending run\'s track is released immediately');
      if (end === 'cancel') assert.equal(h.video.srcObject, null, 'and detached from the video');

      const failure = asFailure(await first);
      assert.equal(failure.code, 'CANCELLED');
      if (end === 'cancel') assert.equal(h.clock.pending, 0, 'no startup timer is left running');

      settleOld = oldSettle;
      const result = await (second ?? s.verify('s-01'));
      assert.equal(result.ok, true, JSON.stringify(result));
      assert.deepEqual(atInstruction, { attached: newer, newerLive: 1 },
        'the late play of the first run left the retry\'s stream attached and running');
      assert.equal(newer.allStopped, true, 'the retry releases its own stream when it ends');
      assert.equal(h.fetch.calls.filter((c) => c.url.includes('/v1/challenge')).length, 1,
        'the ended run never fetched a challenge');
    });
  }
}

test('a play() that never settles fails the start at its deadline and releases the camera', { timeout: 5000 }, async () => {
  const h = session();
  h.video.play = () => new Promise<void>(() => undefined);
  const failure = asFailure(await h.session.enroll('s-01'));
  assert.equal(failure.code, 'CAMERA_UNAVAILABLE');
  assert.equal(h.streams[0]!.allStopped, true);
  assert.equal(h.video.srcObject, null);
  assert.equal(h.fetch.calls.length, 0, 'no nonce is burned on a camera that never started');
  assert.equal(h.clock.pending, 0);
  assert.equal(h.session.busy, false);
});

test('slow sequence refuses a nonce with enough time for the old burst but not the new one', async () => {
  const h = session();
  h.fetch.reply('/v1/challenge', { body: { ...CHALLENGE, action: 'HEAD_SEQUENCE',
    params: { settle_ms: 3200, switch_ms: 9200, first_sign: 1, target: .2 },
    issued_ms: 0, expires_ms: 12000 } });
  assert.equal(asFailure(await h.session.enroll('test')).code, 'CHALLENGE_EXPIRED');
  assert.equal(h.fetch.calls.filter(c => c.url.endsWith('/v1/enroll')).length, 0);
  assert.equal(h.events.filter(e => e.type === 'frame').length, 0);
});
