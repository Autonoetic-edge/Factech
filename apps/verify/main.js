// Wires the flow (flow.js) to the real world: the signed-in account, the /v2 gateway,
// the camera and the capture SDK. Every effect named by flow.js is performed here.
import { CAMERA_CONSTRAINTS, createFaceSession, LUMA_MIN, SHARP_MIN } from '/sdk/index.js';
import { createBridge, createCameraOwner } from './bridge.js';
import { poseOf, poseOfAction, POSE_ORDER, TURN_SIGN } from './cues.js';
import { initial, step } from './flow.js';
import { createFraming, framingStep, precheckReading } from './framing.js';
import { createPreparation, sampleOf } from './framing.js';
import { createTurnMeter, turnStep, yawOf } from './framing.js';
import { glowColour, reducedGlowColour } from './glow.js';
import { createHeadGuide } from './head-guide.js';
import { classifyReply, decide, errorOutcome } from './outcome.js';
import { icon, render, renderMotion } from './view.js';

// The agreement text on this page is this version. If the server has moved on it
// refuses the grant (CONSENT_VERSION_REQUIRED) instead of recording the wrong text.
const CONSENT_TEXT_VERSION = 'template-authentication-v1';
const WORKSPACE = '/workspace';

const $ = (id) => document.getElementById(id);
const video = $('preview');
const guide = createHeadGuide($('face'));

let state = initial();
let account = null;   // { subjectId, csrf }
let bridge = null;
let session = null;
const preparation = createPreparation();
let watchingSettle = false, retryPreparation = false, settleWatch = 0, cameraOpenSeq = 0;
let turnMeter = null; // single turn only: the display-only meter keeps the guide running during capture
let glowPlan = null, glowFrame = 0; // glow challenge only (glow.js): the schedule, and its animation frame
const GLOW_NOTE_KEY = 'facetech.glowNoteSeen';

// The glow around the oval. Neutral from the instruction on, so frame 0 is lit like the
// baseline the engine expects; the schedule's clock starts at frame 0 (the SDK's first frame
// event fires right after it is grabbed, on the same performance.now() clock as its ts_ms).
function paintGlow(rgb) { $('stage-ui').style.setProperty('--glow', rgb.join(' ')); }
function runGlow(t0) {
  const plan = glowPlan;
  const tick = () => {
    if (glowPlan !== plan || state.view !== 'capture') return;
    paintGlow((guide.reducedMotion ? reducedGlowColour : glowColour)(plan, performance.now() - t0));
    glowFrame = requestAnimationFrame(tick);
  };
  tick();
}
function stopGlow() {
  if (!glowPlan && !glowFrame) return;
  glowPlan = null;
  cancelAnimationFrame(glowFrame);
  glowFrame = 0;
  $('stage-ui').style.removeProperty('--glow');
}
// "The screen will glow softly while we check" is shown before the first glow only.
function firstGlow() {
  try {
    if (localStorage.getItem(GLOW_NOTE_KEY)) return false;
    localStorage.setItem(GLOW_NOTE_KEY, '1');
  } catch { /* no storage: show it every time rather than never */ }
  return true;
}
function restartPreparation() {
  if (!watchingSettle || state.view !== 'capture' || bridge.sent) return;
  watchingSettle = false;
  retryPreparation = true;
  session?.cancel('preparation-moved');
}
const watchingCapture = () => state.view === 'capture' && (watchingSettle || !!turnMeter);
function stopSettleWatch() { watchingSettle = false; clearInterval(settleWatch); settleWatch = 0; }

const camera = createCameraOwner({
  mediaDevices: navigator.mediaDevices, video, constraints: CAMERA_CONSTRAINTS,
  onEnded: () => dispatch({ type: 'CAMERA_ENDED' }),
});

// The face guide on the waiting camera screen: /shared/face-detector.js, used as it is.
// It only chooses the line on screen and when Start wakes up. If it cannot load or run,
// GUIDE_OFF puts the page back on the fixed oval. It never sees or changes a decision.
const GUIDE_WAIT_MS = 15000; // the detector is ~10 MB on a first visit; after this the fixed oval takes over
let detector = null, framing = null, guideTimer = 0;
const loadGuide = () => import('/shared/face-detector.js');
// Mean brightness of the whole picture, for the backlight pre-check (framing.js precheck).
let lumaCtx = null;
let lastPrecheck = null; // the reading at auto-start, sent with the capture for the trace
function frameLuma() {
  try {
    if (!video.videoWidth) return null;
    lumaCtx ??= Object.assign(document.createElement('canvas'), { width: 32, height: 24 }).getContext('2d', { willReadFrequently: true });
    lumaCtx.drawImage(video, 0, 0, 32, 24);
    const d = lumaCtx.getImageData(0, 0, 32, 24).data;
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    return sum / (d.length / 4);
  } catch {
    return null;
  }
}
function stopGuide() { clearTimeout(guideTimer); detector?.stop(); }
function guideOff() { restartPreparation(); stopGuide(); dispatch({ type: 'GUIDE_OFF' }); }

