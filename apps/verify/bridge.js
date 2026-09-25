// Adapter between the frozen capture SDK and the account-bound /v2 gateway.
// The SDK source is not edited; it already accepts `env.fetch` and
// `env.getUserMedia`, and this file supplies both.
//
//  camera:    one owner. "Enable camera" opens the stream; Start hands the SDK a
//             clone of it (same device, no second permission prompt), which the SDK
//             owns and stops. The page keeps the original for the preview, so a
//             restart needs no getUserMedia, no black frame, no camera-light blink.
//  transport: the SDK's /v1 calls are re-addressed to /v2/subjects/{me}/..., with
//             the session's CSRF token and one Idempotency-Key per scan. The
//             browser never says who it is; the path is checked against the
//             session by the gateway.
//  result:    the raw /v2 reply is kept for outcome.js. The page never shows the
//             SDK's view of the result.

import { glowOf } from './glow.js';

// Same request the SDK itself makes (packages/face-sdk/src/camera/camera.ts).
const CAMERA_CONSTRAINTS = {
  video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
  audio: false,
};

const DENIED = new Set(['NotAllowedError', 'PermissionDeniedError', 'SecurityError']);

export function createCameraOwner({ mediaDevices, video, onEnded }) {
  let stream = null;

  const live = () => !!stream && stream.getTracks().some((t) => t.readyState !== 'ended');
  const ended = () => { if (stream) { stop(); onEnded?.(); } };

  async function open() {
    if (live()) return;
    if (!mediaDevices?.getUserMedia) throw Object.assign(new Error('no camera API'), { cameraError: 'nocamera' });
    let s;
    try {
      s = await mediaDevices.getUserMedia(CAMERA_CONSTRAINTS);
    } catch (e) {
      throw Object.assign(new Error('camera failed'), { cameraError: DENIED.has(e?.name) ? 'denied' : 'nocamera' });
    }
    stream = s;
    for (const t of s.getTracks()) t.addEventListener('ended', ended);
    video.srcObject = s;
    try { await video.play(); } catch { /* autoplay refusal: the SDK retries play() itself */ }
  }

  function stop() {
    if (!stream) return;
    for (const t of stream.getTracks()) { t.removeEventListener('ended', ended); t.stop(); }
    if (video.srcObject === stream) video.srcObject = null;
    stream = null;
  }

  // Given to the SDK as env.getUserMedia. Hands over a clone of the open stream; if the
  // stream died in the meantime, opens a new one first (permission is already granted).
  async function handoff() {
    if (!live()) { stream = null; await open().catch((e) => { throw Object.assign(new Error('camera'), { name: e.cameraError === 'denied' ? 'NotAllowedError' : 'NotFoundError' }); }); }
    return stream.clone(); // the SDK stops the clone; the page's own tracks stay live
  }

  // After the SDK stopped its clone (and cleared the preview), show the page's stream again.
  function reattach() {
    if (!live() || video.srcObject === stream) return;
    video.srcObject = stream;
    video.play().catch(() => { /* already playing, or autoplay refusal: the SDK retries play() itself */ });
  }

  return { open, stop, handoff, reattach, get live() { return live(); } };
}

const V1 = /\/v1\/(challenge|enroll|verify)$/;

export function createBridge({ fetch, subjectId, csrfToken, newKey }) {
  let scan = null;      // { op, url, body, key } kept in memory only, until forget()
  let reply = null;     // { status, body } raw /v2 reply of the last scan

  let precheck = null;  // page pre-check reading for the engine trace; never decides anything
  let glow = null;      // the challenge's glow schedule (glow.js), read beside the SDK; null = none
  const base = '/v2/subjects/' + subjectId;
  const auth = { 'X-CSRF-Token': csrfToken };

  async function sdkFetch(url, init = {}) {
    const m = V1.exec(new URL(url, 'https://x').pathname);
    if (!m) throw new TypeError('unexpected SDK request');
    const kind = m[1];

    if (kind === 'challenge') {
      const op = new Headers(init.headers).get('X-Facetech-Operation');
      glow = null;
      const res = await fetch(base + '/challenge?operation=' + encodeURIComponent(op ?? ''), {
        ...init, method: 'GET', headers: auth,
      });
      if (res.status !== 200) return asV1Error(res, await json(res));
      // The SDK keeps only nonce/action/params, so the page reads `glow` from a copy of the reply.
      glow = glowOf(await json(res.clone?.()));
      return res;
    }

    const facescan = JSON.parse(init.body).facescan;
    scan = {
      op: kind,
      url: base + '/' + kind,
      body: JSON.stringify({ subject_id: subjectId, facescan }),
      key: newKey(),
      precheck: precheck ? JSON.stringify(precheck) : null,
    };
    reply = null;
    const res = await send(init.signal);
    return forSdk(kind, res, reply.body);
  }

  async function send(signal) {
    const res = await fetch(scan.url, {
      method: 'POST', body: scan.body, signal,
      credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
      headers: {
        ...auth, 'Content-Type': 'application/json', 'Idempotency-Key': scan.key,
        ...(scan.precheck ? { 'X-Facetech-Precheck': scan.precheck } : {}),
      },
    });
    // Retry-After in seconds (the gateway forwards the engine's); capped so a bad header cannot lock the page.
    const wait = Number(res.headers.get('Retry-After'));
    reply = { status: res.status, body: await json(res), retryAfter: Number.isInteger(wait) && wait > 0 ? Math.min(wait, 300) : null };
    return res;
  }

  // Ask again about the SAME scan. Same key and same bytes, so the gateway
  // returns the saved decision or OPERATION_IN_PROGRESS; it cannot run twice.
  async function recheck() {
    if (!scan) return null;
    try { await send(undefined); } catch { return null; }
    return reply;
  }

  return {
    fetch: sdkFetch,
    recheck,
    forget() { scan = null; reply = null; },
    get sent() { return scan !== null; },
    get op() { return scan?.op ?? null; },
    get reply() { return reply; },
    get glow() { return glow; },
    set precheck(value) { precheck = value ?? null; },
  };
}

async function json(res) {
  try { return await res.json(); } catch { return null; }
}

function respond(status, body, from) {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  for (const h of ['X-Request-Id', 'Retry-After']) {
    const v = from.headers.get(h);
    if (v !== null) headers.set(h, v);
  }
  return new Response(JSON.stringify(body), { status, headers });
}

// /v2 errors are { error: CODE, message? }; the SDK reads { error: { code, message } }.
function asV1Error(res, body, status = res.status) {
  const code = typeof body?.error === 'string' ? body.error : 'DEPENDENCY_UNAVAILABLE';
  return respond(status, { error: { code, message: typeof body?.message === 'string' ? body.message : '' } }, res);
}

// Keeps the SDK's own result well-formed. It is NOT what the page displays.
function forSdk(op, res, body) {
  if (res.status !== 200 || typeof body !== 'object' || body === null) return asV1Error(res, body);
  const d = body.decision ?? {};
  if (typeof d.error === 'string') return asV1Error(res, d, 422);
  if (op === 'enroll') {
    if (!body.accepted) return asV1Error(res, { error: 'ENROLL_REFUSED' }, 422);
    return respond(200, { template_id: body.template_id, quality: d.quality ?? null, liveness: d.liveness }, res);
  }
  return respond(200, { match: d.match, score: d.score, threshold: d.threshold, liveness: d.liveness }, res);
}
