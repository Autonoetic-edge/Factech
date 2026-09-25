import assert from 'node:assert/strict';
import { test } from 'node:test';

import { openCamera } from '../src/camera/camera.ts';
import { CAPTURE_SPAN_MS, captureFrames, encodeFrames, jpegOf, releaseFrames } from '../src/camera/capture.ts';
import { CAMERA_READY_MS, CAPTURE_MS, FRAME_COUNT, JPEG_STEPS, LONG_EDGE } from '../src/constants.ts';
import { isCodedError } from '../src/types.ts';
import type { FailureCode } from '../src/types.ts';
import { fakeStream, testEnv } from './helpers.ts';

const noop = (): void => undefined;

async function codeOf(work: () => Promise<unknown>): Promise<FailureCode | 'no-throw'> {
  try { await work(); return 'no-throw'; } catch (e) {
    assert.ok(isCodedError(e), 'expected a coded SDK error, got ' + String(e));
    return e.sdkCode;
  }
}

test('a refused permission is CAMERA_DENIED', async () => {
  for (const name of ['NotAllowedError', 'PermissionDeniedError', 'SecurityError']) {
    const t = testEnv();
    t.onGetUserMedia(() => Promise.reject(Object.assign(new Error('no'), { name })));
    assert.equal(await codeOf(() => openCamera(t.env, t.video, noop)), 'CAMERA_DENIED', name);
  }
});

test('anything else getUserMedia throws is CAMERA_UNAVAILABLE, not a false accusation', async () => {

  for (const name of ['NotFoundError', 'NotReadableError', 'OverconstrainedError', 'AbortError', 'TypeError']) {
    const t = testEnv();
    t.onGetUserMedia(() => Promise.reject(Object.assign(new Error('no'), { name })));
    assert.equal(await codeOf(() => openCamera(t.env, t.video, noop)), 'CAMERA_UNAVAILABLE', name);
  }
});

test('opening attaches the stream to the element the caller handed us, and plays it', async () => {
  const t = testEnv();
  const cam = await openCamera(t.env, t.video, noop);
  assert.equal(t.video.srcObject, cam.stream);
  assert.equal(t.video.plays, 1);
});

test('a play() the browser refuses is not fatal while frames still arrive', async () => {
  const t = testEnv();
  t.video.playRejects = true;
  const cam = await openCamera(t.env, t.video, noop);
  assert.ok(cam.stream);
});

test('a stream that never produces a frame is CAMERA_UNAVAILABLE, and is stopped', async () => {

  const t = testEnv();
  t.video.videoWidth = 0;
  t.video.videoHeight = 0;
  assert.equal(await codeOf(() => openCamera(t.env, t.video, noop)), 'CAMERA_UNAVAILABLE');
  assert.equal(t.streams[0]!.allStopped, true, 'the stream must not be left running');
});

test('the readiness wait gives up at its deadline rather than hanging', async () => {
  const t = testEnv();
  t.video.videoWidth = 0;
  const before = t.clock.now();
  await codeOf(() => openCamera(t.env, t.video, noop));
  assert.ok(t.clock.now() - before >= CAMERA_READY_MS);
});

test('stop() ends every track, detaches the element, and is idempotent', async () => {
  const t = testEnv();
  t.onGetUserMedia(() => {
    const s = fakeStream(2);
    t.streams.push(s);
    return Promise.resolve(s);
  });
  const cam = await openCamera(t.env, t.video, noop);
  cam.stop();
  cam.stop();
  assert.equal(t.streams[0]!.liveCount, 0);
  assert.equal(t.video.srcObject, null);
});

test('a track ending mid-run calls back once, and never after stop()', async () => {
  const t = testEnv();
  let ended = 0;
  const cam = await openCamera(t.env, t.video, () => { ended++; });
  t.streams[0]!.tracks[0]!.end();
  assert.equal(ended, 1);
  cam.stop();
  t.streams[0]!.tracks[0]!.end();
  assert.equal(ended, 1, 'a stopped camera reporting "ended" is us, not the device');
});

