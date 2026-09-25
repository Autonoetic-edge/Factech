import { createFaceSession, CAPTURE_MS, FRAME_COUNT, LANDING_MS, OVAL_START } from '/sdk/index.js?v=ui-v4';
import { createFaceDetector } from '/shared/face-detector.js';
import {
  LOSS_CUES, createEarlyMove, debugRecord, earlyMoveStep, probeOf, sampleOf,
} from '/shared/face-guide.js';
import {
  CUE_TEXT, MOVED_EARLY, PHASE_TEXT, STOPPED_TEXT, failedSignals, messageFor, resultTone,
} from '/shared/messages.js';

const MAX_RESTARTS = 2;

const RESTART_PAUSE_MS = 1800;

const PROBE_STALE_MS = 1000;
const POSITIONING_LIMIT_MS = 8000;

const PREVIEW_CONSTRAINTS = {
  video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }, audio: false,
};

const $ = (id) => document.getElementById(id);
const DEBUG = new URLSearchParams(location.search).get('debug') === '1';

const log = (line) => {

  $('log').textContent += line + '\n';
  $('log').scrollTop = $('log').scrollHeight;
};

const S = {
  stage: 'idle',
  op: null, userId: null, restarts: 0,
  captureOptions: null,
  attempt: null,
  preview: null,
  armedWait: null,
  sdkPhase: null,
  instruction: null,
  progress: null,
  bestProgress: null,
  early: createEarlyMove(),
  lastTickAt: 0,
  lastRecord: null,
};

let attemptSeq = 0;

function beginAttempt() {
  let stop;
  const stopped = new Promise((resolve) => { stop = resolve; });
  S.attempt = { id: ++attemptSeq, cancelled: false, reason: null, stopped, stop };
  return S.attempt;
}

const live = (a) => !!a && S.attempt === a && !a.cancelled;

const untilStopped = (a, work) => Promise.race([work, a.stopped.then(() => undefined)]);

function setOval(scale, ms) {
  const o = $('oval');
  o.style.transitionDuration = (ms || 0) + 'ms';
  o.style.transform = 'scale(' + scale.toFixed(3) + ')';
}

function setCue(text, tone) {
  $('cue').textContent = text || '';
  $('view').dataset.tone = tone || '';
}

const detector = createFaceDetector({
  video: $('preview'),
  view: $('view'),
  onTick: (guide, dets, frame) => {
    S.lastTickAt = performance.now();
    if (DEBUG) {
      S.lastRecord = debugRecord(dets, frame, guide);
      $('debugLine').textContent = JSON.stringify(S.lastRecord);
    }
    if (!live(S.attempt)) return;
    if (S.stage === 'preview' || S.stage === 'probe') {
      setCue((S.stage === 'preview' ? 'Positioning only: ' : '') + CUE_TEXT[guide.cue], guide.cue === 'good' ? 'ok' : 'alert');
      if (guide.armed && S.armedWait) { const w = S.armedWait; S.armedWait = null; w(); }
      return;
    }
    if (S.stage !== 'record') return;
    if (S.instruction?.action === 'HEAD_SEQUENCE') return;
    if (LOSS_CUES.includes(guide.cue)) setCue(CUE_TEXT[guide.cue], 'alert');
    else if (S.sdkPhase === 'move' && S.progress !== null && S.progress >= 1) setCue(PHASE_TEXT.reached, 'ok');
    else if (S.sdkPhase) setCue(PHASE_TEXT[S.sdkPhase], 'ok');

    if (S.sdkPhase === 'hold' && !S.early.tripped) {
      earlyMoveStep(S.early, guide.face ? guide.face.h : null);
      if (S.early.tripped) {
        log(`moved early: growth=${S.early.growth.toFixed(3)} over baseline=${S.early.baseline.toFixed(3)}`);
        session.cancel(MOVED_EARLY);
      }
    }
  },
  onUnavailable: (reason) => {
    log('face guide off: ' + reason + '. The engine still decides.');
    $('guideState').textContent = 'face guide: off';

    if (S.armedWait) { const w = S.armedWait; S.armedWait = null; w(); }
  },
});