function dispatch(event) {
  if (event.type === 'STOP' || event.type === 'HIDDEN') retryPreparation = false;
  const before = state;
  const r = step(state, event);
  state = r.state;
  if (state.view !== 'camera' && !watchingCapture()) stopGuide();
  if (state !== before) paint(before);
  if (r.effect) effects[r.effect]();
}

function paint(before) {
  const focused = document.activeElement?.id;
  const moved = before.view !== state.view || before.outcome !== state.outcome;
  render(state, { guide, focus: moved });
  if (!moved && focused && $(focused)) $(focused).focus({ preventScroll: true });
  $('primary')?.addEventListener('click', () => dispatch({ type: 'PRIMARY' }));
  $('register')?.addEventListener('click', () => dispatch({ type: 'REGISTER' }));
  $('demo')?.addEventListener('click', () => dispatch({ type: 'DEMO' }));
  $('skip')?.addEventListener('click', () => dispatch({ type: 'SKIP' }));
  $('privacy')?.addEventListener('click', () => $('privacy-dialog').showModal());
}

async function api(method, path, body) {
  const headers = { Accept: 'application/json' };
  if (account) headers['X-CSRF-Token'] = account.csrf;
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
  });
  let data = null;
  try { data = await res.json(); } catch { /* not JSON: handled by status */ }
  return { status: res.status, body: data };
}

