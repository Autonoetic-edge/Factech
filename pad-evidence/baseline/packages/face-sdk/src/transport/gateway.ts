import {
  CHALLENGE_TIMEOUT_MS, DEFAULT_CHALLENGE_LIFE_MS, MAX_RETRY_AFTER_MS, SCAN_TIMEOUT_MS,
} from '../constants.ts';
import { CHALLENGE_ACTIONS } from '../types.ts';
import type {
  Challenge, ChallengeAction, Environment, LivenessVerdict, Operation,
} from '../types.ts';

const NONCE_RE = /^[0-9a-f]{32}$/;

const REQUEST_ID_RE = /^[A-Za-z0-9-]{8,64}$/;

const USER_ID_RE = /^[a-z0-9._-]{1,64}$/;

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

export function resolveGatewayUrl(raw: string | undefined): string {
  if (raw === undefined || raw === '') return '';
  if (raw.startsWith('/')) {

    if (raw.startsWith('//')) throw new Error('gatewayUrl: protocol-relative URLs are not allowed');
    if (raw.includes('?') || raw.includes('#')) throw new Error('gatewayUrl: no query or fragment');
    return raw.replace(/\/+$/, '');
  }
  let url: URL;
  try { url = new URL(raw); } catch { throw new Error('gatewayUrl: not a URL'); }
  if (url.username || url.password) throw new Error('gatewayUrl: no credentials in the URL');
  if (url.search || url.hash) throw new Error('gatewayUrl: no query or fragment');
  if (url.protocol === 'http:') {
    if (!LOCAL_HOSTS.has(url.hostname)) throw new Error('gatewayUrl: http is allowed for localhost only');
  } else if (url.protocol !== 'https:') {
    throw new Error('gatewayUrl: must be https (or http on localhost)');
  }
  return (url.origin + url.pathname).replace(/\/+$/, '');
}

export function canonicalUserId(raw: string): string {
  return raw.trim().toLowerCase();
}

export function isValidUserId(id: string): boolean {
  return USER_ID_RE.test(id);
}

export interface TransportOk<T> {
  readonly ok: true;
  readonly value: T;
  readonly requestId: string | null;
}

export interface TransportErr {
  readonly ok: false;
  readonly kind: 'NETWORK' | 'TIMEOUT' | 'CANCELLED' | 'BAD_RESPONSE' | 'REJECTED';
  readonly message: string;
  readonly engineCode: string | null;
  readonly httpStatus: number | null;
  readonly retryAfterMs: number | null;
  readonly requestId: string | null;
}
export type Transported<T> = TransportOk<T> | TransportErr;

export interface GatewayConfig {
  readonly base: string;
  readonly env: Environment;
}

export interface RawReply {
  readonly status: number;
  readonly requestId: string | null;
  readonly retryAfterMs: number | null;
  readonly body: unknown;
  readonly bodyIsJson: boolean;
}

export type TransportEnv = Pick<Environment, 'fetch' | 'setTimeout' | 'clearTimeout'>;

export function rawRequest(
  env: TransportEnv,
  base: string,
  method: string,
  path: string,
  timeoutMs: number,
  runSignal: AbortSignal,
  body?: unknown,
): Promise<Transported<RawReply>> {
  const init: RequestInit = { method };
  if (body !== undefined && body !== null) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  return request({ base, env }, path, timeoutMs, runSignal, init);
}

