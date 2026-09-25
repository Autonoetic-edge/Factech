// Single turn (engine CHALLENGE_POLICY=single-turn-v1), docs/LIVENESS_UPGRADE_PLAN.md Phase 1:
// the page switches screen on the engine's action alone, the SDK's events alone move it, and
// the pre-checks, auto-start and turn meter only guide.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { poseOfAction, poseOf, TURN_SIGN } from '../verify/cues.js';
import { initial, step } from '../verify/flow.js';
import {
  createFraming, framingStep, precheck, precheckReading, HINT_CAP_MS, SETTLE_MS,
  createTurnMeter, turnStep, ZONE_TICKS, yawOf,
} from '../verify/framing.js';
import { stageText, COPY } from '../verify/view.js';

const run = (events, state) => events.reduce((acc, e) => {
  const r = step(acc.state, e);
  return { state: r.state, effects: r.effect ? [...acc.effects, r.effect] : acc.effects };
}, { state, effects: [] });
const capture = () => ({ ...initial(), view: 'capture', busy: 'scan', cameraLive: true, mode: 'verify' });
const LEFT = { type: 'SDK_INSTRUCTION', pose: 'left', settleMs: 1500 };
const MOVE = { type: 'SDK_PHASE', phase: 'move' };
const frame = (index) => ({ type: 'SDK_FRAME', index, total: 12 });

test('the engine action alone picks the screen; HEAD_SEQUENCE keeps the sentence cues', () => {
  assert.equal(poseOfAction('LOOK_LEFT'), 'left');
  assert.equal(poseOfAction('LOOK_RIGHT'), 'right');
  assert.equal(poseOfAction('HEAD_SEQUENCE'), null);
  assert.equal(poseOfAction('MOVE_CLOSER'), null);
  assert.equal(poseOfAction(undefined), null);
  assert.equal(poseOf('Face forward. Hold still.'), 'forward'); // the sequence path is untouched
  assert.deepEqual(TURN_SIGN, { left: 1, right: -1 });
});

test('instruction -> hold -> move -> 12 frames; only SDK events move the phase', () => {
  let r = run([LEFT, { type: 'SDK_PHASE', phase: 'hold' }, frame(1), frame(2)], capture());
  assert.deepEqual(r.state.challenge, { pose: 'left', settleMs: 1500 });
  assert.equal(r.state.phase, 'hold');
  // a turn sample, a page tick or a click cannot start the turn
  r = run([{ type: 'TURN_SAMPLE', zone: 'good' }, { type: 'PRIMARY' }, { type: 'GUIDE', line: 'good' }], r.state);
  assert.equal(r.state.phase, 'hold'); assert.equal(r.state.turn, null); assert.deepEqual(r.effects, []);
  r = run([MOVE, frame(3), { type: 'TURN_SAMPLE', zone: 'good' }, { type: 'TURN_SAMPLE', zone: 'bogus' }], r.state);
  assert.equal(r.state.phase, 'move'); assert.equal(r.state.turn, 'good');
  for (let i = 4; i <= 12; i++) r = run([frame(i)], r.state);
  assert.deepEqual(r.state.frame, { index: 12, total: 12 });
  assert.equal(run([{ type: 'SDK_UPLOADING' }], r.state).state.view, 'processing');
  // a result clears everything the capture showed
  const done = run([{ type: 'RESULT', outcome: 'nomatch' }], r.state).state;
  assert.deepEqual([done.challenge, done.phase, done.turn, done.cue, done.frame], [null, null, null, null, null]);
});