const session = createFaceSession({
  gatewayUrl: '',
  videoElement: $('preview'),

  onInstruction: (instruction) => {
    if (S.stage !== 'record') return undefined;
    S.instruction = instruction;
    $('instruction').textContent = instruction.action === 'HEAD_SEQUENCE'
      ? 'Keep the phone still. Follow the head-turn prompts for about 16 seconds.'
      : instruction.action === 'MOVE_CLOSER'
      ? 'Hold still, then move closer as the oval grows.'
      : instruction.action;
    log(`instruction ${instruction.action} settle=${instruction.settleMs}ms ` +
        `target=${instruction.target} oval=${instruction.oval.from.toFixed(3)}->${instruction.oval.to.toFixed(3)}`);
    setOval(instruction.oval.from, 250);
    return new Promise((r) => setTimeout(r, 300));
  },

  onEvent: (e) => {
    if (S.stage !== 'record') return;
    switch (e.type) {
      case 'action':
        $('instruction').textContent = e.text;
        setCue(e.text, 'ok');
        break;
      case 'camera': log(`camera ${e.state}`); break;
      case 'phase':
        S.sdkPhase = e.phase;
        $('phase').textContent = e.phase;
        log(`phase ${e.phase}`);
        if (e.phase === 'hold') S.early = createEarlyMove();
        if (e.phase === 'move' && S.instruction) {

          const moveMs = Math.max(300, (FRAME_COUNT - 1) * CAPTURE_MS - S.instruction.settleMs);
          setOval(S.instruction.oval.to, moveMs);
        }
        if (!detector.available) setCue(S.instruction?.action === 'HEAD_SEQUENCE' && e.phase === 'hold' ? 'Face forward. Hold still.' : PHASE_TEXT[e.phase], 'ok');
        break;
      case 'guide':
        S.progress = e.progress;
        if (e.progress !== null && (S.bestProgress === null || e.progress > S.bestProgress)) S.bestProgress = e.progress;
        break;
      case 'frame': $('phase').textContent = `${S.sdkPhase} ${e.index}/${e.total}`; break;
      case 'uploading':
        log(`uploading ${e.bytes} bytes, measured approach ` +
            (S.bestProgress === null ? 'unknown' : S.bestProgress.toFixed(2) + ' of the guide target'));
        setCue(PHASE_TEXT.landing, 'ok');
        break;
      default: break;
    }
  },

  faceProbe: () => {
    if (!detector.available || S.stage !== 'record') return null;
    if (performance.now() - S.lastTickAt > PROBE_STALE_MS) return null;
    return probeOf(detector.guide);
  },
});

function stopStream(stream) {
  for (const t of stream.getTracks()) t.stop();
}

async function openPreview(a) {
  const request = Promise.resolve()
    .then(() => navigator.mediaDevices.getUserMedia(PREVIEW_CONSTRAINTS))
    .then(
      (stream) => { if (live(a)) return stream; stopStream(stream); return null; },
      (e) => { if (live(a)) log('preview camera: ' + ((e && e.name) || e)); return null; },
    );
  const stream = await untilStopped(a, request);
  if (!stream || !live(a)) return false;
  S.preview = stream;
  const v = $('preview');
  v.srcObject = stream;
  await untilStopped(a, v.play().catch(() => undefined));
  return live(a);
}

function closePreview() {
  if (!S.preview) return;
  stopStream(S.preview);
  if ($('preview').srcObject === S.preview) $('preview').srcObject = null;
  S.preview = null;
}

async function startGuide(a) {
  const started = detector.start().then((ok) => {
    if (!live(a) && !S.attempt) detector.stop();
    return ok;
  });
  return (await untilStopped(a, started)) === true && live(a);
}