async function request(
  cfg: { readonly base: string; readonly env: TransportEnv },
  path: string,
  timeoutMs: number,
  runSignal: AbortSignal,
  init: RequestInit,
): Promise<Transported<RawReply>> {
  const ac = new AbortController();
  let timedOut = false;
  const onAbort = (): void => ac.abort();
  const timer = cfg.env.setTimeout(() => { timedOut = true; ac.abort(); }, timeoutMs);
  runSignal.addEventListener('abort', onAbort, { once: true });
  if (runSignal.aborted) ac.abort();

  let requestId: string | null = null;
  try {
    const res = await cfg.env.fetch(cfg.base + path, {
      ...init,
      signal: ac.signal,

      credentials: 'same-origin',
      cache: 'no-store',

      redirect: 'error',
      referrerPolicy: 'no-referrer',
    });
    requestId = headerId(res);
    let body: unknown = null;
    let bodyIsJson = true;
    try {
      body = await res.json();
    } catch (e) {
      if (timedOut || runSignal.aborted || !(e instanceof SyntaxError)) throw e;
      bodyIsJson = false;
    }
    return {
      ok: true,
      value: { status: res.status, requestId, retryAfterMs: retryAfter(res), body, bodyIsJson },
      requestId,
    };
  } catch (e) {
    if (timedOut) return err('TIMEOUT', 'request timed out after ' + timeoutMs + 'ms', { requestId });
    if (runSignal.aborted) return err('CANCELLED', 'request aborted', { requestId });
    return err('NETWORK', 'request failed: ' + (e instanceof Error ? e.name : 'unknown'), { requestId });
  } finally {
    cfg.env.clearTimeout(timer);
    runSignal.removeEventListener('abort', onAbort);
  }
}

function err(
  kind: TransportErr['kind'],
  message: string,
  extra?: Partial<Omit<TransportErr, 'ok' | 'kind' | 'message'>>,
): TransportErr {
  return {
    ok: false, kind, message,
    engineCode: extra?.engineCode ?? null,
    httpStatus: extra?.httpStatus ?? null,
    retryAfterMs: extra?.retryAfterMs ?? null,
    requestId: extra?.requestId ?? null,
  };
}

function headerId(res: { headers: Headers }): string | null {
  const v = res.headers.get('X-Request-Id');
  return v !== null && REQUEST_ID_RE.test(v) ? v : null;
}

function retryAfter(res: { headers: Headers }): number | null {
  const v = res.headers.get('Retry-After');
  if (v === null) return null;
  const text = v.trim();

  if (text === '') return null;
  const secs = Number(text);
  if (!Number.isFinite(secs) || secs < 0) return null;
  return Math.min(Math.round(secs * 1000), MAX_RETRY_AFTER_MS);
}