test('stage text: hold line, the direction in words, countdown only while turning, never 0', () => {
  let s = run([LEFT, { type: 'SDK_PHASE', phase: 'hold' }, frame(2)], capture()).state;
  assert.deepEqual(stageText(s), { line: 'still', sub: '' });
  s = run([MOVE], s).state;
  const at = (i, turn) => stageText(run([frame(i), ...(turn ? [{ type: 'TURN_SAMPLE', zone: turn }] : [])], s).state);
  assert.equal(at(4).line, 'turnleft');
  assert.equal(at(4).sub, '4');
  assert.equal(at(6).sub, '3');
  assert.equal(at(8).sub, '2');
  assert.equal(at(10).sub, 'Keep holding…');
  assert.equal(at(12).sub, 'Keep holding…');
  assert.equal(at(7, 'far').sub, 'A little less. · 3');
  assert.match(COPY.STAGE_CUE.turnleft, /LEFT/);
  assert.match(COPY.STAGE_CUE.turnright, /RIGHT/);
  const right = run([{ ...LEFT, pose: 'right' }, MOVE, frame(5)], capture()).state;
  assert.equal(stageText(right).line, 'turnright');
  // reduced motion: no ring or meter on screen, so the frame count is written
  assert.equal(stageText(run([frame(7)], s).state, true).sub, 'Frame 7 of 12');
  assert.equal(stageText(run([LEFT, frame(1)], capture()).state, true).sub, 'Frame 1 of 12');
});

test('the sequence path still renders exactly as before', () => {
  const s = run([{ type: 'SDK_CUE', pose: 'left', text: 'x' }, frame(5)], capture()).state;
  assert.deepEqual(stageText(s), { line: 'left', sub: 'Keep holding · Frame 5 of 12' });
});

test('auto-start: "good" arms preparation once; no guide keeps the Start button', () => {
  const camera = { ...initial(), view: 'camera', cameraLive: true, framing: 'hold', mode: 'verify' };
  const armed = run([{ type: 'GUIDE', line: 'good' }], camera);
  assert.deepEqual(armed.effects, ['prepare']); assert.equal(armed.state.preparing, true);
  assert.deepEqual(run([{ type: 'GUIDE', line: 'good' }, { type: 'GUIDE', line: 'hold' }, { type: 'GUIDE', line: 'good' }], armed.state).effects, []);
  assert.equal(run([{ type: 'PREPARED' }], armed.state).state.view, 'capture');
  assert.deepEqual(run([{ type: 'GUIDE', line: 'closer' }], camera).effects, []);
  const off = { ...camera, guideOff: true };
  assert.deepEqual(run([{ type: 'GUIDE', line: 'good' }], off).effects, []);
  assert.deepEqual(run([{ type: 'PRIMARY' }], off).effects, ['startScan']);
});

test('pre-checks: one hint at a time, most important first', () => {
  const g = (o = {}) => ({ luma: 120, sharp: 80, ...o });
  assert.equal(precheck(g()), null);
  assert.equal(precheck(g({ luma: 20, sharp: 1 }), { yaw: 0.5 }), 'frontal');
  assert.equal(precheck(g({ luma: 20, sharp: 1 }), { yaw: 0.1 }), 'light');
  assert.equal(precheck(g({ luma: 90 }), { frameLuma: 200 }), 'light'); // backlight
  assert.equal(precheck(g({ luma: 90 }), { frameLuma: 120 }), null);
  assert.equal(precheck(g({ sharp: 3 })), 'steady');
  assert.equal(precheck({ luma: null, sharp: null }, {}), null); // nothing measured: no hint
});

test('pre-check hints replace "good", then a light or blur hint gives way after 10 s', () => {
  const F = createFraming();
  const dark = { cue: 'good', armed: true, luma: 20, sharp: 80 };
  assert.equal(framingStep(F, dark, 0), 'find');
  assert.equal(framingStep(F, dark, SETTLE_MS), 'light');
  assert.equal(framingStep(F, dark, HINT_CAP_MS - 1), 'light');
  assert.equal(framingStep(F, dark, HINT_CAP_MS), 'good'); // capped: the check may start
  // a guide problem (distance) still wins over the pre-checks, and resets the cap
  const F2 = createFraming();
  const far = { cue: 'far', armed: false, luma: 20 };
  framingStep(F2, far, 0); assert.equal(framingStep(F2, far, SETTLE_MS), 'closer');
  // frontal is never capped
  const F3 = createFraming();
  const turned = { cue: 'good', armed: true, luma: 120, sharp: 80 };
  framingStep(F3, turned, 0, { yaw: 0.6 });
  assert.equal(framingStep(F3, turned, HINT_CAP_MS * 2, { yaw: 0.6 }), 'frontal');
  assert.equal(framingStep(F3, turned, HINT_CAP_MS * 2 + 1, { yaw: 0 }), 'good');
});