const effects = {
  async load() {
    try {
      const me = await api('GET', '/auth/session');
      if (me.status === 401) {
        const options = await api('GET', '/auth/options');
        loadGuide().then((m) => m.loadDetector()).catch(() => {}); // warm the cache before sign-up; failures are retried after sign-in
        return dispatch({ type: 'SIGNED_OUT', registration: options.status === 200 && options.body?.registration === true });
      }
      const subjectId = me.body?.subject_id, csrf = me.body?.csrf_token;
      if (me.status !== 200 || typeof subjectId !== 'string' || typeof csrf !== 'string') return dispatch({ type: 'LOAD_FAILED' });
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) return dispatch({ type: 'UNSUPPORTED' });
      account = { subjectId, csrf };
      $('signout').hidden = false; // a shared phone can be handed over from here, not only from /workspace
      bridge = createBridge({
        fetch: (url, init) => fetch(url, init), subjectId, csrfToken: csrf,
        newKey: () => crypto.randomUUID().replaceAll('-', ''),
      });
      // Enrolled or not is the server's answer, never remembered in the browser.
      const list = await api('GET', `/v2/subjects/${subjectId}/templates`);
      if (list.status === 401) return dispatch({ type: 'SIGNED_OUT' });
      if (list.status !== 200 || !Array.isArray(list.body?.templates)) return dispatch({ type: 'LOAD_FAILED' });
      $('account-line').textContent = typeof me.body.preferred_username === 'string' ? 'Signed in as ' + me.body.preferred_username : 'Signed in';
      dispatch({ type: 'LOADED', mode: list.body.templates.length ? 'verify' : 'enroll' });
      // Tell the person before they invest a capture: 30 s before the gateway's own expiry.
      // flow.js ignores it during capture/processing; a later 401 still shows 'expired'.
      if (Number.isInteger(me.body.expires_at)) {
        setTimeout(() => dispatch({ type: 'SESSION_EXPIRED', early: true }), Math.max(0, (me.body.expires_at - 30) * 1000 - Date.now()));
      }
      // Fetch the face guide now, while the person reads, so the camera screen does not wait for it.
      loadGuide().then((m) => m.loadDetector()).catch(() => dispatch({ type: 'GUIDE_OFF' }));
    } catch {
      dispatch({ type: 'LOAD_FAILED' });
    }
  },

  async grantConsent() {
    try {
      const r = await api('POST', `/v2/subjects/${account.subjectId}/consents/template_authentication`, { text_version: CONSENT_TEXT_VERSION });
      if (r.status === 200 && r.body?.granted === true) return dispatch({ type: 'CONSENT_OK' });
      const outcome = errorOutcome(r.body?.error, r.status);
      // 'consent' here means this page's agreement text is out of date: a server-side problem, not the user's.
      dispatch({ type: 'CONSENT_FAILED', outcome: outcome === 'consent' ? 'server' : outcome });
    } catch {
      dispatch({ type: 'CONSENT_FAILED', outcome: 'offline' });
    }
  },

  async openCamera() {
    const seq = ++cameraOpenSeq;
    try {
      await camera.open();
      if (seq !== cameraOpenSeq || document.hidden) { camera.stop(); return; }
      dispatch({ type: 'CAMERA_OK' });
    } catch (e) {
      if (seq === cameraOpenSeq) dispatch({ type: 'CAMERA_FAILED', error: e.cameraError ?? 'nocamera' });
    }
  },

  async startGuide() {
    if (state.guideOff) return;
    preparation.reset();
    guideTimer = setTimeout(guideOff, GUIDE_WAIT_MS);
    try {
      const { createFaceDetector } = await loadGuide();
      framing = createFraming({ lumaMin: LUMA_MIN, sharpMin: SHARP_MIN });
      detector ??= createFaceDetector({
        video, view: video, onUnavailable: guideOff,
        onTick(g, dets, frame) {
          if ((state.view !== 'camera' && !watchingCapture()) || state.guideOff) return stopGuide();
          clearTimeout(guideTimer);
          const now = performance.now();
          const ready = preparation.tick(sampleOf(g, dets, frame), now);
          if (state.view === 'capture') {
            if (preparation.disturbed(now)) restartPreparation();
            if (turnMeter && state.challenge) {
              const zone = turnStep(turnMeter, yawOf(dets, frame), state.phase, TURN_SIGN[state.challenge.pose]);
              if (zone) dispatch({ type: 'TURN_SAMPLE', zone });
            }
            return;
          }
          const extra = { yaw: yawOf(dets, frame), frameLuma: frameLuma() };
          const line = framingStep(framing, g, now, extra);
          lastPrecheck = precheckReading(g, extra, framing);
          dispatch({ type: 'GUIDE', line: state.preparing && line === 'good' ? 'hold' : line });
          if (state.preparing && ready && !document.hidden) dispatch({ type: 'PREPARED' });
        },
      });
      detector.reset();
      await detector.start(); // a failed load reports itself through onUnavailable
    } catch {
      guideOff();
    }
  },

  prepare() { preparation.reset(); detector?.reset(); },
  // After an automatic restart: the guide is still running on the page's own stream; arm it again.
  restartScan() { preparation.reset(); detector?.reset(); detector?.start(); },
  stopCamera() { cameraOpenSeq++; stopSettleWatch(); camera.stop(); },

  async startScan() {
    const op = state.mode;
    retryPreparation = false;
    turnMeter = null;
    stopGlow();
    watchingSettle = !state.guideOff; // no guide, no page-side stillness watch: the server's settle window still judges it
    if (watchingSettle) {
      preparation.begin(performance.now());
      detector?.start();
      settleWatch = setInterval(() => {
        if (watchingSettle && preparation.disturbed(performance.now())) restartPreparation();
      }, 100);
    }
    bridge.forget();
    bridge.precheck = lastPrecheck;
    session = createFaceSession({
      videoElement: video,
      cancelOnHide: false, // flow.js decides: cancel before sending, never after
      env: { fetch: bridge.fetch, getUserMedia: () => camera.handoff() },
      onEvent(e) {
        if (e.type === 'instruction') {
          // Single turn: the engine's action picks the screen. HEAD_SEQUENCE maps to null and
          // keeps today's sentence cues below.
          const pose = poseOfAction(e.instruction?.action);
          if (pose) {
            if (!state.guideOff) turnMeter = createTurnMeter();
            glowPlan = bridge.glow;
            if (glowPlan) {
              paintGlow(glowPlan.neutral);
              dispatch({ type: 'SDK_INSTRUCTION', pose, settleMs: e.instruction.settleMs, glow: true, glowNote: firstGlow() });
            } else dispatch({ type: 'SDK_INSTRUCTION', pose, settleMs: e.instruction.settleMs });
          }
        } else if (e.type === 'phase') {
          if (e.phase === 'move' && state.challenge) stopSettleWatch(); // the turn is asked for now: not "you moved"
          dispatch({ type: 'SDK_PHASE', phase: e.phase });
        } else if (e.type === 'action') {
          const pose = poseOf(e.text);
          if (pose === 'left' || pose === 'right') { stopSettleWatch(); stopGuide(); }
          if (pose) dispatch({ type: 'SDK_CUE', pose, text: e.text });
        } else if (e.type === 'frame') {
          if (e.index === 1 && glowPlan && !glowFrame) runGlow(performance.now());
          dispatch({ type: 'SDK_FRAME', index: e.index, total: e.total });
        }
        else if (e.type === 'uploading') dispatch({ type: 'SDK_UPLOADING' });
        else if (e.type === 'camera' && e.state === 'stopped') dispatch({ type: 'CAMERA_STOPPED' });
      },
    });
    let result;
    try {
      result = await session[op](account.subjectId);
    } catch {
      result = { ok: false, code: 'BAD_RESPONSE' };
    }
    stopSettleWatch();
    stopGlow();
    turnMeter = null;
    session.dispose();
    session = null;
    if (retryPreparation && !bridge.sent && !document.hidden && result.code === 'CANCELLED' && camera.live) {
      retryPreparation = false;
      camera.reattach(); // the SDK stopped only its clone: no reopen, no camera-light blink
      return dispatch({ type: 'PREPARATION_RETRY' });
    }
    camera.stop();
    dispatch({ type: 'RESULT', ...decide(op, result, bridge) });
  },

  cancelScan() { retryPreparation = false; stopSettleWatch(); stopGlow(); session?.cancel('user-stop'); },

  async recheck() {
    const reply = await bridge.recheck();
    const c = reply ? classifyReply(bridge.op, reply) : { outcome: 'uncertain', recording: null, requestId: null };
    dispatch({ type: 'RECHECKED', ...c });
  },

  forgetScan() { bridge?.forget(); },
  waitRetry() { setTimeout(() => dispatch({ type: 'RETRY_READY' }), state.retryAfter * 1000); },
  goWorkspace() { bridge?.forget(); location.assign(WORKSPACE); },
  goSignin() { location.assign('/auth/login'); },
  goRegister() { location.assign('/auth/register'); },
};

