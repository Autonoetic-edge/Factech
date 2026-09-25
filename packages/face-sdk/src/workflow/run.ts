import type { Operation } from '../types.ts';

export interface Run {
  readonly id: number;
  readonly op: Operation;
  readonly userId: string;
}

export interface RunHooks {

  readonly onCancel?: (run: Run, reason: string) => void;

  readonly cleanup?: (run: Run, reason: string) => void;
}

export interface RunController {
  start(op: Operation, userId: string): Run;
  live(run: Run | null): boolean;
  current(): Run | null;
  own(run: Run, release: () => void): boolean;

  end(run: Run): void;
  cancel(reason: string): boolean;
  guard<T>(run: Run, body: (run: Run) => Promise<T>): Promise<T>;
}

export function createRunController(hooks: RunHooks = {}): RunController {
  let seq = 0;
  let current: Run | null = null;
  const owned = new Map<number, (() => void)[]>();

  function release(run: Run): void {
    const list = owned.get(run.id);
    owned.delete(run.id);
    if (list) for (const fn of list) { try { fn(); } catch {                                   } }
  }

  const ctl: RunController = {
    start(op, userId) {
      if (current) ctl.cancel('superseded');
      current = Object.freeze({ id: ++seq, op, userId });
      return current;
    },
    live(run) { return !!run && !!current && current.id === run.id; },
    current() { return current; },
    end(run) { if (ctl.live(run)) { current = null; release(run); } },
    own(run, fn) {
      if (!ctl.live(run)) { try { fn(); } catch {                           } return false; }
      const list = owned.get(run.id);
      if (list) list.push(fn); else owned.set(run.id, [fn]);
      return true;
    },
    cancel(reason) {
      if (!current) return false;
      const run = current;
      current = null;
      release(run);
      hooks.onCancel?.(run, reason);
      hooks.cleanup?.(run, reason);
      return true;
    },
    async guard(run, body) {
      try {
        return await body(run);
      } finally {
        const wasLive = ctl.live(run);
        if (wasLive) current = null;
        release(run);
        if (wasLive) hooks.cleanup?.(run, 'finished');
      }
    },
  };
  return ctl;
}

export const RUN_CANCELLED = 'run-cancelled';

export interface CancelledError extends Error { readonly cancelled: true }

export function assertLive(ctl: RunController, run: Run): void {
  if (ctl.live(run)) return;
  const e = new Error(RUN_CANCELLED) as Error & { cancelled: true };
  e.cancelled = true;
  throw e;
}
export function isCancelled(e: unknown): e is CancelledError {
  return e instanceof Error && (e as { cancelled?: unknown }).cancelled === true;
}

export function bindLifecycle(
  doc: Pick<Document, 'addEventListener' | 'removeEventListener' | 'hidden'>,
  win: Pick<Window, 'addEventListener' | 'removeEventListener'>,
  onInterrupt: (reason: string) => void,
): () => void {
  const vis = (): void => { if (doc.hidden) onInterrupt('hidden'); };
  const hide = (): void => onInterrupt('pagehide');
  doc.addEventListener('visibilitychange', vis);
  win.addEventListener('pagehide', hide);
  return () => {
    doc.removeEventListener('visibilitychange', vis);
    win.removeEventListener('pagehide', hide);
  };
}

export function sleep(
  env: { setTimeout(fn: () => void, ms: number): unknown; clearTimeout(id: unknown): void },
  ms: number,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const done = (): void => {
      env.clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = env.setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
  });
}

export async function withCap(
  env: { setTimeout(fn: () => void, ms: number): unknown; clearTimeout(id: unknown): void },
  capMs: number,
  work: () => void | Promise<void>,
): Promise<void> {
  let timer: unknown;
  let armed = false;
  try {
    const p = work();
    if (!p) return;
    await Promise.race([p.catch(() => undefined), new Promise<void>((resolve) => {
      timer = env.setTimeout(resolve, capMs);
      armed = true;
    })]);
  } catch {                                                     } finally {
    if (armed) env.clearTimeout(timer);
  }
}
