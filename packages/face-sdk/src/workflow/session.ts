import {
  EXPIRY_MARGIN_MS, FACE_LOST_MS, LANDING_MS, READY_TIMEOUT_MS,
} from '../constants.ts';
import { createGuide, guideStep, instructionOf } from '../challenge/guide.ts';
import { CAPTURE_SPAN_MS, captureFrames, encodeFrames, releaseFrames } from '../camera/capture.ts';
import type { RawFrame } from '../camera/capture.ts';
import { openCamera } from '../camera/camera.ts';
import { packScan } from '../encoding/scan.ts';
import {
  canonicalUserId, getChallenge, isValidUserId, postScan, resolveGatewayUrl,
} from '../transport/gateway.ts';
import type { EnrollBody, GatewayConfig, TransportErr, VerifyBody } from '../transport/gateway.ts';
import { codedError, isCodedError } from '../types.ts';
import type {
  CapturePhase, DeviceInfo, EnrollResult, Environment, Failure, FailureCode, FaceProbe,
  FaceSession, FaceSessionOptions, Operation, ScanOptions, SessionEvent, VerifyResult,
} from '../types.ts';
import {
  assertLive, bindLifecycle, createRunController, isCancelled, sleep, withCap,
} from './run.ts';
import type { Run } from './run.ts';

