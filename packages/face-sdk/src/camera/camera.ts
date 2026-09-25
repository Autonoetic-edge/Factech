import { CAMERA_CONSTRAINTS, CAMERA_READY_MS, CAMERA_READY_POLL_MS } from '../constants.ts';
import { codedError } from '../types.ts';
import type { Environment } from '../types.ts';

export interface Camera {
  readonly stream: MediaStream;

  stop(): void;
}

const DENIED = new Set(['NotAllowedError', 'PermissionDeniedError', 'SecurityError']);

function errorName(e: unknown): string {
  return e instanceof Error ? e.name : '';
}

export async function openCamera(
  env: Environment,
  video: HTMLVideoElement,
  onEnded: () => void,
  owned: () => boolean = () => true,
  signal?: AbortSignal,
): Promise<Camera> {
  let stream: MediaStream;
  try {
    stream = await env.getUserMedia(CAMERA_CONSTRAINTS);
  } catch (e) {
    const name = errorName(e);
    if (DENIED.has(name)) throw codedError('CAMERA_DENIED', 'camera permission refused (' + name + ')');
    throw codedError('CAMERA_UNAVAILABLE', 'camera unavailable' + (name ? ' (' + name + ')' : ''));
  }

  const tracks = stream.getTracks();
  if (!owned() || signal?.aborted) {
    for (const t of tracks) t.stop();
    throw codedError('CANCELLED', 'the camera arrived after its attempt ended');
  }
  let stopped = false;
  const ended = (): void => { if (!stopped) onEnded(); };
  for (const t of tracks) t.addEventListener('ended', ended);

  const cam: Camera = {
    stream,
    stop(): void {
      if (stopped) return;
      stopped = true;
      for (const t of tracks) { t.removeEventListener('ended', ended); t.stop(); }

      if (video.srcObject === stream) video.srcObject = null;
    },
  };

  const abort = (): void => cam.stop();
  signal?.addEventListener('abort', abort, { once: true });
  try {
    video.srcObject = stream;
    const deadline = env.now() + CAMERA_READY_MS;
    await startPlaying(env, video, deadline, signal);
    await waitForFrames(env, video, () => owned() && !stopped, deadline);
  } catch (e) {
    cam.stop();
    throw e;
  } finally {
    signal?.removeEventListener('abort', abort);
  }
  return cam;
}

function startPlaying(
  env: Environment,
  video: HTMLVideoElement,
  deadline: number,
  signal: AbortSignal | undefined,
): Promise<void> {
  let played: Promise<unknown>;
  try { played = Promise.resolve(video.play()); } catch { played = Promise.resolve(); }
  return new Promise<void>((resolve, reject) => {
    let done = false;
    let timer: unknown = null;
    const settle = (err: Error | null): void => {
      if (done) return;
      done = true;
      if (timer !== null) env.clearTimeout(timer);
      signal?.removeEventListener('abort', cancelled);
      if (err) reject(err); else resolve();
    };
    const cancelled = (): void => settle(codedError('CANCELLED', 'the attempt ended while the camera was starting'));
    if (signal?.aborted) { cancelled(); return; }
    signal?.addEventListener('abort', cancelled, { once: true });
    timer = env.setTimeout(() => {
      timer = null;
      settle(codedError('CAMERA_UNAVAILABLE', 'camera did not start playing within ' + CAMERA_READY_MS + 'ms'));
    }, Math.max(0, deadline - env.now()));
    played.then(() => settle(null), () => settle(null));
  });
}

async function waitForFrames(
  env: Environment,
  video: HTMLVideoElement,
  owned: () => boolean,
  deadline: number,
): Promise<void> {
  for (;;) {
    if (!owned()) throw codedError('CANCELLED', 'the attempt ended while the camera was starting');
    if (video.videoWidth > 0 && video.videoHeight > 0) return;
    if (env.now() >= deadline) {
      throw codedError('CAMERA_UNAVAILABLE',
        'camera produced no frames within ' + CAMERA_READY_MS + 'ms');
    }
    await new Promise<void>((resolve) => { env.setTimeout(resolve, CAMERA_READY_POLL_MS); });
  }
}
