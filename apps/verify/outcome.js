// Turns what the server (and only the server) said into one page outcome.
//
// Success needs every predicate below. HTTP 200 alone, or the SDK's `ok`
// alone, is never enough; a reply that contradicts itself is shown as a
// server problem, never as success.

import { failedSignals } from '../shared/messages.js';

const isRecord = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);

/**
 * @param {'enroll'|'verify'} op
 * @param {{status:number, body:unknown}|null} reply  the raw /v2 reply, or null if none arrived
 * @returns {{outcome:string, recording:string|null, requestId:string|null, scores:object|null}}
 */
export function classifyReply(op, reply) {
  if (!reply) return out('uncertain');
  const { status, body } = reply;
  const b = isRecord(body) ? body : {};
  const requestId = typeof b.request_id === 'string' ? b.request_id : null;

  if (status !== 200) {
    const r = out(errorOutcome(b.error, status), null, requestId, whyNotLive(b.error, b.message));
    if (reply.retryAfter) r.retryAfter = reply.retryAfter; // seconds, from the 429/503 header (bridge.js)
    return r;
  }

  const d = isRecord(b.decision) ? b.decision : {};
  if (b.operation !== op || typeof b.accepted !== 'boolean') return out('server', null, requestId);

  if (!b.accepted) {
    // A refused scan carries the engine's code inside the decision.
    if (typeof d.error === 'string') return out(errorOutcome(d.error, 200), null, requestId, whyNotLive(d.error, d.message));
    if (op === 'verify' && d.match === false) return out('nomatch', null, requestId, numbers({ match: d.score, threshold: d.threshold }));
    return out('server', null, requestId);
  }

  const live = isRecord(d.liveness) && d.liveness.live === true && d.liveness.enforced === true;
  const pad = isRecord(d.pad) && d.pad.outcome === 'live';
  const specific = op === 'enroll'
    ? typeof b.template_id === 'string' && b.template_id !== ''
    : d.match === true;
  if (!live || !pad || !specific) return out('server', null, requestId);

  return out('success', recordingStatus(b.recording), requestId, numbers(op === 'verify'
    ? { match: d.score, threshold: d.threshold, liveness: d.liveness.score }
    : { quality: isRecord(d.quality) ? d.quality.score : null, liveness: d.liveness.score }));
}

function out(outcome, recording = null, requestId = null, scores = null) {
  return { outcome, recording, requestId, scores };
}

// Only real numbers the server sent are ever shown; anything else is left out.
function numbers(fields) {
  const kept = Object.entries(fields).filter(([, v]) => typeof v === 'number' && Number.isFinite(v));
  return kept.length ? Object.fromEntries(kept) : null;
}

// A refused liveness check says which part refused: the anti-spoof model ('PAD ...'), or the
// failed signals in the engine's own message, parsed by the shared messages.js. The first
// failed signal in the engine's order gives the advice; anything unparsed gives none.
const SIGNAL_REASON = { challenge: 'movement', landmarks: 'tracking', motion: 'notlive', duplicates: 'notlive', timing: 'slowcamera' };
function whyNotLive(code, message) {
  if (code !== 'LIVENESS_FAIL' || typeof message !== 'string') return null;
  if (message.includes('PAD')) return { reason: 'spoof' };
  // pad_* codes (pad_flash, the enrolment bar's pad_enrol_*) are spoof checks: the one shared line.
  const reason = failedSignals(message).map((s) => (s.startsWith('pad_') ? 'spoof' : SIGNAL_REASON[s])).find(Boolean);
  return reason ? { reason } : null;
}

// Recording is separate from the face check: its failure never changes the outcome.
// Values are the gateway's own (facetech_auth/privacy.py): stored | skipped | pending | failed.
function recordingStatus(r) {
  if (!isRecord(r)) return 'off';
  if (r.status === 'stored') return 'saved';
  if (r.status === 'skipped') return 'off';
  if (r.status === 'pending') return 'pending';
  return 'failed';
}

// Server codes come from the SDK's shared ERROR_CODES (packages/face-sdk/src/errors.ts);
// main.js imports the map from /sdk/index.js and hands it in with useErrorCodes(). The page
// keys its outcome by each code's shared stage; the wording for it stays in view.js.
let errorCodes = null;
export function useErrorCodes(map) { errorCodes = map ?? null; }

const BY_STAGE = {
  session: 'expired', reauth: 'reauth', consent: 'consent',
  capture: 'quality', liveness: 'liveness', attempt: 'retry',
  enrolment: 'notenrolled', enrolled: 'already', capacity: 'busy',
  pending: 'pending', round: 'closed',
};

function serverStage(code) {
  if (!errorCodes) throw new Error('outcome.js needs the SDK ERROR_CODES (useErrorCodes)');
  if (typeof code !== 'string' || !Object.hasOwn(errorCodes, code)) return null;
  const info = errorCodes[code];
  return info.origin === 'sdk' ? null : info.stage; // SDK codes never come from a server
}

export function errorOutcome(code, status) {
  const stage = serverStage(code);
  if (stage && BY_STAGE[stage]) return BY_STAGE[stage];
  if (status === 401) return 'expired';
  if (status === 429 || status === 503) return 'busy';
  return 'server';
}

/** SDK-side failures that never reached a server decision. */
export function sdkFailureOutcome(code) {
  switch (code) {
    case 'CAMERA_DENIED':
    case 'CAMERA_UNAVAILABLE':
    case 'CAMERA_ENDED': return code; // the camera notice is keyed by the shared code (view.js)
    case 'CANCELLED': return 'cancelled';
    case 'FACE_LOST': return 'quality';
    case 'SCAN_TOO_LARGE': return 'quality';
    case 'CHALLENGE_EXPIRED': return 'retry';
    default: return null; // NETWORK / TIMEOUT / BAD_RESPONSE etc: decided by whether the scan was sent
  }
}

/**
 * The one place a finished attempt becomes a page outcome.
 * @param {'enroll'|'verify'} op
 * @param {{ok:boolean, code?:string, engineCode?:string|null, httpStatus?:number|null}} sdkResult
 * @param {{sent:boolean, reply:{status:number, body:unknown}|null}} bridge
 */
export function decide(op, sdkResult, bridge) {
  if (bridge.sent) {
    const c = classifyReply(op, bridge.reply);
    // The gateway gave up waiting on the engine; the engine may still commit. Ask, do not redo.
    const gaveUp = bridge.reply?.status === 503 && bridge.reply.body?.error === 'DEPENDENCY_UNAVAILABLE';
    return gaveUp ? { ...c, outcome: 'uncertain' } : c;
  }
  // Nothing was submitted, so nothing here can be a success.
  const outcome = sdkFailureOutcome(sdkResult.code)
    ?? (sdkResult.engineCode || sdkResult.httpStatus ? errorOutcome(sdkResult.engineCode, sdkResult.httpStatus) : 'offline');
  return { outcome: outcome === 'success' ? 'server' : outcome, recording: null, requestId: null };
}