function captureDeps(t: ReturnType<typeof testEnv>, over: Partial<Parameters<typeof captureFrames>[0]> = {}) {
  return {
    env: t.env, video: t.video,
    onFrame: () => undefined,
    onTick: () => undefined,
    shouldAbort: () => null,
    ...over,
  };
}

test('a capture is 12 frames, one cadence apart, with no gap after the last', async () => {
  const t = testEnv();
  const started = t.clock.now();
  const frames = await captureFrames(captureDeps(t));
  assert.equal(frames.length, FRAME_COUNT);
  const span = frames[FRAME_COUNT - 1]!.ts - frames[0]!.ts;
  assert.equal(span, (FRAME_COUNT - 1) * CAPTURE_MS);
  assert.equal(span, CAPTURE_SPAN_MS);

  assert.equal(t.clock.now() - started, CAPTURE_SPAN_MS);
  releaseFrames(frames);
});

test('frames are ordered and reported 1-based to the UI', async () => {
  const t = testEnv();
  const seen: number[] = [];
  const frames = await captureFrames(captureDeps(t, {
    onFrame: (index, total) => { seen.push(index); assert.equal(total, FRAME_COUNT); },
  }));
  assert.deepEqual(seen, Array.from({ length: FRAME_COUNT }, (_, i) => i + 1));
  for (let i = 1; i < frames.length; i++) assert.ok(frames[i]!.ts > frames[i - 1]!.ts);
  releaseFrames(frames);
});

test('the tick carries ms since the FIRST FRAME, which is what the engine measures from', async () => {
  const t = testEnv();
  const ticks: number[] = [];
  const frames = await captureFrames(captureDeps(t, { onTick: (ms) => ticks.push(ms) }));
  assert.equal(ticks[0], 0, 'the first tick is at the first frame, not before it');
  assert.equal(Math.max(...ticks), CAPTURE_SPAN_MS);

  const gaps = ticks.slice(1).map((v, i) => v - ticks[i]!);
  assert.ok(Math.max(...gaps) <= 100, 'largest tick gap was ' + Math.max(...gaps));
  releaseFrames(frames);
});

test('frames are downscaled to the long edge and pre-encoded at step 0', async () => {
  const t = testEnv();
  t.video.videoWidth = 1280;
  t.video.videoHeight = 720;
  const frames = await captureFrames(captureDeps(t));
  assert.equal(frames[0]!.canvas.width, LONG_EDGE);
  assert.equal(frames[0]!.canvas.height, Math.round(720 * LONG_EDGE / 1280));
  assert.ok(frames[0]!.jpeg0, 'the gap should have been spent encoding');
  releaseFrames(frames);
});

test('encodeFrames reuses the pre-encoded bytes only when asked', async () => {
  const t = testEnv();
  const frames = await captureFrames(captureDeps(t));
  const [q0, e0] = JPEG_STEPS[0]!;
  const reused = encodeFrames(t.env, frames, q0, e0, true);
  assert.equal(reused[0]!.bytes, frames[0]!.jpeg0, 'the same buffer, not a copy');
  const fresh = encodeFrames(t.env, frames, JPEG_STEPS[3]![0], JPEG_STEPS[3]![1], false);
  assert.notEqual(fresh[0]!.bytes, frames[0]!.jpeg0);
  assert.notEqual(fresh[0]!.bytes.length, reused[0]!.bytes.length, 'a lower step is a different size');
  releaseFrames(frames);
});

test('an abort request stops the capture and drops every frame taken so far', async () => {
  for (const code of ['CANCELLED', 'FACE_LOST'] as FailureCode[]) {
    const t = testEnv();
    let n = 0;
    const deps = captureDeps(t, {
      onFrame: () => { n++; },
      shouldAbort: () => (n >= 4 ? code : null),
    });
    assert.equal(await codeOf(() => captureFrames(deps)), code);

    const live = t.canvases.all.filter((c) => c.draws > 0 && (c.width > 0 || c.height > 0));
    assert.deepEqual(live, [], 'canvases were left allocated after an aborted capture');
  }
});

