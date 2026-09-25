import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import http from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');
const BROWSERS = [
  process.env.FACETECH_BROWSER,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean);

const LIVE = { live: true, score: 0.9, enforced: true, failed_signals: [] };
const NOT_LIVE_TEXT = 'Your face matched, but the live-person check was not confirmed. You are not verified.';
const json = (status, body, id) => ({ status, body: JSON.stringify(body), id });

const CASES = [
  { name: 'verify: match + enforced liveness passed', reply: json(200, { match: true, score: 0.7, threshold: 0.5, liveness: LIVE }, 'req-browser-passed'),
    text: 'It is you. Verified.', tone: 'ok' },
  { name: 'verify: match + enforced liveness failed',
    reply: json(200, { match: true, score: 0.7, threshold: 0.5, liveness: { live: false, score: 0.2, enforced: true, failed_signals: ['challenge'] } }, 'req-browser-failed'),
    text: NOT_LIVE_TEXT, tone: 'alert' },
  { name: 'verify: match + liveness not enforced',
    reply: json(200, { match: true, score: 0.7, threshold: 0.5, liveness: { live: true, score: 0.9, enforced: false, failed_signals: [] } }, 'req-browser-unenforced'),
    text: NOT_LIVE_TEXT, tone: 'alert' },
  { name: 'verify: match + unknown liveness', reply: json(200, { match: true, score: 0.7, threshold: 0.5, liveness: { score: 0.9 } }, 'req-browser-unknown'),
    text: NOT_LIVE_TEXT, tone: 'alert' },
  { name: 'verify: match + missing liveness', reply: json(200, { match: true, score: 0.7, threshold: 0.5 }, 'req-browser-missing'),
    text: NOT_LIVE_TEXT, tone: 'alert' },
  { name: 'verify: no match', reply: json(200, { match: false, score: 0.1, threshold: 0.5, liveness: LIVE }, 'req-browser-nomatch'),
    text: 'That face did not match.', tone: 'alert' },
  { name: 'enroll: liveness passed', op: 'enroll',
    reply: json(200, { template_id: 'tpl-1', quality: { score: 0.8 }, liveness: LIVE }, 'req-browser-enroll'),
    text: 'You are enrolled.', tone: 'ok' },
  { name: 'enroll: liveness not enforced', op: 'enroll',
    reply: json(200, { template_id: 'tpl-1', quality: { score: 0.8 }, liveness: { live: true, score: 0.9, enforced: false, failed_signals: [] } }, 'req-browser-enroll-ne'),
    text: 'Your face was saved, but the live-person check was not confirmed.', tone: 'alert' },
  { name: 'error: LIVENESS_FAIL 422', reply: json(422, { error: { code: 'LIVENESS_FAIL', message: 'liveness check failed: challenge' } }, 'req-browser-lfail'),
    text: 'Hold still at the start, then come closer until your face fills the oval.', tone: 'alert' },
  { name: 'error: CHALLENGE_FAIL 422', reply: json(422, { error: { code: 'CHALLENGE_FAIL', message: 'reused_nonce' } }, 'req-browser-cfail'),
    text: 'That attempt timed out or was already used. Please try again.', tone: 'alert' },
  { name: 'error: 500 from the server', reply: json(500, { error: { code: 'INTERNAL', message: 'boom' } }, 'req-browser-500'),
    text: 'Something went wrong. Please try again.', tone: 'alert' },
  { name: 'error: invalid JSON on 200', reply: { status: 200, body: '{not json', id: 'req-browser-badjson' },
    text: 'Something went wrong on our side. Please try again.', tone: 'alert' },
  { name: 'cancel during recording', cancelAtPhase: true, reply: json(200, { match: true, score: 0.7, threshold: 0.5, liveness: LIVE }, 'req-browser-cancel'),
    text: 'Stopped.', tone: 'alert', noUpload: true },
  { name: 'camera permission denied', deny: true, reply: json(200, { match: true, score: 0.7, threshold: 0.5, liveness: LIVE }, 'req-browser-deny'),
    text: 'Camera access is blocked. Allow the camera in your browser settings and try again.', tone: 'alert', noUpload: true },
];

