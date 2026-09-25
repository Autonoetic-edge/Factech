import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  assertLive, bindLifecycle, createRunController, isCancelled, sleep, withCap,
} from '../src/workflow/run.ts';
import type { Run } from '../src/workflow/run.ts';
import { fakeClock } from './helpers.ts';

test('a run token snapshots the operation and the id, and is frozen', () => {
  const ctl = createRunController();
  const run = ctl.start('verify', 's-01');
  assert.equal(run.op, 'verify');
  assert.equal(run.userId, 's-01');
  assert.throws(() => { (run as { op: string }).op = 'enroll'; }, TypeError);
});

test('only the current run is live', () => {
  const ctl = createRunController();
  const first = ctl.start('verify', 's-01');
  const second = ctl.start('enroll', 's-02');
  assert.equal(ctl.live(first), false);
  assert.equal(ctl.live(second), true);
  assert.equal(ctl.live(null), false);
  assert.equal(ctl.current(), second);
});

test('starting a run supersedes the one before it, with that reason', () => {
  const reasons: string[] = [];
  const ctl = createRunController({ onCancel: (_r, reason) => reasons.push(reason) });
  ctl.start('verify', 's-01');
  ctl.start('enroll', 's-02');
  assert.deepEqual(reasons, ['superseded']);
});

test('assertLive throws a cancellation a caller can recognise', () => {
  const ctl = createRunController();
  const run = ctl.start('verify', 's-01');
  assert.doesNotThrow(() => assertLive(ctl, run));
  ctl.cancel('user');
  try {
    assertLive(ctl, run);
    assert.fail('should have thrown');
  } catch (e) {
    assert.ok(isCancelled(e));
  }
  assert.equal(isCancelled(new Error('other')), false);
});

test('what a run owns is released when it ends, and only once', () => {
  const ctl = createRunController();
  const run = ctl.start('verify', 's-01');
  let released = 0;
  assert.equal(ctl.own(run, () => { released++; }), true);
  ctl.cancel('user');
  assert.equal(released, 1);
  ctl.cancel('user');
  assert.equal(released, 1);
});

test('a resource acquired by a run that is already dead is released at once', () => {

  const ctl = createRunController();
  const run = ctl.start('verify', 's-01');
  ctl.cancel('user');
  let released = 0;
  assert.equal(ctl.own(run, () => { released++; }), false);
  assert.equal(released, 1, 'own() returning false means "already released", not "dropped"');
});

test('a release that throws does not stop the others', () => {
  const ctl = createRunController();
  const run = ctl.start('verify', 's-01');
  let second = 0;
  ctl.own(run, () => { throw new Error('boom'); });
  ctl.own(run, () => { second++; });
  ctl.cancel('user');
  assert.equal(second, 1);
});

test('guard ends the run and releases what it owned, on success and on failure', async () => {
  for (const throws of [false, true]) {
    const ctl = createRunController();
    const run = ctl.start('verify', 's-01');
    let released = 0;
    ctl.own(run, () => { released++; });
    const body = async (): Promise<string> => {
      if (throws) throw new Error('nope');
      return 'done';
    };
    await ctl.guard(run, body).catch(() => undefined);
    assert.equal(released, 1, throws ? 'after a throw' : 'after a return');
    assert.equal(ctl.current(), null);
  }
});

test('a stale run\'s guard does not run the page-wide cleanup (f-stale-cleanup)', async () => {

  const events: string[] = [];
  const ctl = createRunController({
    onCancel: (run, reason) => events.push(`cancel ${run.id} ${reason}`),
    cleanup: (run, reason) => events.push(`cleanup ${run.id} ${reason}`),
  });
  const a = ctl.start('verify', 's-01');
  ctl.own(a, () => events.push(`release ${a.id}`));
  let resolveA: (() => void) | null = null;
  const pendingA = ctl.guard(a, () => new Promise<void>((r) => { resolveA = r; }));

  const b = ctl.start('enroll', 's-02');
  events.push(`start ${b.id}`);
  ctl.own(b, () => events.push(`release ${b.id}`));
  assert.deepEqual(events, [
    `release ${a.id}`, `cancel ${a.id} superseded`, `cleanup ${a.id} superseded`, `start ${b.id}`,
  ]);

  resolveA!();
  await pendingA;
  events.push(`A finished`);
  assert.equal(ctl.current(), b, 'B is still the current run');
  assert.equal(ctl.live(b), true);

  await ctl.guard(b, async () => undefined);
  assert.deepEqual(events, [
    `release ${a.id}`, `cancel ${a.id} superseded`, `cleanup ${a.id} superseded`, `start ${b.id}`,
    'A finished', `release ${b.id}`, `cleanup ${b.id} finished`,
  ]);
  assert.equal(ctl.current(), null);
});

test('a cancel, then a new run, then the old body ending: the same order holds (f-stale-cleanup)', async () => {

  const cleaned: [number, string][] = [];
  const ctl = createRunController({ cleanup: (run, reason) => cleaned.push([run.id, reason]) });
  const a = ctl.start('verify', 's-01');
  let finishA: (() => void) | null = null;
  const bodyA = ctl.guard(a, () => new Promise<void>((r) => { finishA = r; }));
  ctl.cancel('navigated');
  const b = ctl.start('verify', 's-02');
  assert.deepEqual(cleaned, [[a.id, 'navigated']], 'no superseded entry: A was already gone');
  finishA!();
  await bodyA;
  assert.deepEqual(cleaned, [[a.id, 'navigated']], 'A\'s stale finally cleaned up B');
  await ctl.guard(b, async () => undefined);
  assert.deepEqual(cleaned, [[a.id, 'navigated'], [b.id, 'finished']]);
});