test('turn meter: baseline from the hold, zones by size, smoothed, direction-aware, display only', () => {
  const M = createTurnMeter();
  for (const y of [0.05, 0.1, 0.0]) assert.equal(turnStep(M, y, 'hold', 1), null);
  assert.equal(turnStep(M, 0.2, 'move', 1), 'more'); // baseline 0.05
  let z;
  for (let i = 0; i < ZONE_TICKS; i++) z = turnStep(M, 0.6, 'move', 1);
  assert.equal(z, 'good');
  assert.equal(turnStep(M, 1.2, 'move', 1), 'good'); // one sample is not enough to change
  for (let i = 0; i < ZONE_TICKS; i++) z = turnStep(M, 1.2, 'move', 1);
  assert.equal(z, 'far');
  for (let i = 0; i < ZONE_TICKS; i++) z = turnStep(M, null, 'move', 1);
  assert.equal(z, 'lost');
  const R = createTurnMeter();
  turnStep(R, 0, 'hold', -1);
  turnStep(R, 0.6, 'move', -1); // the wrong way for a RIGHT turn
  assert.equal(R.zone, 'more');
  const W = createTurnMeter(); turnStep(W, 0, 'hold', -1);
  assert.equal(turnStep(W, -0.6, 'move', -1), 'good');
});

test('page yaw: one face only, same formula as the engine head gate', () => {
  const f = { videoW: 640, videoH: 480 };
  const kp = (nose) => [{ keypoints: [{ x: .4, y: .4 }, { x: .6, y: .4 }, { x: nose, y: .5 }] }];
  assert.equal(yawOf(kp(.5), f), 0);
  assert.ok(yawOf(kp(.55), f) > 0); // nose to image-right = participant's left
  assert.equal(yawOf([], f), null);
  assert.equal(yawOf([...kp(.5), ...kp(.5)], f), null);
});

test('pre-check reading: the numbers behind the hint, rounded, with the hint-cap state', () => {
  const F = createFraming();
  assert.deepEqual(precheckReading({ luma: 81.54, sharp: 33.06 }, { frameLuma: 140.23 }, F),
    { luma: 81.5, frame_luma: 140.2, backlight: 58.7, sharp: 33.1, hint: null, capped: false });
  F.hint = 'light';
  assert.equal(precheckReading({ luma: 40 }, {}, F).capped, true, 'a start with the light hint showing = the 10 s cap let it through');
  assert.deepEqual(precheckReading({}, {}, null),
    { luma: null, frame_luma: null, backlight: null, sharp: null, hint: null, capped: false });
});

// ---- Phase 4 glow (engine CHALLENGE_POLICY=single-turn-glow-v1): display only ----
import { glowOf, glowColour, reducedGlowColour } from '../verify/glow.js';

const GLOW_BODY = { glow: {
  version: 'glow-v1', neutral: [255, 255, 255], fade_ms: 400, end_ms: 2200,
  steps: [
    { colour: 'blue', rgb: [111, 168, 255], start_ms: 400 },
    { colour: 'amber', rgb: [255, 192, 97], start_ms: 1000 },
    { colour: 'green', rgb: [111, 224, 160], start_ms: 1550 },
  ],
} };
// engine/app/flash.py displayed() on the same schedule (computed with the engine, 24 Sep 2026).
const ENGINE_DISPLAYED = [[0, [255, 255, 255]], [399, [255, 255, 255]], [400, [255, 255, 255]], [600, [183, 212, 255]], [800, [111, 168, 255]], [1000, [111, 168, 255]], [1200, [183, 180, 176]], [1550, [255, 192, 97]], [1750, [183, 208, 128]], [2200, [111, 224, 160]], [2400, [183, 240, 208]], [2700, [255, 255, 255]]];