const STATIC = [
  ['/sdk/', path.join(REPO, 'packages', 'face-sdk', 'dist')],
  ['/shared/', path.join(HERE, 'shared')],
  ['/', path.join(HERE, 'integration-demo')],
];
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.map': 'application/json' };

function fixtureServer(state) {
  return http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const p = url.pathname;
    if (p === '/v1/challenge') {
      state.challenges++;
      res.writeHead(200, { 'content-type': 'application/json', 'x-request-id': 'req-browser-challenge' });
      res.end(JSON.stringify({ nonce: 'b'.repeat(32), action: 'MOVE_CLOSER',
        params: { settle_ms: 1500, target: 0.3 }, issued_ms: Date.now(), expires_ms: Date.now() + 30000 }));
      return;
    }
    if (p === '/v1/verify' || p === '/v1/enroll') {
      const chunks = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        state.uploads.push({ path: p, bytes: Buffer.concat(chunks).length });
        const r = state.reply;
        res.writeHead(r.status, { 'content-type': 'application/json', 'x-request-id': r.id });
        res.end(r.body);
      });
      return;
    }
    if (p.startsWith('/vendor/')) { res.writeHead(404); res.end(); return; }
    for (const [prefix, dir] of STATIC) {
      if (!p.startsWith(prefix)) continue;
      const rel = p.slice(prefix.length) || 'index.html';
      const file = path.join(dir, rel);
      if (!file.startsWith(dir) || !existsSync(file)) break;
      res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
      res.end(readFileSync(file));
      return;
    }
    res.writeHead(404); res.end();
  });
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
    }
  };
  const opened = new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  return {
    opened,
    send(method, params = {}, sessionId) {
      const id = ++seq;
      ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
    close: () => ws.close(),
  };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error('timed out waiting for ' + what);
    await sleep(100);
  }
}

const LIVE_ARG = process.argv.indexOf('--live');
const LIVE_ORIGIN = LIVE_ARG > 0 ? process.argv[LIVE_ARG + 1] : null;

const LIVE_CASES = [
  { name: 'real stack, real face guide, fake camera: no face means no recording, then cancel', live: true,
    guideCheck: true },
  { name: 'real stack, guide blocked, fake camera: enroll must not show success', op: 'enroll', live: true,
    blockVendor: true },
  { name: 'real stack, guide blocked, fake camera: verify must not show success', op: 'verify', live: true,
    blockVendor: true },
];

async function main() {
  const exe = BROWSERS.find((b) => existsSync(b));
  if (!exe) { console.log('SKIP  no installed Chromium-family browser found'); process.exit(2); }
  if (LIVE_ORIGIN) {
    const failures = await runGroup(exe, LIVE_ORIGIN, { uploads: [] }, LIVE_CASES, true);
    console.log(failures ? `${failures} FAILED` : `all ${LIVE_CASES.length} live cases passed`);
    process.exit(failures ? 1 : 0);
  }
  const state = { reply: null, challenges: 0, uploads: [] };
  const server = fixtureServer(state);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let failures = 0;
  try {
    failures += await runGroup(exe, origin, state, CASES.filter((c) => !c.deny), true);
    failures += await runGroup(exe, origin, state, CASES.filter((c) => c.deny), false);
  } finally {
    server.close();
  }
  console.log(failures ? `${failures} FAILED` : `all ${CASES.length} browser cases passed`);
  process.exit(failures ? 1 : 0);
}