test('releaseFrames frees the canvases and the pre-encoded bytes', async () => {
  const t = testEnv();
  const frames = await captureFrames(captureDeps(t));
  releaseFrames(frames);
  for (const f of frames) {
    assert.equal(f.canvas.width, 0);
    assert.equal(f.canvas.height, 0);
    assert.equal(f.jpeg0, null);
  }
});

test('a canvas with no 2d context is a camera failure, not a silent blank frame', async () => {
  const t = testEnv();
  const broken = { width: 0, height: 0, getContext: () => null, toDataURL: () => '' };
  const env = { ...t.env, createCanvas: () => broken as unknown as HTMLCanvasElement };
  assert.equal(await codeOf(() => captureFrames(captureDeps({ ...t, env } as typeof t))), 'CAMERA_UNAVAILABLE');
});

test('jpegOf downscales only when the source is over the long edge', () => {
  const t = testEnv();
  const cv = t.env.createCanvas();
  cv.width = 640; cv.height = 480;
  const before = t.canvases.all.length;
  jpegOf(t.env, cv, 0.5, 480);
  assert.equal(t.canvases.all.length, before + 1, 'one scratch canvas for the downscale');
  assert.equal(t.canvases.all[before]!.width, 0, 'and it is released again');
  jpegOf(t.env, cv, 0.5, 640);
  assert.equal(t.canvases.all.length, before + 1, 'no downscale needed, no canvas made');
});

test('jpegOf returns the JPEG bytes, not a data URL', () => {
  const t = testEnv();
  const cv = t.env.createCanvas();
  cv.width = 100; cv.height = 100;
  const bytes = jpegOf(t.env, cv, 0.5, 480);
  assert.equal(bytes[0], 0xff);
  assert.equal(bytes[1], 0xd8);
});

test('a stream that arrives for an attempt that no longer owns the camera is stopped and never attached', async () => {
  const t = testEnv();
  const current = fakeStream();
  t.video.srcObject = current;
  assert.equal(await codeOf(() => openCamera(t.env, t.video, noop, () => false)), 'CANCELLED');
  assert.equal(t.streams[0]!.allStopped, true);
  assert.equal(t.video.srcObject, current, 'the newer attempt keeps its video');
  assert.equal(t.video.plays, 0);
});

test('losing ownership while waiting for frames stops the stream without detaching a newer one', async () => {
  const t = testEnv();
  t.video.videoWidth = 0;
  let owned = true;
  const newer = fakeStream();
  t.video.play = () => { t.video.srcObject = newer; owned = false; return Promise.resolve(); };
  assert.equal(await codeOf(() => openCamera(t.env, t.video, noop, () => owned)), 'CANCELLED');
  assert.equal(t.streams[0]!.allStopped, true);
  assert.equal(t.video.srcObject, newer);
});

test('an abort while play() is pending stops the stream at once and leaves a newer one attached', { timeout: 5000 }, async () => {
  const t = testEnv();
  const ac = new AbortController();
  let played = false;
  t.video.play = () => { played = true; return new Promise<void>(() => undefined); };
  const opening = codeOf(() => openCamera(t.env, t.video, noop, () => !ac.signal.aborted, ac.signal));
  for (let i = 0; i < 200 && !played; i++) await new Promise((r) => setImmediate(r));
  assert.ok(played);
  const newer = fakeStream();
  t.video.srcObject = newer;
  ac.abort();
  assert.equal(t.streams[0]!.allStopped, true);
  assert.equal(t.video.srcObject, newer);
  assert.equal(await opening, 'CANCELLED');
  assert.equal(t.clock.pending, 0);
});

test('a play() rejected after the start deadline has already failed the start leaves no unhandled rejection', { timeout: 5000 }, async () => {
  const t = testEnv();
  let reject: ((e: Error) => void) | null = null;
  t.video.play = () => new Promise<void>((_res, rej) => { reject = rej; });
  assert.equal(await codeOf(() => openCamera(t.env, t.video, noop)), 'CAMERA_UNAVAILABLE');
  assert.equal(t.streams[0]!.allStopped, true);
  assert.equal(t.video.srcObject, null);
  reject!(new Error('late'));
  await new Promise((r) => setImmediate(r));
  assert.equal(t.clock.pending, 0);
});
