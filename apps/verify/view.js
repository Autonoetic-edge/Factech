// Draws the page from flow state. Layout, copy tone and class names follow design
// study 04; every "demo / simulated / sample" line is gone because nothing here is.
// All strings below are static. Nothing from the network is put in innerHTML except the
// server's Retry-After, and that only as a number.
import { POSE_ORDER } from './cues.js';

const $ = (id) => document.getElementById(id);

const icons = {
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  camera: '<path d="M8 6 10 3h4l2 3h4v14H4V6z"/><circle cx="12" cy="13" r="4"/>',
  face: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M9 9v2m6-2v2m-6 4q3 3 6 0"/>',
  shield: '<path d="m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6z"/><path d="m8 12 3 3 5-6"/>',
  light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  pause: '<path d="M9 5v14m6-14v14"/>', play: '<path d="m8 5 11 7-11 7z"/>',
};
export const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.arrow}</svg>`;
const primary = (label, busy, waiting = false) => `<button class="primary" id="primary"${busy ? ' disabled aria-busy="true"' : waiting ? ' disabled' : ''}>${label}${icon('arrow')}</button>`;

// Spoken to screen readers, and shown under the demo figure.
const CUE_SAY = {
  forward: 'Checking has started. Hold still and look straight.',
  left: 'Slowly turn a little to your left. Then hold.',
  right: 'Slowly turn a little to your right. Then hold.',
};

// The full-screen camera shows the face and ONE bold line, so the line is short.
const STAGE_CUE = {
  framing: 'Place your face<br> in the oval.',
  loading: 'Loading the<br> face guide…',
  // The face guide's steady lines (framing.js). 'framing' above is used when the guide is off.
  find: 'Place your face<br> in the oval.',
  one: 'Only you<br> in the frame.',
  closer: 'Move closer.',
  back: 'Move back<br> a little.',
  centre: 'Centre your face<br> in the oval.',
  // Pre-checks before the automatic start (framing.js precheck), one at a time.
  frontal: 'Face the camera.',
  light: 'Find more light<br> on your face.',
  steady: 'Hold the phone<br> steady.',
  hold: 'Hold still<br> and look straight.',
  good: 'Good.<br> Hold still.',
  moved: 'You moved.<br> Hold still, starting again.',
  starting: 'Good.<br> Hold still.',
  tap: 'Tap Start<br> when you are ready.', // no face guide: the Start button is the fallback
  forward: 'Checking has started.<br> Hold still and look straight.',
  left: 'Turn a little to your left<br> and hold it there.',
  right: 'Now a little to your right<br> and hold it there.',
  checking: 'Checking now.<br> Keep this page open.',
  // Single turn (the engine sent LOOK_LEFT / LOOK_RIGHT): the direction is always a word.
  still: 'Hold still.',
  turnleft: 'Turn a little<br> to your LEFT.',
  turnright: 'Turn a little<br> to your RIGHT.',
};

// The turn meter's zone as words (framing.js turnStep). Display only; the engine decides.
const TURN_LINE = {
  more: 'A little more.',
  good: 'Good. Hold it there.',
  far: 'A little less.',
  lost: 'Too far. Turn back until both eyes show.',
};
// Shown once, before the first glow challenge (plan Phase 4); main.js remembers it was seen.
const GLOW_NOTE = 'The screen will glow softly while we check.';
// The SDK's non-sequence cadence (packages/face-sdk/src/constants.ts CAPTURE_MS = 500).
const SINGLE_CADENCE_S = 0.5;

// Everything behind the full-screen camera. Inert while it is up, so focus stays on it.
const BEHIND = '.skip,.sidebar,.topbar,.page-heading,.experience-top,#screen,#steps,.under-card,.footer';

// Keyed by the SDK's shared camera codes (ERROR_CODES stage 'camera').
const CAMERA_NOTICE = {
  CAMERA_DENIED: 'Camera access is off. Allow the camera for this site in your browser settings, then try again. On iPhone: Settings > Safari > Camera. No check has started.',
  CAMERA_UNAVAILABLE: 'We could not open a camera. Close other apps or tabs that may be using it, check that one is connected, then try again.',
  CAMERA_ENDED: 'The camera stopped before the check was sent. Nothing was submitted. Enable it again when you are ready.',
};

// outcome -> [title, lead, button]
const OUTCOMES = {
  quality: ['We need a clearer view.', 'Face the light, keep both eyes visible and stay in the frame. Nothing was saved from this attempt.', 'Try again'],
  liveness: ['Let’s try once more.', 'We couldn’t confirm your presence. Hold still at the start, then follow each small head turn.', 'Try again'],
  nomatch: ['No match this time.', 'This attempt did not match your account’s saved face. No new template was added.', 'Try again'],
  busy: ['A short pause.<br><em>We’re busy.</em>', 'The service is busy right now. Please wait a moment before trying again.', 'Try again'],
  retry: ['That took<br><em>a little too long.</em>', 'The check timed out before it could be decided. Nothing was saved. Start a fresh one when you are ready.', 'Try again'],
  server: ['Something went wrong<br><em>on our side.</em>', 'We could not get a clear answer from the service, so this attempt does not count. Please try again.', 'Try again'],
  offline: ['We couldn’t<br><em>reach the service.</em>', 'Check your connection. Nothing was sent, so nothing was saved.', 'Try again'],
  expired: ['Let’s get you<br><em>signed in again.</em>', 'Your sign-in timed out. Sign in again to continue; your progress is safe.', 'Continue to sign in'],
  expiring: ['Let’s get you<br><em>signed in again.</em>', 'Your sign-in is about to time out. Please sign in again before starting a check. Nothing is lost.', 'Continue to sign in'],
  reauth: ['One more<br><em>sign-in, please.</em>', 'For your security, saving a face needs a sign-in from the last few minutes. Sign in again and you can continue straight away.', 'Continue to sign in'],
  uncertain: ['Let’s check<br><em>where we left off.</em>', 'The connection dropped after your check was sent. It may have finished. Check it before starting a new one.', 'Check existing result'],
  pending: ['Still<br><em>checking.</em>', 'Your check was received and is still being decided. No new check has started.', 'Check again'],
  cancelled: ['Stopped.<br><em>Take your time.</em>', 'Nothing was submitted. Start again whenever you are ready.', 'Start again'],
  consent: ['One thing<br><em>before we go on.</em>', 'We need your agreement to use a face template for your account before a check can run.', 'Review and agree'],
  notenrolled: ['Let’s set up<br><em>your face check.</em>', 'There is no saved face on your account yet, so there is nothing to compare with. Set it up first.', 'Set up now'],
  already: ['You’re already<br><em>set up.</em>', 'Your account already has a saved face. No new template was added.', 'Return to workspace'],
  closed: ['Not available<br><em>right now.</em>', 'Face checks are not open for your account at the moment. Please contact your administrator.', 'Return to workspace'],
};

// mode -> [title, lead, button] on a server-confirmed pass. Enrolment: "Your face is saved", and
// the person is already signed in (plan Phase 5), so the one next step is the workspace.
const SUCCESS = {
  enroll: ['Your face<br> <em>is saved.</em>', 'Your face template was saved to your account.', 'Go to workspace'],
  verify: ['Verified.<br> <em>Welcome back.</em>', 'Your face matched your saved template and the required checks passed.', 'Return to workspace'],
};

// Pinned by verify-flow.test.js so the participant wording only changes on purpose.
export const COPY = Object.freeze({ OUTCOMES, CAMERA_NOTICE, STAGE_CUE, TURN_LINE, GLOW_NOTE, SUCCESS });

// The server decided against these three; every other non-success outcome means no decision was made.
const REFUSED = ['quality', 'liveness', 'nomatch'];
// Why a liveness check was refused (outcome.js reason), written as an instruction, never as a
// verdict on the person. The numbers behind a decision are not shown to the participant.
const WHY_NOT_LIVE = {
  movement: 'You moved before the turn, or turned too late. Hold still until the screen says Turn, then turn a little sooner and hold it.',
  tracking: 'That turn was too big. A small turn is enough; keep both eyes on the screen.',
  slowcamera: 'Your camera was too slow. Close other apps and try again.',
  // Every spoof check shares ONE calm line that does not say which check tripped (plan §6.3).
  notlive: 'We couldn’t confirm a live face. Try again in even light, holding the phone yourself.',
  spoof: 'We couldn’t confirm a live face. Try again in even light, holding the phone yourself.',
};

// The capture stage's line and sub-line, from flow state only: the SDK's cue decides the line,
// the SDK's frame events the count. The page never advances either by itself.
export function stageText(state, reducedMotion = false) {
  if (state.view !== 'capture') return { line: state.view === 'processing' ? 'checking' : null, sub: '' };
  if (state.challenge) return singleTurnText(state, reducedMotion);
  const n = state.cue?.count ?? 0;
  const hold = n >= 3 ? 'Almost done. Keep holding' : n ? 'Keep holding' : '';
  const frame = state.frame ? `Frame ${+state.frame.index} of ${+state.frame.total}` : '';
  return { line: state.cue?.pose ?? 'starting', sub: [hold, frame].filter(Boolean).join(' · ') };
}

// Single turn: hold, then one turn. The countdown is (total - index) x cadence from the SDK's
// frame events only, and only while turning; the last second says "Keep holding…", never 0.
// With reduced motion the ring and meter are hidden, so the frame count is written instead.
function singleTurnText(state, reducedMotion) {
  const frame = state.frame ? `Frame ${+state.frame.index} of ${+state.frame.total}` : '';
  if (state.phase !== 'move') {
    const note = state.challenge.glowNote ? GLOW_NOTE : '';
    return { line: 'still', sub: [note, reducedMotion ? frame : ''].filter(Boolean).join(' · ') };
  }
  const left = state.frame ? Math.ceil((state.frame.total - state.frame.index) * SINGLE_CADENCE_S) : null;
  const count = left === null ? '' : left <= 1 ? 'Keep holding…' : String(left);
  return { line: 'turn' + state.challenge.pose, sub: [TURN_LINE[state.turn] ?? '', reducedMotion ? frame : count].filter(Boolean).join(' · ') };
}

export function render(state, { guide, focus }) {
  const v = state.view, capture = v === 'capture', processing = v === 'processing', result = v === 'result';
  const success = result && state.outcome === 'success';
  const refused = result && REFUSED.includes(state.outcome);
  const verdict = success ? (state.mode === 'enroll' ? 'Face saved' : 'Verified')
    : refused ? (state.mode === 'enroll' ? 'Not saved' : 'Not verified') : result ? 'No result' : '';
  const busy = !!state.busy;
  let eyebrow = '', title = '', lead = '', body = '', action = '', note = '';

  if (v === 'loading') {
    eyebrow = 'Your account'; title = 'One<br><em>moment.</em>'; lead = 'Loading your account.';
  } else if (v === 'signin') {
    eyebrow = 'Your account'; title = 'Sign in to<br><em>your account.</em>'; lead = 'Sign in with your own account to set up or use your face check.';
    body = '<ol class="account-journey"><li><strong>Create your account</strong><span>Choose a username and password.</span></li><li><strong>Set up your face check</strong><span>Review consent, then follow the camera guide.</span></li></ol>';
    // New people first when self-registration is open: Create account leads, sign-in is the link. Same events either way.
    const off = busy ? ' disabled' : '';
    action = state.registration
      ? `<button class="primary" id="register"${off}>Create account${icon('arrow')}</button><button class="account-create" id="primary"${off}>I already have an account</button>`
      : primary('Sign in', busy);
    note = state.registration ? 'Already set up? Sign in.' : 'New to the team? Ask your administrator for an account.';
    if (!state.registration) body = '';
  } else if (v === 'unsupported') {
    eyebrow = 'Your browser'; title = 'This browser<br><em>can’t run the check.</em>';
    lead = 'A face check needs a camera and a current browser on a secure connection. Try the latest Chrome, Safari, Edge or Firefox on a phone or laptop with a camera.';
  } else if (v === 'unavailable') {
    eyebrow = 'Your account'; title = 'We couldn’t<br><em>load your account.</em>'; lead = 'Check your connection and try again. Nothing has started.';
    action = primary('Try again', busy);
  } else if (v === 'intro') {
    eyebrow = 'Your agreement'; title = 'Before<br><em>we start.</em>';
    lead = 'A face check saves a face template to your account: a set of numbers made from your face, not a photo. For this preview it is kept for up to 30 days. You can have it deleted at any time (see Privacy details).';
    body = `<ul class="briefing"><li>${icon('camera')}A camera and a well-lit space</li><li>${icon('face')}Under 20 seconds of guided capture</li><li>${icon('shield')}You can stop before submission</li></ul><p class="privacy-line">By continuing, you agree to a face template being saved to your account and used for your face checks. No recording is kept.</p><button class="privacy-link" id="privacy">Privacy details</button>`;
    action = primary('Agree & continue', busy); note = 'Your agreement is saved to your account. The camera stays off.';
  } else if (v === 'demo') {
    eyebrow = 'How it works'; title = 'How the<br> <em>check works.</em>';
    lead = 'Look straight and hold still until the screen says turn. Then turn your head a little, the way the screen says, and hold it. A small turn is enough: keep both eyes on the screen. The check starts by itself when your face is in place.';
    action = primary('Continue', busy) + '<button class="privacy-link" id="skip">Skip</button>';
    note = 'The camera is off. This is only a demonstration.';
  } else if (v === 'ready') {
    eyebrow = '01 / Prepare';
    title = state.mode === 'verify' ? 'Welcome<br><em>back.</em>' : 'Let’s get<br><em>you ready.</em>';
    lead = 'Face a light source and keep your phone at eye level. Your browser will ask for camera access.';
    body = (state.cameraError ? `<p class="notice" role="alert">${CAMERA_NOTICE[state.cameraError]}</p>` : '')
      + `<ul class="briefing"><li>${icon('light')}Face the light, avoid bright backlighting</li><li>${icon('camera')}Keep your phone steady at eye level</li></ul>`
      + '<button class="privacy-link" id="demo">Watch the demo</button><button class="privacy-link" id="privacy">Privacy details</button>';
    action = primary(state.cameraError ? 'Try camera again' : 'Enable camera', busy);
    note = 'The camera opens only after you choose to enable it.';
  } else if (success) {
    eyebrow = '03 / Passed';
    const [t, l, label] = SUCCESS[state.mode === 'enroll' ? 'enroll' : 'verify'];
    title = t; lead = l;
    body = `<div class="result-detail"><strong>Recording</strong>${{ saved: 'Saved separately, as you chose.', failed: 'Not saved. This does not affect your face check.', pending: 'Still being saved.' }[state.recording] ?? 'Not saved.'}</div>`;
    action = primary(label, busy); note = state.mode === 'enroll' ? 'Saved to your account.' : 'Confirmed for your account.';
  } else if (result) {
    const [t, l, label] = OUTCOMES[state.outcome] ?? OUTCOMES.server;
    eyebrow = refused ? '03 / Not passed' : 'No result';
    title = refused ? `${verdict}.<br> <em>${t}</em>` : t;
    lead = (state.outcome === 'liveness' && WHY_NOT_LIVE[state.scores?.reason]) || l;
    if (state.retryAfter) lead = `The service is busy right now. You can try again in ${+state.retryAfter} seconds.`;
    action = primary(label, busy, !!state.retryAfter); // asleep until RETRY_READY
    if (state.busy === 'recheck') body += '<p class="notice" role="status">Checking your existing result. No new check has started.</p>';
    note = ['uncertain', 'pending'].includes(state.outcome) ? 'Your submitted check stays separate from any new attempt.' : refused ? '' : 'Nothing was checked, so nothing was saved.';
  }

  // No account yet: no visual panel, step strip, clock or "linked to" line, and a plain title.
  const noAccount = ['loading', 'signin', 'unsupported', 'unavailable'].includes(v);
  $('visual').parentElement.hidden = noAccount;
  $('steps').hidden = noAccount;
  $('time-note').hidden = noAccount;
  $('linked-note').hidden = noAccount;
  $('experience-body').classList.toggle('solo', noAccount);
  $('page-title').textContent = state.mode === 'enroll' ? 'Set up your face check' : state.mode === 'verify' ? 'Confirm it’s you' : 'Face check';
  if (v === 'signin') $('account-line').textContent = 'Not signed in';
  else if (v === 'unavailable') $('account-line').textContent = 'Couldn’t check';

  // Camera on: the page steps back and the full-screen camera takes over (drawn further down).
  const stage = v === 'camera' || capture || processing;
  $('screen').innerHTML = stage ? '' : `<div class="screen-enter"><div class="eyebrow${success ? ' pass' : ''}">${eyebrow}</div><h2 tabindex="-1">${title}</h2><p class="lead">${lead}</p>${body}</div><div class="action-group">${action}<p class="action-note">${note}</p></div>`;
  if (result && state.requestId) {
    const ref = document.createElement('p'); ref.className = 'action-note'; ref.textContent = 'Reference ' + state.requestId;
    $('screen').querySelector('.action-group').append(ref);
  }

  const failedEarly = result && ['busy', 'expired', 'expiring', 'reauth', 'closed', 'consent', 'notenrolled'].includes(state.outcome);
  const progress = failedEarly ? 0 : capture || processing || (result && state.outcome === 'cancelled') ? 1 : result ? 2 : 0;
  $('steps').innerHTML = [['Prepare', 'Your camera & choices'], ['Face check', 'Follow the guide'], ['Result', 'Your next step']]
    .map(([label, desc], i) => `<li class="${i === progress ? 'current' : i < progress ? 'done' : ''}" ${i === progress ? 'aria-current="step"' : ''}><span class="step-number">${i < progress ? '✓' : `0${i + 1}`}</span><span>${label}<small>${desc}</small></span></li>`).join('');

  // The panel in the card: framing diagram, or the head figure while the demo is open. Camera always off here.
  const demo = v === 'demo';
  const single = capture && !!state.challenge;
  const pose = demo ? state.previewPose : single ? (state.phase === 'move' ? state.challenge.pose : 'forward') : capture ? state.cue?.pose ?? 'forward' : 'forward';
  guide.setPose(pose);
  // A result shows one big mark: a tick only for a server-confirmed pass, a cross for a server refusal.
  const marked = success || refused;
  $('visual').classList.toggle('preparation-mode', !demo && !stage && !marked);
  $('visual').classList.toggle('success', marked);
  $('visual').classList.toggle('refused', refused);
  $('completion-mark').hidden = !marked;
  $('completion-mark').querySelector('path').setAttribute('d', refused ? 'm15 15 18 18m0-18-18 18' : 'm12 25 8 8 17-18');
  $('experience-body').classList.toggle('visual-first', demo || marked);
  $('visual-label').textContent = demo ? 'MOVEMENT DEMO' : result ? 'RESULT' : 'BEFORE YOU BEGIN';
  $('visual-state').parentElement.hidden = result;
  $('time-note').innerHTML = icon('clock') + (result ? 'Result' : 'Under 20 sec capture');
  $('visual-cue').textContent = demo ? CUE_SAY[pose] : result ? '' : 'A little space. A clear view.';
  $('visual-note').textContent = demo ? 'Tap a movement to see it.' : result ? '' : 'Sit comfortably, with light in front of you.';
  $('pose-track').hidden = !demo;
  [...$('pose-track').children].forEach((el, i) => {
    const active = POSE_ORDER[i] === pose;
    el.classList.toggle('active', active);
    el.setAttribute('aria-pressed', String(active));
  });
  $('motion').hidden = !demo || !guide.available;

  // The full-screen camera: the face, one bold line, Start before the check, ✕ until it is sent.
  $('visual').classList.toggle('stage', stage);
  $('preview').hidden = !(stage && state.cameraLive);
  $('stage-ui').hidden = !stage;
  $('stage-ui').classList.toggle('checking', processing);
  const guided = v === 'camera' && !state.guideOff;
  const placed = !guided || state.framing === 'good';
  $('stage-ui').classList.toggle('placed', guided && placed);
  const loading = guided && state.guideLoading;
  const preparing = v === 'camera' && state.preparing;
  const waitingLine = loading ? 'loading' : state.notice === 'moved' ? 'moved' : preparing && placed ? 'starting' : guided ? state.framing ?? 'find' : v === 'camera' && state.cameraLive ? 'tap' : 'framing';
  const text = stageText(state, guide.reducedMotion);
  $('stage-cue').innerHTML = STAGE_CUE[text.line ?? waitingLine] + (text.sub ? `<small>${text.sub}</small>` : '');
  // Frame ring: one segment per SDK frame. Fill bar: decorative, for the two hold windows only.
  $('stage-ring').setAttribute('pathLength', state.frame?.total ?? 12);
  $('stage-ring').setAttribute('stroke-dasharray', state.frame ? Array(state.frame.index).fill('.82 .18').join(' ') + ' 0 ' + state.frame.total : '0 12');
  $('visual').classList.toggle('capture', capture);
  $('stage-ui').classList.toggle('preparing', preparing);
  $('stage-ui').classList.toggle('settling', capture && (single ? state.phase !== 'move' : state.cue?.pose === 'forward'));
  // A glow challenge: the colour around the oval (main.js paints --glow) replaces the hold bar.
  $('stage-ui').classList.toggle('glowing', single && state.challenge.glow === true);
  // The hold bar lasts as long as the engine's own hold (settle_ms), not a CSS guess.
  $('stage-ui').style.setProperty('--settle-ms', single && Number.isFinite(state.challenge.settleMs) ? `${+state.challenge.settleMs}ms` : '3.2s');
  // Single turn: an arrow on the side to turn to (preview mirrored: participant-left = screen-left),
  // and the turn meter. Both decorative; the line above always says the direction in words.
  const turning = single && state.phase === 'move';
  $('stage-arrow').hidden = !turning;
  $('stage-arrow').classList.toggle('right', turning && state.challenge.pose === 'right');
  $('turn-meter').hidden = !turning;
  $('turn-meter').dataset.zone = turning ? state.turn ?? 'more' : '';
  // With the face guide the check starts itself (flow.js auto-start). Start is only the
  // fallback for when the guide could not load or run.
  $('stage-actions').innerHTML = v === 'camera' && !state.preparing
    ? (guided ? '<p class="stage-note">The check starts by itself when your face is in place.</p>' : primary('Start', busy)) : '';
  $('stop').hidden = processing;
  $('stop').setAttribute('aria-label', capture ? 'Stop face check' : 'Close camera');
  document.body.classList.toggle('stage-on', stage);
  document.querySelectorAll(BEHIND).forEach((el) => { el.inert = stage; });

  const spoken = (key) => STAGE_CUE[key].replaceAll('<br>', '');
  $('announcement').textContent = single ? [spoken(text.line), TURN_LINE[state.turn] ?? '', state.phase !== 'move' && state.challenge.glowNote ? GLOW_NOTE : ''].filter(Boolean).join(' ')
    : capture ? (state.cue ? CUE_SAY[state.cue.pose] : '') : processing ? 'Checking your result.'
    : v === 'camera' ? spoken(waitingLine)
    : result ? verdict + '.' : '';
  if (focus) (stage ? $('stage-cue') : $('screen').querySelector('h2')).focus({ preventScroll: true });
}

export function renderMotion(guide) {
  const b = $('motion');
  b.innerHTML = icon(guide.paused ? 'play' : 'pause');
  const label = guide.reducedMotion ? 'Reduced motion enabled' : guide.paused ? 'Play illustration' : 'Pause illustration';
  b.disabled = guide.reducedMotion || !guide.available;
  b.setAttribute('aria-label', label); b.title = label;
}