export function createFaceSession(options: FaceSessionOptions): FaceSession {
  const env = resolveEnvironment(options.env);
  const cfg: GatewayConfig = { base: resolveGatewayUrl(options.gatewayUrl), env };
  const video = options.videoElement;
  let disposed = false;

  // Cancellation reason per run id, not one shared variable: a superseding
  // start() cancels the previous run synchronously, before that run's own
  // catch has a chance to read why. Keyed storage lets each run read only
  // its own reason, however many runs start and finish around it.
  const stopInfo = new Map<number, { code: FailureCode | null; reason: string }>();

  const ctl = createRunController({
    onCancel: (run, reason) => {
      if (!stopInfo.has(run.id)) stopInfo.set(run.id, { code: null, reason });
    },
  });
  const unbind = options.cancelOnHide === false || !env.document || !env.window
    ? null
    : bindLifecycle(env.document, env.window, (reason) => {
      if (ctl.current()) ctl.cancel(reason);
    });

  function emit(event: SessionEvent): void {
    try { options.onEvent?.(event); } catch {                                        }
  }

  async function run<T>(op: Operation, rawId: string, scan: ScanOptions | undefined): Promise<T> {
    const result = await runScan(op, rawId, scan);
    emit({ type: 'result', result });
    return result as T;
  }

  async function runScan(
    op: Operation,
    rawId: string,
    scan: ScanOptions | undefined,
  ): Promise<EnrollResult | VerifyResult> {
    if (disposed) return fail(op, 'CANCELLED', 'the session has been disposed', { reason: 'disposed' });
    const userId = canonicalUserId(String(rawId));

    if (!isValidUserId(userId)) {
      return fail(op, 'INVALID_USER_ID', 'user_id must match [a-z0-9._-]{1,64} after trimming and lowercasing');
    }
    const token = ctl.start(op, userId);
    const ac = new AbortController();

    ctl.own(token, () => ac.abort());
    try {
      return await ctl.guard(token, (r) => attempt(r, ac.signal, scan));
    } catch (e) {

      return fail(op, 'BAD_RESPONSE', 'unexpected SDK error: ' + (e instanceof Error ? e.name : 'unknown'));
    }
  }

  async function attempt(
    token: Run,
    signal: AbortSignal,
    scan: ScanOptions | undefined,
  ): Promise<EnrollResult | VerifyResult> {
    const op = token.op;
    let frames: RawFrame[] | null = null;
    try {
      emit({ type: 'camera', state: 'opening' });
      const cam = await openCamera(env, video, () => {

        if (!ctl.live(token)) return;
        stopInfo.set(token.id, { code: 'CAMERA_ENDED', reason: 'camera-ended' });
        ctl.cancel('camera-ended');
      }, () => ctl.live(token), signal);

      if (!ctl.own(token, () => { cam.stop(); emit({ type: 'camera', state: 'stopped' }); })) {
        assertLive(ctl, token);
      }
      assertLive(ctl, token);
      emit({ type: 'camera', state: 'ready' });

      const got = await getChallenge(cfg, signal, token.op, token.userId);
      assertLive(ctl, token);
      if (!got.ok) return fromTransport(op, got, 'CHALLENGE_UNAVAILABLE');
      const challenge = got.value;

      const receivedAt = env.now();
      const remaining = (): number => challenge.lifeMs - (env.now() - receivedAt);

      const instruction = instructionOf(challenge);
      emit({ type: 'instruction', instruction });
      await withCap(env, READY_TIMEOUT_MS, () => options.onInstruction?.(instruction));
      assertLive(ctl, token);

      const sequence = challenge.action === 'HEAD_SEQUENCE';
      if (remaining() < (sequence ? 15400 : CAPTURE_SPAN_MS) + LANDING_MS + EXPIRY_MARGIN_MS) {
        return fail(op, 'CHALLENGE_EXPIRED', 'too little of the challenge window left to record a scan');
      }

      const readProbe = (): FaceProbe | null => {
        try { return options.faceProbe?.() ?? null; } catch { return null; }
      };

      const guide = createGuide(challenge);
      let phase: CapturePhase = 'hold';
      emit({ type: 'phase', phase });
      let faceLostSince = 0;
      let lastCue = '';

      frames = await captureFrames({
        env, video,
        intervalMs: sequence ? 1400 : undefined,
        onFrame: (index, total) => emit({ type: 'frame', index, total }),
        onTick: (elapsed) => {
          const probe = readProbe();
          if (sequence) {
            const settle = challenge.params?.['settle_ms'] ?? 3200;
            const change = challenge.params?.['switch_ms'] ?? 9000;
            const sign = challenge.params?.['first_sign'] ?? 1;
            const direction = (elapsed < change ? sign : -sign) > 0 ? 'LEFT' : 'RIGHT';
            const cue = elapsed <= settle ? 'Face forward. Hold still.' : `Slowly turn your head a little to your ${direction}, then hold. Keep both eyes visible and the phone still.`;
            if (cue !== lastCue) { emit({ type: 'action', text: cue }); lastCue = cue; }
            if (probe?.lost === true) { if (!faceLostSince) faceLostSince = env.now(); } else faceLostSince = 0;
            return;
          }
          guideStep(guide, elapsed, probe?.height ?? null);
          const next: CapturePhase = guide.phase === 'hold' ? 'hold' : 'move';
          if (next !== phase) { phase = next; emit({ type: 'phase', phase }); }
          emit({ type: 'guide', phase: guide.phase, progress: guide.progress });

          if (probe?.lost === true) { if (!faceLostSince) faceLostSince = env.now(); } else faceLostSince = 0;
        },
        shouldAbort: () => {
          if (!ctl.live(token)) return stopInfo.get(token.id)?.code ?? 'CANCELLED';
          if (faceLostSince && env.now() - faceLostSince > FACE_LOST_MS) return 'FACE_LOST';
          return null;
        },
      });
      assertLive(ctl, token);
      const firstFrameAt = frames[0]!.ts;

      emit({ type: 'phase', phase: 'landing' });
      await sleep(env, LANDING_MS, signal);
      assertLive(ctl, token);

      if (remaining() < EXPIRY_MARGIN_MS) {
        return fail(op, 'CHALLENGE_EXPIRED', 'the challenge would expire before the scan arrived');
      }

      const captured = frames;
      const packed = packScan(
        (quality, longEdge, step) => encodeFrames(env, captured, quality, longEdge, step === 0),
        env.deviceInfo(), env.randomId(), challenge,
      );

      releaseFrames(frames);
      frames = null;
      assertLive(ctl, token);

      emit({ type: 'uploading', bytes: packed.size });
      const sent = await postScan(cfg, op, token.userId, packed.b64, scan?.captureMeta ?? null, signal);
      assertLive(ctl, token);
      if (!sent.ok) return fromTransport(op, sent, 'ENGINE_REJECTED');

      const elapsedMs = Math.round(env.now() - firstFrameAt);
      if (op === 'enroll') {
        const body = sent.value as EnrollBody;
        return {
          ok: true, op: 'enroll', userId: token.userId, liveness: body.liveness,
          requestId: sent.requestId, elapsedMs,
          templateId: body.templateId, quality: body.quality,
        };
      }
      const body = sent.value as VerifyBody;
      return {
        ok: true, op: 'verify', userId: token.userId, liveness: body.liveness,
        requestId: sent.requestId, elapsedMs,
        match: body.match, score: body.score, threshold: body.threshold,
      };
    } catch (e) {
      const info = stopInfo.get(token.id);
      stopInfo.delete(token.id);
      if (isCancelled(e)) {
        return fail(op, info?.code ?? 'CANCELLED', 'the attempt was stopped before it finished',
          { reason: info?.reason ?? 'cancelled' });
      }
      if (isCodedError(e)) {
        return fail(op, e.sdkCode, e.message, e.sdkCode === 'CANCELLED' ? { reason: info?.reason ?? 'cancelled' } : undefined);
      }
      throw e;
    } finally {
      if (frames) releaseFrames(frames);
      frames = null;
    }
  }

  return {
    enroll(userId, scan) { return run<EnrollResult>('enroll', userId, scan); },
    verify(userId, scan) { return run<VerifyResult>('verify', userId, scan); },
    cancel(reason) { ctl.cancel(reason ?? 'cancelled'); },
    dispose() {
      disposed = true;
      ctl.cancel('disposed');
      unbind?.();
    },
    get busy() { return ctl.current() !== null; },
  };
}