async function runGroup(exe, origin, state, cases, autoAccept) {
  const profile = mkdtempSync(path.join(tmpdir(), 'facetech-e2e-'));
  const browser = spawn(exe, [
    '--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
    '--use-fake-device-for-media-stream', ...(autoAccept ? ['--use-fake-ui-for-media-stream'] : []),
    '--no-first-run', '--no-default-browser-check', '--disable-extensions', 'about:blank',
  ], { stdio: 'ignore' });
  let failures = 0;
  let client = null;
  try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    await waitFor(() => existsSync(portFile) && readFileSync(portFile, 'utf8').includes('\n'), 20000, 'the browser');
    const [port] = readFileSync(portFile, 'utf8').split('\n');
    const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
    console.log(`browser ${version.Browser} (${path.basename(exe)}), page served from ${origin}`);
    client = cdp(version.webSocketDebuggerUrl);
    await client.opened;

    for (const c of cases) {
      state.reply = c.reply; state.challenges = 0; state.uploads = [];
      const { browserContextId } = await client.send('Target.createBrowserContext');
      if (c.deny) {
        await client.send('Browser.setPermission',
          { permission: { name: 'camera' }, setting: 'denied', origin, browserContextId });
      }
      const { targetId } = await client.send('Target.createTarget', { url: 'about:blank', browserContextId });
      const { sessionId } = await client.send('Target.attachToTarget', { targetId, flatten: true });
      const evaluate = async (expression) => (await client.send('Runtime.evaluate',
        { expression, returnByValue: true, awaitPromise: true }, sessionId)).result.value;
      let got = { text: '', tone: '', log: '' };
      try {
        if (c.blockVendor) {
          await client.send('Network.enable', {}, sessionId);
          await client.send('Network.setBlockedURLs', { urls: ['*/vendor/*'] }, sessionId);
        }
        await client.send('Page.navigate', { url: origin + '/' }, sessionId);
        await waitFor(() => evaluate("document.documentElement.dataset.ready === '1'"), 10000, 'page ready');
        await evaluate(`document.getElementById('${c.op || 'verify'}').click()`);
        if (c.guideCheck) {
          await waitFor(() => evaluate("document.getElementById('cue').textContent === " +
            "'We cannot see a face. Look straight at the screen.'"), 30000, 'the no-face cue');
          await sleep(3000);
          got.stillPreview = await evaluate("document.getElementById('phase').textContent === '' && " +
            "document.getElementById('result').textContent === ''");
          await evaluate("document.getElementById('cancel').click()");
        }
        if (c.cancelAtPhase) {
          await waitFor(() => evaluate("/hold|move/.test(document.getElementById('phase').textContent)"), 15000, 'recording');
          await evaluate("document.getElementById('cancel').click()");
        }
        await waitFor(() => evaluate("document.getElementById('result').textContent !== ''"), 30000, 'a result');
        await sleep(300);
        got = { ...got, ...await evaluate(`({ text: document.getElementById('result').textContent,
          tone: document.getElementById('view').dataset.tone,
          cue: document.getElementById('cue').textContent,
          log: document.getElementById('log').textContent,
          buttons: !document.getElementById('verify').disabled && document.getElementById('cancel').disabled,
          preview: document.getElementById('preview').srcObject === null })`) };
      } catch (e) {
        got.error = e.message;
      }
      const reqId = (/requestId=(\S+)/.exec(got.log || '') || [])[1] || null;
      if (c.live) {
        const result = (/result \S+ (?:ok|failed) .*/.exec(got.log || '') || [''])[0];
        const ok = got.tone === 'alert' && got.buttons === true && got.preview === true &&
          (c.guideCheck ? got.stillPreview === true && got.text === 'Stopped.' && !reqId
            : !!reqId && reqId !== 'null');
        console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.name}: text "${got.text}"; tone ${got.tone}; ${result}` +
          (got.error ? ' ERROR ' + got.error : ''));
        if (!ok || got.error) failures++;
        await client.send('Target.closeTarget', { targetId });
        await client.send('Target.disposeBrowserContext', { browserContextId });
        continue;
      }
      const checks = [
        [got.text === c.text, `text "${got.text}"`],
        [got.tone === c.tone, `tone ${got.tone}`],
        [got.buttons === true, 'buttons reset'],
        [got.preview === true, 'video detached'],
        [c.noUpload ? state.uploads.length === 0 : state.uploads.length === 1, `uploads ${state.uploads.length}`],
        [c.noUpload || reqId === c.reply.id, `requestId ${reqId}`],
      ];
      const bad = checks.filter(([ok]) => !ok);
      console.log(`${bad.length ? 'FAIL' : 'PASS'}  ${c.name}: ${checks.map(([, d]) => d).join('; ')}` +
        (got.error ? ' ERROR ' + got.error : ''));
      if (bad.length || got.error) failures++;
      await client.send('Target.closeTarget', { targetId });
      await client.send('Target.disposeBrowserContext', { browserContextId });
    }
  } finally {
    try { client?.close(); } catch { }
    browser.kill();
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch { }
  }
  return failures;
}

main().catch((e) => { console.error(e); process.exit(1); });