function waitForArmed(a) {
  let timer;
  const ready = new Promise((resolve) => {
    S.armedWait = resolve;
    timer = setTimeout(() => {
      if (['multi', 'hidden'].includes(detector.guide?.cue)) {
        cancelAttempt('multiple faces');
        const text = 'Stopped before capture: make sure only the consenting test subject is in view.';
        $('result').textContent = text;
        setCue(text, 'alert');
        resolve();
        return;
      }
      log('Positioning time ended. Starting server-issued head-turn capture; the server checks usable frames.');
      resolve();
    }, POSITIONING_LIMIT_MS);
  });
  return untilStopped(a, ready).finally(() => clearTimeout(timer));
}

function finishAttempt(a, resetConsent = true) {
  if (S.attempt !== a) return;
  S.attempt = null;
  S.armedWait = null;
  closePreview();
  detector.stop();
  S.stage = 'idle';
  S.sdkPhase = null;
  if (resetConsent) window.facetechResetConsent?.();
  $('enroll').disabled = $('verify').disabled = $('probe').disabled = false;
  $('cancel').disabled = true;
  $('instruction').textContent = '';
  $('phase').textContent = '';
  setOval(OVAL_START, 250);
}

function cancelAttempt(reason) {
  const a = S.attempt;
  if (!a || a.cancelled) return;
  a.cancelled = true;
  a.reason = reason;
  a.stop();
  const wasRecording = S.stage === 'record';
  const wasProbe = S.stage === 'probe';
  session.cancel(reason);
  finishAttempt(a);
  log(`stopped (${reason})` + (wasRecording ? '' : ' before recording'));
  if (wasProbe) { setCue('', ''); return; }
  const text = reason === 'hidden' ? STOPPED_TEXT.hidden : STOPPED_TEXT.cancelled;
  $('result').textContent = text;
  setCue(text, 'alert');
}

$('captureConsent')?.addEventListener?.('change', () => {
  if (!$('captureConsent').checked) cancelAttempt('consent withdrawn');
});

function show(result) {
  const sentence = !result.ok && result.engineCode === 'LIVENESS_FAIL' && S.instruction?.action === 'HEAD_SEQUENCE'
    ? 'The live-person check was not confirmed. Face forward at the start, then follow each small head-turn prompt. Keep both eyes visible and the phone still.'
    : messageFor(result);
  $('result').textContent = sentence;
  $('view').dataset.tone = resultTone(result);
  setCue(sentence, $('view').dataset.tone);

  const detail = result.ok
    ? (result.op === 'enroll'
      ? `template=${result.templateId} quality=${result.quality}`
      : `match=${result.match} score=${result.score.toFixed(4)} threshold=${result.threshold}`)
      + ` liveness=${result.liveness} ${result.elapsedMs}ms`
    : `code=${result.code} engineCode=${result.engineCode} reason=${result.reason} ` +
      `retryable=${result.retryable} http=${result.httpStatus}`;
  log(`result ${result.op} ${result.ok ? 'ok' : 'failed'} ${detail} requestId=${result.requestId}`);

  if (!result.ok && result.message) log(`message: ${result.message}`);
  const failed = result.ok ? [] : failedSignals(result.message);
  if (failed.length) log(`failed signals: ${failed.join(', ')}`);
  window.facetechCaptureReceipt?.(result.requestId);
}

async function attempt(a) {
  S.stage = 'preview';
  S.sdkPhase = null;
  S.progress = null;
  S.bestProgress = null;
  S.instruction = null;
  $('instruction').textContent = 'Position your face first (up to 8 seconds). The head-turn prompts start afterward.';
  setOval(OVAL_START, 250);
  detector.setMode('start');
  detector.reset();
  if (await openPreview(a) && await startGuide(a)) {
    setCue('Checking your face...', '');
    await waitForArmed(a);
  }
  S.armedWait = null;
  closePreview();
  if (!live(a)) return null;

  S.stage = 'record';
  detector.setMode('record');
  setCue('Getting ready...', '');
  const options = S.captureOptions ? {captureMeta: {...S.captureOptions.captureMeta, restarts: S.restarts}} : undefined;
  const result = S.op === 'enroll' ? await session.enroll(S.userId, options) : await session.verify(S.userId, options);
  if (!live(a)) return null;
  S.stage = 'idle';
  S.sdkPhase = null;
  return result;
}