test('a run that is still live at the end of its body does clean up', async () => {
  const cleaned: string[] = [];
  const ctl = createRunController({ cleanup: (_run, reason) => cleaned.push(reason) });
  const run = ctl.start('verify', 's-01');
  await ctl.guard(run, async () => undefined);
  assert.deepEqual(cleaned, ['finished']);
});

test('a stale run\'s own resources are still released by its own finally', async () => {
  const ctl = createRunController();
  const a = ctl.start('verify', 's-01');
  let aReleased = 0;
  ctl.own(a, () => { aReleased++; });
  let resolveA: (() => void) | null = null;
  const pendingA = ctl.guard(a, () => new Promise<void>((r) => { resolveA = r; }));
  ctl.start('enroll', 's-02');
  assert.equal(aReleased, 1, 'the supersede released A\'s own');
  resolveA!();
  await pendingA;
  assert.equal(aReleased, 1, 'and did not release it twice');
});

test('two runs\' resources never cross', async () => {
  const ctl = createRunController();
  const released: string[] = [];
  const a = ctl.start('verify', 's-01');
  ctl.own(a, () => released.push('a'));
  const b = ctl.start('enroll', 's-02');
  ctl.own(b, () => released.push('b'));
  assert.deepEqual(released, ['a']);
  await ctl.guard(b, async () => undefined);
  assert.deepEqual(released, ['a', 'b']);
});

test('end() finishes a current run quietly: its own things released, no hook called', () => {

  const hooks: string[] = [];
  const ctl = createRunController({
    onCancel: () => hooks.push('cancel'), cleanup: () => hooks.push('cleanup'),
  });
  const a = ctl.start('enroll', 's-01');
  let released = 0;
  ctl.own(a, () => { released++; });
  ctl.end(a);
  assert.equal(ctl.current(), null);
  assert.equal(ctl.live(a), false);
  assert.equal(released, 1);
  assert.deepEqual(hooks, []);

  const b = ctl.start('verify', 's-02');
  ctl.end(a);
  assert.equal(ctl.current(), b);
});

test('cancel with nothing running is a no-op', () => {
  const ctl = createRunController();
  assert.equal(ctl.cancel('user'), false);
});

function eventTarget() {
  const listeners = new Map<string, Set<() => void>>();
  return {
    hidden: false,
    addEventListener(type: string, fn: () => void) {
      const set = listeners.get(type);
      if (set) set.add(fn); else listeners.set(type, new Set([fn]));
    },
    removeEventListener(type: string, fn: () => void) { listeners.get(type)?.delete(fn); },
    fire(type: string) { for (const fn of [...(listeners.get(type) ?? [])]) fn(); },
    count() { let n = 0; for (const s of listeners.values()) n += s.size; return n; },
  };
}

test('a hidden page interrupts; a visible one does not', () => {
  const doc = eventTarget(), win = eventTarget();
  const reasons: string[] = [];
  const unbind = bindLifecycle(doc as never, win as never, (r) => reasons.push(r));
  doc.fire('visibilitychange');
  assert.deepEqual(reasons, [], 'becoming visible is not an interruption');
  doc.hidden = true;
  doc.fire('visibilitychange');
  win.fire('pagehide');
  assert.deepEqual(reasons, ['hidden', 'pagehide']);
  unbind();
  assert.equal(doc.count() + win.count(), 0);
  doc.fire('visibilitychange');
  assert.equal(reasons.length, 2, 'unbind means unbind');
});

test('sleep resolves at its deadline', async () => {
  const clock = fakeClock();
  const before = clock.now();
  await sleep(clock, 700, new AbortController().signal);
  assert.equal(clock.now() - before, 700);
});

test('sleep is cut short by an abort, and leaves no timer behind', async () => {
  const clock = fakeClock();
  const ac = new AbortController();
  const before = clock.now();
  const pending = sleep(clock, 60_000, ac.signal);
  ac.abort();
  await pending;
  assert.equal(clock.now() - before, 0);
  assert.equal(clock.pending, 0);
});

test('sleep with an already-aborted signal returns without waiting', async () => {
  const clock = fakeClock();
  const ac = new AbortController();
  ac.abort();
  await sleep(clock, 60_000, ac.signal);
  assert.equal(clock.pending, 0);
});

test('withCap waits for the hook, but never past the cap', async () => {
  const clock = fakeClock();
  const before = clock.now();
  await withCap(clock, 3000, () => new Promise<void>((r) => { clock.setTimeout(r, 500); }));
  assert.equal(clock.now() - before, 500);

  const t2 = clock.now();

  await withCap(clock, 3000, () => new Promise<void>(() => undefined));
  assert.equal(clock.now() - t2, 3000);
});

test('withCap tolerates a hook that throws, and one that returns nothing', async () => {
  const clock = fakeClock();
  await withCap(clock, 3000, () => { throw new Error('UI bug'); });
  await withCap(clock, 3000, () => Promise.reject(new Error('UI bug')));
  await withCap(clock, 3000, () => undefined);
  assert.equal(clock.pending, 0, 'a synchronous hook arms no timer to leak');
});

test('a cancel from inside the body cleans up once, under the cancel\'s own reason', async () => {

  const cleaned: string[] = [];
  const ctl = createRunController({ cleanup: (_run, reason) => cleaned.push(reason) });
  const a = ctl.start('verify', 's-01');
  await ctl.guard(a, async () => 'ok');
  const b = ctl.start('verify', 's-01');
  await assert.rejects(ctl.guard(b, async () => { throw new Error('capture blew up'); }));
  const c = ctl.start('verify', 's-01');
  await ctl.guard(c, async () => { ctl.cancel('navigated'); });
  assert.deepEqual(cleaned, ['finished', 'finished', 'navigated']);
  assert.equal(ctl.current(), null);
});
