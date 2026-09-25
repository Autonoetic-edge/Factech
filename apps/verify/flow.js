// The participant flow as a pure state machine: step(state, event) -> { state, effect }.
// No DOM, camera, timers or network in here, so every rule below is unit-tested
// (apps/tests/verify-flow.test.js). main.js performs the effects.
//
// Rules this file owns:
//  - the demo is optional: shown once by itself on a first-time set-up, always skippable,
//    and its practice buttons only work there, with the camera off;
//  - the check starts itself once the face guide says every pre-check passed and the face
//    stayed still (auto-start). If the guide cannot run (GUIDE_OFF) a Start button is the
//    fallback and works by itself, as it did before there was a guide;
//  - Start works once: a second click while a scan is running does nothing;
//  - 'success' can only arrive in a RESULT event, which main.js builds from the server's reply;
//  - a hidden tab cancels a capture that has not been sent, and never touches one that has.

export const CAMERA_ERRORS = new Set(['denied', 'nocamera', 'cameralost']);

export function initial() {
  return {
    view: 'loading',      // loading | signin | unsupported | unavailable | intro | demo | ready | camera | capture | processing | result
    demoSeen: false,      // the automatic demo runs once per visit
    mode: null,           // 'enroll' | 'verify'
    name: null,
    busy: null,           // 'consent' | 'camera' | 'scan' | 'recheck' | 'leaving'
    cameraLive: false,
    cameraError: null,    // 'denied' | 'nocamera' | 'cameralost'
    preparing: false,
    framing: null,        // the steady line from framing.js while the camera waits: find | one | closer | back | centre | hold | good
    guideOff: false,      // the face guide could not load or run: the fixed oval is used instead
    guideLoading: false,  // camera on, guide not yet running: "Loading the face guide…"
    notice: null,         // 'moved': the scan restarted in place because the person moved
    previewPose: 'forward',
    cue: null,            // { pose, text, count } from SDK events only
    frame: null,          // { index, total } from SDK events only
    challenge: null,      // single turn only: { pose, settleMs } from the SDK's instruction event,
                          // plus glow: true (and glowNote the first time) when the challenge carries a glow
    phase: null,          // single turn only: 'hold' | 'move' from the SDK's phase events
    turn: null,           // single turn only: the turn meter's zone (display only, framing.js)
    outcome: null,
    scores: null,         // numbers the server sent with its decision, shown on the result
    recording: null,
    requestId: null,
    retryAfter: null,     // seconds the server asked us to wait before trying again (busy)
  };
}

const same = (state) => ({ state, effect: null });
const to = (state, patch, effect = null) => ({ state: { ...state, ...patch }, effect });
// Everything one capture showed; cleared whenever a capture starts or ends.
const FRESH = { cue: null, frame: null, challenge: null, phase: null, turn: null };
const TURN_ZONES = new Set(['more', 'good', 'far', 'lost']);

function result(state, e) {
  if (CAMERA_ERRORS.has(e.outcome)) {
    return to(state, { view: 'ready', busy: null, cameraLive: false, cameraError: e.outcome, ...FRESH });
  }
  const retryAfter = Number.isInteger(e.retryAfter) && e.retryAfter > 0 ? e.retryAfter : null;
  return to(state, {
    view: 'result', busy: null, cameraLive: false, ...FRESH,
    outcome: e.outcome, scores: e.scores ?? null, recording: e.recording ?? null, requestId: e.requestId ?? null, retryAfter,
  }, retryAfter ? 'waitRetry' : null);
}

