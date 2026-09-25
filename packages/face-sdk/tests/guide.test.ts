import assert from 'node:assert/strict';
import { test } from 'node:test';

import { CAPTURE_MS, DEFAULT_SETTLE_MS, FRAME_COUNT, GUIDE_TICK_MS, LANDING_MS } from '../src/constants.ts';
import {
  GUIDE_CAL, OVAL_END, OVAL_MARGIN, OVAL_START, START_MAX, TARGET_MAX,
  createGuide, guideStep, instructionOf, median, ovalPlan, settleMsOf, targetOf,
} from '../src/challenge/guide.ts';
import type { Challenge } from '../src/types.ts';

const challenge = (params: Record<string, number> | null): Challenge =>
  ({ nonce: 'c'.repeat(32), action: 'MOVE_CLOSER', params, lifeMs: 30_000 });

test('settle_ms and target come from the challenge, not from the UI', () => {
  const ch = challenge({ settle_ms: 1450, target: 0.319 });
  assert.equal(settleMsOf(ch), 1450);
  assert.equal(targetOf(ch), 0.319);
});

test('a challenge with no params falls back to the pre-S7 pacing rather than a NaN', () => {
  for (const params of [null, {}, { settle_ms: 0 }, { settle_ms: -5, target: -1 }]) {
    const ch = challenge(params);
    assert.equal(settleMsOf(ch), DEFAULT_SETTLE_MS);
    assert.equal(targetOf(ch), null);
    const plan = ovalPlan(ch);
    assert.ok(Number.isFinite(plan.from) && Number.isFinite(plan.to));
  }
});

test('the oval carries the target as a GROWTH RATIO from the size the user lined up with', () => {
  for (const target of [0.22, 0.28, TARGET_MAX]) {
    const plan = ovalPlan(challenge({ settle_ms: 1000, target }));
    assert.equal(plan.from, OVAL_START, 'the start is the preflight oval, so the real baseline is not moved');
    assert.ok(Math.abs(plan.to / plan.from - Math.exp(target) * OVAL_MARGIN) < 1e-12);
    assert.ok(plan.to <= OVAL_END + 1e-12, 'the end size stays inside the 300x400 view');
    assert.ok(Math.log(plan.to / plan.from) >= target * GUIDE_CAL,
      'following the oval from its start reaches the point the SDK guide counts as done');
  }
  const gentle = ovalPlan(challenge({ settle_ms: 1000, target: 0.22 }));
  const steep = ovalPlan(challenge({ settle_ms: 1000, target: TARGET_MAX }));
  assert.ok(steep.to > gentle.to, 'a bigger approach ends closer');
  assert.ok(Math.abs(steep.to - OVAL_END) < 1e-12);
});

test('the instruction is exactly what the UI needs and nothing about the nonce', () => {
  const ch = challenge({ settle_ms: 1450, target: 0.319 });
  const instruction = instructionOf(ch);
  assert.deepEqual(Object.keys(instruction).sort(), ['action', 'oval', 'settleMs', 'target']);
  assert.equal(instruction.action, 'MOVE_CLOSER');
  assert.equal(instruction.settleMs, 1450);
  assert.equal(JSON.stringify(instruction).includes(ch.nonce), false);
});

test('median', () => {
  assert.equal(median([]), null);
  assert.equal(median([3]), 3);
  assert.equal(median([3, 1]), 2);
  assert.equal(median([5, 1, 3]), 3);
  assert.equal(median([4, 1, 3, 2]), 2.5);
});

test('the hold phase collects, reports no progress, and ends at settle_ms', () => {
  const G = createGuide(challenge({ settle_ms: 1000, target: 0.3 }));
  for (const t of [0, 200, 400, 600, 800, 999]) {
    guideStep(G, t, 0.4);
    assert.equal(G.phase, 'hold');
    assert.equal(G.progress, null, 'there is nothing to measure against yet');
  }
  assert.equal(G.hold.length, 6);
  guideStep(G, 1000, 0.4);
  assert.equal(G.phase, 'move');
});

test('the baseline is the hold window\'s median, so one bad read cannot set it', () => {
  const G = createGuide(challenge({ settle_ms: 500, target: 0.3 }));
  for (const h of [0.40, 0.41, 0.90, 0.40, 0.39]) guideStep(G, 100, h);
  guideStep(G, 600, 0.40);
  assert.equal(G.baseline, 0.40);
});