// Practice buttons: move the guide, nothing else (flow.js ignores them outside the demo).
[...$('pose-track').children].forEach((button, i) => {
  button.addEventListener('click', () => dispatch({ type: 'PREVIEW', pose: POSE_ORDER[i] }));
});
// The ✕ on the full-screen camera. It is never rebuilt, so a tap cannot land on a vanishing button.
$('stop').innerHTML = icon('close');
$('stop').addEventListener('click', () => {
  // A mis-tap during capture would throw away the whole sequence; before capture ✕ only closes the camera.
  if (state.view === 'capture' && !confirm('Stop the check? Nothing has been sent yet.')) return;
  dispatch({ type: 'STOP' });
});
$('motion').addEventListener('click', () => { guide.setPaused(!guide.paused); renderMotion(guide); });
$('help').addEventListener('click', () => $('help-dialog').showModal());
$('signout').addEventListener('click', async () => {
  $('signout').disabled = true;
  const r = await api('POST', '/auth/logout').catch(() => ({ status: 0 }));
  if (r.status === 200 || r.status === 401) return location.assign('/'); // 401: already signed out
  $('signout').disabled = false;
  $('signout').textContent = 'Sign out didn’t finish. Try again';
});
document.querySelectorAll('[data-close]').forEach((el) => {
  if (el.classList.contains('close')) el.innerHTML = icon('close');
  el.addEventListener('click', () => el.closest('dialog').close());
});

document.addEventListener('visibilitychange', () => { if (document.hidden) dispatch({ type: 'HIDDEN' }); });
// Back button: a page restored from the back/forward cache still has busy:'leaving', a dead
// guide and no camera. Reload so load() shows the real session state; otherwise re-enable buttons.
window.addEventListener('pageshow', (e) => { if (e.persisted) location.reload(); else dispatch({ type: 'RESUME' }); });
// Leaving the page: release everything. A scan already sent finishes on the server by itself.
window.addEventListener('pagehide', () => {
  retryPreparation = false; stopSettleWatch(); stopGlow(); stopGuide();
  if (state.view === 'capture') session?.cancel('pagehide');
  if (state.view !== 'processing') session?.dispose();
  camera.stop();
  guide.destroy();
});

renderMotion(guide);
paint({});
effects.load();