const ENGINE_RETRYABLE = new Set([
  'NO_FACE', 'MULTI_FACE', 'LIVENESS_FAIL', 'CHALLENGE_FAIL', 'LOW_QUALITY', 'BUSY',
  'ENGINE_UNREACHABLE',
]);
const ENGINE_COUNTS_AS_ATTEMPT = new Set([
  'NO_FACE', 'MULTI_FACE', 'LIVENESS_FAIL', 'CHALLENGE_FAIL',
]);

const SDK_RETRYABLE: ReadonlySet<FailureCode> = new Set<FailureCode>([
  'CAMERA_ENDED', 'FACE_LOST', 'CHALLENGE_UNAVAILABLE', 'CHALLENGE_EXPIRED',
  'NETWORK', 'TIMEOUT', 'SCAN_TOO_LARGE',
]);

function fail(
  op: Operation,
  code: FailureCode,
  message: string,
  extra?: Partial<Pick<Failure, 'engineCode' | 'httpStatus' | 'retryAfterMs' | 'requestId' | 'reason'>>,
): Failure {
  const engineCode = extra?.engineCode ?? null;
  return {
    ok: false, op, code, message,
    retryable: engineCode !== null ? ENGINE_RETRYABLE.has(engineCode) : SDK_RETRYABLE.has(code),
    countsAsAttempt: engineCode !== null && ENGINE_COUNTS_AS_ATTEMPT.has(engineCode),
    engineCode,
    httpStatus: extra?.httpStatus ?? null,
    retryAfterMs: extra?.retryAfterMs ?? null,
    requestId: extra?.requestId ?? null,
    reason: extra?.reason ?? null,
  };
}

function fromTransport(op: Operation, e: TransportErr, rejectedAs: FailureCode): Failure {
  const code: FailureCode = e.kind === 'TIMEOUT' ? 'TIMEOUT'
    : e.kind === 'NETWORK' ? 'NETWORK'
      : e.kind === 'CANCELLED' ? 'CANCELLED'
        : e.kind === 'BAD_RESPONSE' ? 'BAD_RESPONSE'
          : rejectedAs;
  return fail(op, code, e.message, {
    engineCode: e.engineCode, httpStatus: e.httpStatus,
    retryAfterMs: e.retryAfterMs, requestId: e.requestId,
  });
}

function resolveEnvironment(over: Partial<Environment> | undefined): Environment {
  const g = globalThis as typeof globalThis & {
    navigator?: Navigator; document?: Document; window?: Window;
  };
  const base: Environment = {
    now: () => (typeof performance === 'undefined' ? Date.now() : performance.now()),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
    fetch: (...args) => fetch(...args),
    getUserMedia: (constraints) => {
      const md = g.navigator?.mediaDevices;

      if (!md) return Promise.reject(codedError('CAMERA_UNAVAILABLE', 'navigator.mediaDevices is not available'));
      return md.getUserMedia(constraints);
    },
    createCanvas: () => {
      const doc = g.document;
      if (!doc) throw codedError('CAMERA_UNAVAILABLE', 'no document to create a canvas in');
      return doc.createElement('canvas');
    },
    deviceInfo: () => browserDeviceInfo(g),
    randomId: () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID() : 'ch-' + Date.now().toString(36)),
    document: g.document ?? null,
    window: g.window ?? null,
  };
  return over ? { ...base, ...definedOnly(over) } : base;
}

function definedOnly(over: Partial<Environment>): Partial<Environment> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(over)) if (v !== undefined) out[k] = v;
  return out as Partial<Environment>;
}

function browserDeviceInfo(g: { navigator?: Navigator; window?: Window }): DeviceInfo {
  return {
    user_agent: g.navigator?.userAgent ?? '',
    screen: {
      w: Math.round(g.window?.innerWidth ?? 0),
      h: Math.round(g.window?.innerHeight ?? 0),
    },
    tz_offset: new Date().getTimezoneOffset(),
  };
}