async function run(op) {
  if (S.attempt) return;
  const a = beginAttempt();
  let prepared = false;
  $('enroll').disabled = $('verify').disabled = $('probe').disabled = true;
  $('cancel').disabled = false;
  $('result').textContent = 'Checking consent and recording before opening the camera...';
  try {
    if (typeof window.facetechCaptureOptions !== 'function') throw Error('The recording form did not load. Reload before testing.');
    const options = await untilStopped(a, window.facetechCaptureOptions());
    if (!live(a)) return;
    if (!options?.captureMeta?.consent) throw Error('Consented recording is required. Reload this evaluation page.');
    S.captureOptions = options;
    const testCase = S.captureOptions?.captureMeta?.case;
    if (op === 'enroll' && ['screen_photo', 'screen_video'].includes(testCase)) {
      const suffix = testCase === 'screen_photo' ? '-photo' : '-video';
      if (!$('userId').value.trim().toLowerCase().endsWith(suffix)) {
        $('result').textContent = `Use a separate attack enrollment identity ending in ${suffix}. Keep genuine templates separate.`;
        return;
      }
    }
    prepared = true;
    S.op = op;
    S.userId = $('userId').value;
    S.restarts = 0;
    $('result').textContent = '';
    log(`--- ${op} (about 16 seconds of recording; follow the head-turn prompts)`);
    for (;;) {
      const result = await attempt(a);
      if (!result) break;
      if (!result.ok && result.code === 'CANCELLED' && result.reason === MOVED_EARLY) {
        show(result);
        if (S.restarts >= MAX_RESTARTS) break;
        S.restarts++;
        log(`restart ${S.restarts} of ${MAX_RESTARTS}`);
        await untilStopped(a, new Promise((r) => setTimeout(r, RESTART_PAUSE_MS)));
        if (!live(a)) break;
        continue;
      }
      show(result);
      break;
    }
  } catch (e) {
    if (live(a)) { $('result').textContent = e.message || 'Capture stopped.'; log('stopped: ' + ((e && e.name) || e)); setCue('', ''); }
  } finally {
    finishAttempt(a, prepared);
  }
}

$('enroll').onclick = () => run('enroll');
$('verify').onclick = () => run('verify');
$('cancel').onclick = () => cancelAttempt('user-pressed-cancel');

document.addEventListener('visibilitychange', () => { if (document.hidden) cancelAttempt('hidden'); });
addEventListener('pagehide', () => { cancelAttempt('pagehide'); session.dispose(); });

if (DEBUG) {
  $('debug').hidden = false;

  $('keep').onclick = () => {
    if (!S.lastRecord) return;
    $('samples').value += JSON.stringify(sampleOf($('label').value, S.lastRecord)) + '\n';
  };
  $('copy').onclick = () => {
    $('samples').select();
    navigator.clipboard?.writeText($('samples').value).catch(() => undefined);
  };

  $('probe').onclick = async () => {
    if (S.attempt) return;
    const a = beginAttempt();
    S.stage = 'probe';
    $('enroll').disabled = $('verify').disabled = $('probe').disabled = true;
    $('cancel').disabled = false;
    detector.setMode('start');
    detector.reset();
    if (await openPreview(a) && await startGuide(a)) {
      await a.stopped;
    }
    finishAttempt(a);
    if (!a.cancelled) setCue('', '');
  };
}

log('ready' + (DEBUG ? ' (debug)' : '') + '. The face guide loads when you press enroll or verify.');

document.documentElement.dataset.ready = '1';