export function step(state, e) {
  // True in every view.
  if (e.type === 'CAMERA_STOPPED') return to(state, { cameraLive: false });
  // Back from Keycloak (or any page we left for): the browser may restore this page with
  // 'leaving' still set, which keeps every button disabled. Only that flag is cleared.
  if (e.type === 'RESUME') return state.busy === 'leaving' ? to(state, { busy: null }) : same(state);
  if (e.type === 'GUIDE_OFF') return state.guideOff ? same(state) : to(state, { guideOff: true, guideLoading: false, preparing: false });
  if (e.type === 'SESSION_EXPIRED' && !['capture', 'processing'].includes(state.view)) {
    const r = result(state, { outcome: e.early ? 'expiring' : 'expired' }); // early: told before investing a capture
    return state.cameraLive ? { state: r.state, effect: 'stopCamera' } : r;
  }

  switch (state.view) {
    case 'loading':
      if (e.type === 'LOADED') return to(state, { view: e.mode === 'enroll' ? 'intro' : 'ready', mode: e.mode, name: e.name ?? null });
      if (e.type === 'SIGNED_OUT') return to(state, { view: 'signin', registration: e.registration === true });
      if (e.type === 'UNSUPPORTED') return to(state, { view: 'unsupported' });
      if (e.type === 'LOAD_FAILED') return to(state, { view: 'unavailable' });
      return same(state);

    case 'signin':
      if (e.type === 'REGISTER' && state.registration && !state.busy) return to(state, { busy: 'leaving' }, 'goRegister');
      if (e.type === 'PRIMARY' && !state.busy) return to(state, { busy: 'leaving' }, 'goSignin');
      return same(state);

    case 'unavailable':
      if (e.type === 'PRIMARY') return to(initial(), {}, 'load');
      return same(state);

    case 'intro':
      if (e.type === 'PRIMARY' && !state.busy) return to(state, { busy: 'consent' }, 'grantConsent');
      if (e.type === 'CONSENT_OK') return to(state, { view: state.demoSeen ? 'ready' : 'demo', busy: null, demoSeen: true, previewPose: 'forward' });
      if (e.type === 'CONSENT_FAILED') return result(state, e);
      return same(state);

    case 'demo': // camera off; leaving it never opens the camera
      if (e.type === 'PREVIEW') return to(state, { previewPose: e.pose });
      if (e.type === 'PRIMARY' || e.type === 'SKIP') return to(state, { view: 'ready' });
      return same(state);

    case 'ready':
      if ((e.type === 'STOP' || e.type === 'HIDDEN') && state.busy === 'camera') return to(state, { busy: null, preparing: false, cameraLive: false }, 'stopCamera');
      if (e.type === 'DEMO' && !state.busy) return to(state, { view: 'demo', demoSeen: true, previewPose: 'forward' });
      if (e.type === 'PRIMARY' && !state.busy) return to(state, { busy: 'camera', cameraError: null }, 'openCamera');
      if (e.type === 'CAMERA_OK') return to(state, { view: 'camera', busy: null, cameraLive: true, cameraError: null, previewPose: 'forward', framing: 'find', guideLoading: !state.guideOff, notice: null }, 'startGuide');
      if (e.type === 'CAMERA_FAILED') return to(state, { busy: null, cameraLive: false, cameraError: e.error });
      return same(state);

    case 'camera':
      if (e.type === 'GUIDE') {
        // "You moved" stays while the face is still well placed; a real problem replaces it.
        const notice = ['hold', 'good'].includes(e.line) ? state.notice : null;
        // Auto-start: every pre-check passed ('good'), so preparation arms itself; the ring
        // fills while the face stays still (READY_MS), then PREPARED starts the scan.
        if (e.line === 'good' && !state.preparing && !state.busy && state.cameraLive && !state.guideOff) {
          return to(state, { framing: e.line, guideLoading: false, notice, preparing: true }, 'prepare');
        }
        return e.line === state.framing && !state.guideLoading && notice === state.notice ? same(state) : to(state, { framing: e.line, guideLoading: false, notice });
      }
      if (e.type === 'PRIMARY' && !state.busy && state.cameraLive && !state.preparing) {
        // No guide: nothing on the page can measure stillness, so Start begins the scan itself.
        // The SDK's own cues and the server's settle window still judge the capture.
        if (state.guideOff) return to(state, { view: 'capture', busy: 'scan', ...FRESH }, 'startScan');
        return to(state, { preparing: true }, 'prepare');
      }
      if (e.type === 'PREPARED' && state.preparing && state.cameraLive && !state.guideOff) {
        return to(state, { view: 'capture', preparing: false, busy: 'scan', ...FRESH, notice: null }, 'startScan');
      }
      if (e.type === 'CAMERA_ENDED') return to(state, { view: 'ready', cameraLive: false, cameraError: 'cameralost' });
      if (e.type === 'STOP' || e.type === 'HIDDEN') return to(state, { view: 'ready', cameraLive: false, preparing: false }, 'stopCamera');
      return same(state);

    case 'capture':
      if (e.type === 'PREPARATION_RETRY') {
        // The person moved before the first turn: stay on the stage, keep the camera, say so,
        // and let the guide arm a fresh preparation. Nothing was sent (main.js checks).
        if (state.guideOff) return to(state, { view: 'camera', busy: null, preparing: false, ...FRESH });
        return to(state, { view: 'camera', busy: null, preparing: true, notice: 'moved', ...FRESH }, 'restartScan');
      }
      if (e.type === 'SDK_CUE') {
        const count = state.cue && state.cue.pose === e.pose ? state.cue.count : (state.cue?.count ?? 0) + 1;
        return to(state, { cue: { pose: e.pose, text: e.text, count } });
      }
      if (e.type === 'SDK_FRAME') return to(state, { frame: { index: e.index, total: e.total } });
      // Single turn: the engine's action (via the SDK's instruction) picks the screen; the
      // SDK's phase events alone move it from hold to turn. The meter only colours the line.
      if (e.type === 'SDK_INSTRUCTION') {
        const glow = e.glow === true ? { glow: true, glowNote: e.glowNote === true } : {};
        return to(state, { challenge: { pose: e.pose, settleMs: e.settleMs, ...glow }, phase: 'hold', turn: null });
      }
      if (e.type === 'SDK_PHASE' && state.challenge && (e.phase === 'hold' || e.phase === 'move')) {
        return e.phase === state.phase ? same(state) : to(state, { phase: e.phase });
      }
      if (e.type === 'TURN_SAMPLE' && state.challenge && state.phase === 'move' && TURN_ZONES.has(e.zone)) {
        return e.zone === state.turn ? same(state) : to(state, { turn: e.zone });
      }
      if (e.type === 'SDK_UPLOADING') return to(state, { view: 'processing' });
      if (e.type === 'STOP' || e.type === 'HIDDEN') return { state, effect: 'cancelScan' };
      if (e.type === 'RESULT') return result(state, e);
      return same(state); // PREVIEW and PRIMARY are deliberately dead here

    case 'processing':
      // Sent. Nothing the page does now may invent an answer: no cancel, no resend, no HIDDEN handling.
      if (e.type === 'RESULT') return result(state, e);
      return same(state);

    case 'result': {
      if (e.type === 'RECHECKED') return result(state, e);
      if (e.type === 'RETRY_READY') return state.retryAfter ? to(state, { retryAfter: null }) : same(state);
      if (e.type !== 'PRIMARY' || state.busy || state.retryAfter) return same(state);
      const o = state.outcome;
      if (o === 'success' || o === 'already') return to(state, { busy: 'leaving' }, 'goWorkspace');
      if (o === 'expired' || o === 'expiring' || o === 'reauth') return to(state, { busy: 'leaving' }, 'goSignin');
      if (o === 'uncertain' || o === 'pending') return to(state, { busy: 'recheck' }, 'recheck');
      if (o === 'consent') return to(state, { view: 'intro', outcome: null });
      if (o === 'notenrolled') return to(state, { view: 'intro', mode: 'enroll', outcome: null });
      if (o === 'closed') return to(state, { busy: 'leaving' }, 'goWorkspace');
      return to(state, { view: 'ready', outcome: null, cameraError: null }, 'forgetScan');
    }

    default:
      return same(state);
  }
}