function record(v: unknown): Record<string, unknown> | null {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return null;
  return v as Record<string, unknown>;
}
function finite(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function envelopeCode(body: unknown): string | null {
  const error = record(record(body)?.['error']);
  const code = error?.['code'];
  return typeof code === 'string' && /^[A-Z][A-Z0-9_]{2,39}$/.test(code) ? code : null;
}

export const ENGINE_MESSAGE_MAX = 200;

export function engineMessage(body: unknown, userId?: string | null): string | null {
  const m = record(record(body)?.['error'])?.['message'];
  if (typeof m !== 'string') return null;
  let text = m.replace(/[\x00-\x1f\x7f]+/g, ' ');
  if (userId) text = text.split(userId).join('<user>');
  text = text.replace(/[0-9a-f]{16,}/gi, '<hex>').trim();
  if (!text) return null;
  return text.length > ENGINE_MESSAGE_MAX ? text.slice(0, ENGINE_MESSAGE_MAX) + '...' : text;
}

function rejected(reply: RawReply, userId?: string | null): TransportErr {
  const code = envelopeCode(reply.body);

  const said = code ? engineMessage(reply.body, userId) : null;
  return err('REJECTED', 'gateway returned ' + reply.status + (code ? ' ' + code : '') +
    (said ? ': ' + said : ''), {
    engineCode: code, httpStatus: reply.status,
    retryAfterMs: reply.retryAfterMs, requestId: reply.requestId,
  });
}
function badShape(reply: RawReply, what: string): TransportErr {
  return err('BAD_RESPONSE', 'response does not match the contract: ' + what,
    { httpStatus: reply.status, requestId: reply.requestId });
}

const ACTIONS: ReadonlySet<string> = new Set<string>(CHALLENGE_ACTIONS);

function validateParams(v: unknown): Record<string, number> | null | 'bad' {
  if (v === undefined || v === null) return null;
  const map = record(v);
  if (!map) return 'bad';
  const out: Record<string, number> = {};
  for (const [k, raw] of Object.entries(map)) {
    const n = finite(raw);
    if (n === null) return 'bad';
    out[k] = n;
  }
  return out;
}

function challengeLifeMs(body: Record<string, unknown>): number {
  const issued = finite(body['issued_ms']), expires = finite(body['expires_ms']);
  if (issued === null || expires === null) return DEFAULT_CHALLENGE_LIFE_MS;
  const life = expires - issued;
  return life > 0 ? Math.min(life, DEFAULT_CHALLENGE_LIFE_MS) : DEFAULT_CHALLENGE_LIFE_MS;
}

export async function getChallenge(
  cfg: GatewayConfig,
  runSignal: AbortSignal,
): Promise<Transported<Challenge>> {
  const reply = await request(cfg, '/v1/challenge', CHALLENGE_TIMEOUT_MS, runSignal, { method: 'GET' });
  if (!reply.ok) return reply;
  const raw = reply.value;
  if (raw.status !== 200) return rejected(raw);
  const body = record(raw.body);
  if (!raw.bodyIsJson) return badShape(raw, 'body is not valid JSON');
  if (!body) return badShape(raw, 'challenge body is not a map');
  const nonce = body['nonce'];
  if (typeof nonce !== 'string' || !NONCE_RE.test(nonce)) return badShape(raw, 'nonce is not 32 lowercase hex');
  const action = body['action'];
  if (typeof action !== 'string' || !ACTIONS.has(action)) return badShape(raw, 'action is not in the contract 1.4 vocabulary');
  const params = validateParams(body['params']);
  if (params === 'bad') return badShape(raw, 'params is not a map of finite numbers');
  return {
    ok: true, requestId: raw.requestId,
    value: { nonce, action: action as ChallengeAction, params, lifeMs: challengeLifeMs(body) },
  };
}

export interface EnrollBody {
  readonly templateId: string;
  readonly quality: number | null;
  readonly liveness: LivenessVerdict;
}
export interface VerifyBody {
  readonly match: boolean;
  readonly score: number;
  readonly threshold: number;
  readonly liveness: LivenessVerdict;
}

export function livenessVerdict(v: unknown): LivenessVerdict {
  const l = record(v);
  if (!l || typeof l['live'] !== 'boolean') return 'unknown';
  if (l['enforced'] !== true) return 'not-enforced';
  return l['live'] === true ? 'passed' : 'failed';
}

function scanBody(userId: string, facescanB64: string): Record<string, string> {
  return { user_id: userId, facescan: facescanB64 };
}

export async function postScan(
  cfg: GatewayConfig,
  op: Operation,
  userId: string,
  facescanB64: string,
  captureMeta: Readonly<Record<string, string | number | boolean>> | null,
  runSignal: AbortSignal,
): Promise<Transported<EnrollBody | VerifyBody>> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };

  if (captureMeta) headers['X-Capture-Meta'] = JSON.stringify(captureMeta);
  const reply = await request(cfg, op === 'enroll' ? '/v1/enroll' : '/v1/verify',
    SCAN_TIMEOUT_MS, runSignal, {
      method: 'POST', headers, body: JSON.stringify(scanBody(userId, facescanB64)),
    });
  if (!reply.ok) return reply;
  const raw = reply.value;
  if (raw.status !== 200) return rejected(raw, userId);
  const body = record(raw.body);
  if (!raw.bodyIsJson) return badShape(raw, 'body is not valid JSON');
  if (!body) return badShape(raw, 'body is not a map');
  const liveness = livenessVerdict(body['liveness']);

  if (op === 'enroll') {
    const templateId = body['template_id'];
    if (typeof templateId !== 'string' || templateId === '') return badShape(raw, 'template_id is not a non-empty string');

    const quality = finite(record(body['quality'])?.['score']);
    return { ok: true, requestId: raw.requestId, value: { templateId, quality, liveness } };
  }
  const match = body['match'];

  if (typeof match !== 'boolean') return badShape(raw, 'match is not a boolean');
  const score = finite(body['score']), threshold = finite(body['threshold']);
  if (score === null || threshold === null) return badShape(raw, 'score or threshold is not a finite number');
  return { ok: true, requestId: raw.requestId, value: { match, score, threshold, liveness } };
}