test('progress is the achieved log gain over the asked one, calibrated', () => {
  const target = 0.3;
  const G = createGuide(challenge({ settle_ms: 500, target }));
  guideStep(G, 0, 0.40);
  guideStep(G, 600, 0.40 * Math.exp(target * GUIDE_CAL));
  assert.ok(Math.abs(G.progress! - 1) < 1e-12);
  assert.equal(G.phase, 'done');
});

test('done latches: falling back does not un-finish a movement already made', () => {
  const G = createGuide(challenge({ settle_ms: 500, target: 0.3 }));
  guideStep(G, 0, 0.40);
  guideStep(G, 600, 0.40 * Math.exp(0.3 * GUIDE_CAL) * 1.1);
  assert.equal(G.done, true);
  guideStep(G, 700, 0.40);
  assert.equal(G.done, true);
  assert.equal(G.phase, 'done');
});

test('with no measurement at all the guide still phases, and never claims progress', () => {

  const G = createGuide(challenge({ settle_ms: 900, target: 0.3 }));
  guideStep(G, 0, null);
  assert.equal(G.phase, 'hold');
  guideStep(G, 1000, null);
  assert.equal(G.phase, 'move');
  assert.equal(G.progress, null);
  assert.equal(G.done, false);
});

test('f09 f11 full sweep: with no measurement, every tick of every recording is hold then move, never done', () => {

  const span = (FRAME_COUNT - 1) * CAPTURE_MS + LANDING_MS;
  for (const params of [{ settle_ms: 900, target: 0.22 }, { settle_ms: 1600, target: 0.3 },
    { settle_ms: 2400, target: TARGET_MAX }, null]) {
    const G = createGuide(challenge(params));
    const settle = settleMsOf(challenge(params));
    const seen = new Set<string>();
    for (let t = 0; t <= span; t += GUIDE_TICK_MS) {
      for (const reading of [null, undefined, 0, -1, Number.NaN]) {
        guideStep(G, t, reading as number | null | undefined);
        const where = `settle=${settle} t=${t} reading=${String(reading)}`;
        assert.equal(G.phase, t < settle ? 'hold' : 'move', where);
        assert.equal(G.progress, null, where);
        assert.equal(G.done, false, where);
        assert.equal(G.baseline, null, where);
        seen.add(G.phase);
      }
    }
    assert.equal(G.hold.length, 0, 'nothing unusable was collected as a reading');
    assert.deepEqual([...seen].sort(), ['hold', 'move'], `settle=${settle} visited both phases`);
  }
});

test('a hold window with no usable reading leaves no baseline to divide by', () => {
  const G = createGuide(challenge({ settle_ms: 500, target: 0.3 }));
  guideStep(G, 100, null);
  guideStep(G, 600, 0.5);
  assert.equal(G.baseline, null);
  assert.equal(G.progress, null);
});

test('a target of null (no params) never produces progress or a done', () => {
  const G = createGuide(challenge(null));
  guideStep(G, 0, 0.4);
  guideStep(G, DEFAULT_SETTLE_MS + 1, 0.9);
  assert.equal(G.progress, null);
  assert.equal(G.done, false);
});

test('the start gate leaves room for the largest target the engine draws', () => {

  assert.ok(START_MAX * Math.exp(TARGET_MAX * GUIDE_CAL) <= 0.92 + 1e-12);
  assert.ok(START_MAX * 1.01 * Math.exp(TARGET_MAX * GUIDE_CAL) > 0.92);
});

test('growth just short of the asked target is still move, and just over it is done', () => {

  const G = createGuide(challenge({ settle_ms: 1500, target: 0.3 }));
  guideStep(G, 0, 0.50);
  guideStep(G, 500, 0.51);
  guideStep(G, 1000, 0.49);
  assert.equal(G.phase, 'hold');
  guideStep(G, 2000, 0.515);
  assert.equal(G.phase, 'move');
  assert.equal(G.baseline, 0.50);
  const needed = 0.50 * Math.exp(0.3 * GUIDE_CAL);
  guideStep(G, 3000, needed - 0.01);
  assert.equal(G.phase, 'move');
  guideStep(G, 3500, needed + 0.001);
  assert.equal(G.phase, 'done');
});

test('a face crossing the old fixed 70% line has made almost none of the movement', () => {

  const G = createGuide(challenge({ settle_ms: 900, target: 0.22 }));
  guideStep(G, 0, 0.69);
  guideStep(G, 1000, 0.71);
  assert.equal(G.phase, 'move');
  assert.ok(G.progress !== null && G.progress < 0.2, String(G.progress));
});
