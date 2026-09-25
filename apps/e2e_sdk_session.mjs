import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { createFaceSession } from '../packages/face-sdk/dist/index.js';

const BASE = process.argv[2] || process.env.FACETECH_GATEWAY || 'http://127.0.0.1:8080';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(HERE, '..', 'engine', 'tests', 'fixtures');

const USER_ID = 'e2e.sdk.' + Date.now().toString(36);

let failures = 0;
const ok = (cond, what) => {
  console.log((cond ? 'PASS  ' : 'FAIL  ') + what);
  if (!cond) failures++;
};

function fixtureCanvases(action) {
  const dir = path.join(FIXTURES, {
    MOVE_CLOSER: 'live_closer', LOOK_LEFT: 'live_left', LOOK_RIGHT: 'live_right',
  }[action] ?? 'live_closer');
  const files = readdirSync(dir).filter((f) => f.endsWith('.jpg')).sort();
  if (files.length !== 12) {
    throw new Error(`${dir}: expected 12 frames, found ${files.length} ` +
      '(run engine/tests/fixtures/make_fixtures.py)');
  }
  const b64 = files.map((f) => readFileSync(path.join(dir, f)).toString('base64'));
  let created = 0;
  const factory = () => {
    const slot = created++;
    const cv = {
      width: 0,
      height: 0,
      getContext: () => ({ drawImage() {} }),

      toDataURL: () => 'data:image/jpeg;base64,' + (b64[(slot - 1) % 12] ?? b64[0]),
    };
    return cv;
  };
  factory.frames = () => created - 1;
  return factory;
}

function fakeStream() {
  const track = {
    stop() {},
    addEventListener() {},
    removeEventListener() {},
  };
  return { getTracks: () => [track] };
}

const fakeVideo = () => ({ videoWidth: 640, videoHeight: 480, srcObject: null, play: async () => {} });

const deadVideo = () => ({ videoWidth: 0, videoHeight: 0, srcObject: null, play: async () => {} });

function envFor(canvases, getUserMedia) {
  return {

    fetch: (...args) => fetch(...args),
    getUserMedia: getUserMedia ?? (async () => fakeStream()),
    createCanvas: canvases,
    deviceInfo: () => ({
      user_agent: 'facetech-e2e-sdk-session',
      screen: { w: 1280, h: 720 },
      tz_offset: new Date().getTimezoneOffset(),
    }),

    document: null,
    window: null,
  };
}

async function currentAction() {
  const res = await fetch(BASE + '/v1/challenge');
  if (!res.ok) throw new Error('GET /v1/challenge -> ' + res.status);
  const { action } = await res.json();
  return action;
}

function sessionWith(canvases, getUserMedia) {
  return createFaceSession({
    gatewayUrl: BASE,
    videoElement: fakeVideo(),
    env: envFor(canvases, getUserMedia),
  });
}

async function realCapture(op, action) {
  const canvases = fixtureCanvases(action);
  const phases = [];
  let frames = 0;
  const session = createFaceSession({
    gatewayUrl: BASE,
    videoElement: fakeVideo(),
    env: envFor(canvases),
    onEvent: (e) => {
      if (e.type === 'phase') phases.push(e.phase);
      if (e.type === 'frame') frames = e.index;
    },
  });
  const t0 = Date.now();
  const result = await session[op](USER_ID);
  const wall = Date.now() - t0;
  session.dispose();

  console.log(`\n${op}: ${JSON.stringify(result)}`);
  ok(result.ok === true, `${op} resolved ok (code=${result.code ?? '-'} engineCode=${result.engineCode ?? '-'})`);
  ok(frames === 12, `${op} emitted 12 frame events (got ${frames})`);
  ok(phases[0] === 'hold' && phases[phases.length - 1] === 'landing',
    `${op} phases ran hold -> ... -> landing (${phases.join(',')})`);
  ok(wall >= 6000, `${op} took a real recording's time (${wall}ms)`);
  ok(canvases.frames() === 12, `${op} grabbed exactly 12 frames (${canvases.frames()})`);
  if (result.ok) {
    ok(result.requestId !== null, `${op} surfaced a validated X-Request-Id (${result.requestId})`);
    ok(result.liveness === 'passed', `${op} liveness verdict is passed (${result.liveness})`);
    if (op === 'verify') ok(result.match === true, `verify matched (score=${result.score})`);
  }
  return result;
}

async function cancelMidCapture(action) {
  const session = sessionWith(fixtureCanvases(action));
  const pending = session.verify(USER_ID);

  setTimeout(() => session.cancel('e2e-cancel'), 1800);
  const result = await pending;
  session.dispose();
  console.log(`\ncancel: ${JSON.stringify(result)}`);
  ok(result.ok === false && result.code === 'CANCELLED', `cancel gave CANCELLED (${result.code})`);
  ok(result.reason === 'e2e-cancel', `cancel carried its own reason (${result.reason})`);
  ok(result.countsAsAttempt === false, 'a cancel does not count as an attempt');
}

async function cameraDenied() {
  const session = sessionWith(fixtureCanvases('MOVE_CLOSER'), async () => {
    throw Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' });
  });
  const result = await session.enroll(USER_ID);
  session.dispose();
  console.log(`\ndenied: ${JSON.stringify(result)}`);
  ok(result.ok === false && result.code === 'CAMERA_DENIED', `a refused camera gave CAMERA_DENIED (${result.code})`);
  ok(result.retryable === false, 'a refused camera is not retryable without the user changing something');

  ok(!result.message.includes(USER_ID), 'the failure message carries no user id');
}

async function cameraProducesNoFrames() {
  let stopped = 0;
  const session = createFaceSession({
    gatewayUrl: BASE,
    videoElement: deadVideo(),
    env: envFor(fixtureCanvases('MOVE_CLOSER'), async () => ({
      getTracks: () => [{ stop() { stopped++; }, addEventListener() {}, removeEventListener() {} }],
    })),
  });
  const t0 = Date.now();
  const result = await session.enroll(USER_ID);
  const wall = Date.now() - t0;
  session.dispose();
  console.log(`\nno frames: ${JSON.stringify(result)}`);
  ok(result.ok === false && result.code === 'CAMERA_UNAVAILABLE',
    `a camera that never delivers a frame gave CAMERA_UNAVAILABLE (${result.code})`);

  ok(wall >= 3000 && wall < 6000, `it gave up after about 3s, not never (${wall}ms)`);

  ok(stopped >= 1, `the granted stream's track was stopped (${stopped})`);
  ok(!result.message.includes(USER_ID), 'the failure message carries no user id');
}

const action = await currentAction();
console.log(`gateway=${BASE}  user_id=${USER_ID}  challenge action=${action}`);
await realCapture('enroll', action);
await realCapture('verify', await currentAction());
await cancelMidCapture(action);
await cameraDenied();
await cameraProducesNoFrames();

console.log(failures ? `\n${failures} check(s) FAILED` : '\nall checks passed');
process.exit(failures ? 1 : 0);