test('glow: the page shows what the engine expects at every offset from frame 0', () => {
  const g = glowOf(GLOW_BODY);
  for (const [t, rgb] of ENGINE_DISPLAYED) {
    glowColour(g, t).forEach((v, i) => assert.ok(Math.abs(v - rgb[i]) <= 1, `t=${t}: ${glowColour(g, t)} vs ${rgb}`));
  }
});

test('glow: absent or malformed schedule = no glow, so the page is unchanged', () => {
  assert.equal(glowOf(null), null);
  assert.equal(glowOf({ nonce: 'n', action: 'LOOK_LEFT', params: {} }), null);
  const bad = (patch, step = {}) => glowOf({ glow: { ...GLOW_BODY.glow, ...patch, steps: [{ ...GLOW_BODY.glow.steps[0], ...step }] } });
  assert.notEqual(bad({}), null);
  assert.equal(bad({ neutral: [255, 255] }), null);
  assert.equal(bad({}, { rgb: [300, 0, 0] }), null);
  assert.equal(bad({}, { start_ms: 2200 }), null); // must start before end_ms
  assert.equal(bad({ fade_ms: -1 }), null);
  assert.equal(glowOf({ glow: { ...GLOW_BODY.glow, steps: [] } }), null);
  assert.equal(glowOf({ glow: { ...GLOW_BODY.glow, steps: [GLOW_BODY.glow.steps[1], GLOW_BODY.glow.steps[0]] } }), null); // out of order
});

test('glow: reduced motion is one slow change to the first colour, on the schedule clock', () => {
  const g = glowOf(GLOW_BODY);
  assert.deepEqual(reducedGlowColour(g, 0), [255, 255, 255]);
  assert.deepEqual(reducedGlowColour(g, 400), [255, 255, 255]);
  assert.deepEqual(reducedGlowColour(g, 1300), [183, 212, 255]); // halfway, never another colour
  assert.deepEqual(reducedGlowColour(g, 2200), [111, 168, 255]);
  assert.deepEqual(reducedGlowColour(g, 5000), [111, 168, 255]); // holds: no second change
  // monotone: each channel only ever moves one way
  let prev = reducedGlowColour(g, 0);
  for (let t = 0; t <= 3000; t += 50) {
    const c = reducedGlowColour(g, t);
    c.forEach((v, i) => assert.ok(v <= prev[i] || g.steps[0].rgb[i] >= g.neutral[i]));
    prev = c;
  }
});

test('glow: the one-time line shows during the hold only; no glow keeps today\'s state exactly', () => {
  const GLOW = { ...LEFT, glow: true, glowNote: true };
  let s = run([GLOW, { type: 'SDK_PHASE', phase: 'hold' }, frame(2)], capture()).state;
  assert.deepEqual(s.challenge, { pose: 'left', settleMs: 1500, glow: true, glowNote: true });
  assert.deepEqual(stageText(s), { line: 'still', sub: COPY.GLOW_NOTE });
  assert.equal(COPY.GLOW_NOTE, 'The screen will glow softly while we check.');
  assert.deepEqual(stageText(s, true), { line: 'still', sub: `${COPY.GLOW_NOTE} · Frame 2 of 12` });
  assert.equal(stageText(run([MOVE, frame(4)], s).state).sub, '4');
  s = run([{ ...LEFT, glow: true, glowNote: false }], capture()).state;
  assert.deepEqual(stageText(s), { line: 'still', sub: '' });
  // no glow (or anything but true) = exactly the single-turn-v1 state
  for (const extra of [{}, { glow: false }, { glow: 'yes', glowNote: true }]) {
    assert.deepEqual(run([{ ...LEFT, ...extra }], capture()).state.challenge, { pose: 'left', settleMs: 1500 });
  }
});
