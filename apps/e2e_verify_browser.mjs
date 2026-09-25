// Staging check for the AmFatec page against the REAL local stack (tools/amfatec_stage.py):
// real Keycloak sign-in, real gateway + Postgres, real frozen engine, Chrome with its fake camera.
//
//   node apps/e2e_verify_browser.mjs [user] [--mobile] [--shots <dir>]
//
// The fake camera has no face in it, so the expected end of the journey is the SERVER's
// refusal, shown as a retry screen. A success needs a real face on a real device.
// The test user's password is read from the owned stack's local state and typed into
// Keycloak's form; it is never printed.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const ORIGIN = process.env.AMFATEC_E2E_ORIGIN ?? 'https://127.0.0.1:18444'; // a deployed site: set this and AMFATEC_E2E_PASSWORD
const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
];
const args = process.argv.slice(2);
const mobile = args.includes('--mobile');
const shots = args.includes('--shots') ? args[args.indexOf('--shots') + 1] : null;
const user = args.find((a) => !a.startsWith('--') && a !== shots) ?? 'bob';
const password = process.env.AMFATEC_E2E_PASSWORD ?? JSON.parse(readFileSync(path.join(ROOT, '.hardening-runtime/state/synthetic-secrets.json'), 'utf8')).users[user].password;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() > end) throw new Error('timed out waiting for ' + what);
    await sleep(150);
  }
}
function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let seq = 0;
  const pending = new Map(), events = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
    } else if (msg.method) events.push(msg);
  };
  return {
    opened: new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; }),
    events,
    send(method, params = {}, sessionId) {
      const id = ++seq;
      ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
    close: () => ws.close(),
  };
}

const checks = [];
const check = (name, ok, detail = '') => { checks.push(ok); console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (ok || !detail ? '' : '  -> ' + detail)); };

const exe = BROWSERS.find((b) => existsSync(b));
if (!exe) { console.log('SKIP  no Chromium-family browser found'); process.exit(2); }
const profile = mkdtempSync(path.join(tmpdir(), 'amfatec-e2e-'));
const browser = spawn(exe, [
  '--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
  '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
  '--ignore-certificate-errors', // the owned stack's self-signed local CA; staging only
  '--no-first-run', '--no-default-browser-check', '--disable-extensions', 'about:blank',
], { stdio: 'ignore' });

let client;
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  await waitFor(async () => existsSync(portFile) && readFileSync(portFile, 'utf8').includes('\n'), 20000, 'the browser');
  const [port] = readFileSync(portFile, 'utf8').split('\n');
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  console.log(`browser ${version.Browser}, ${mobile ? 'mobile 390x844' : 'desktop 1280x900'}, user ${user}`);
  client = cdp(version.webSocketDebuggerUrl);
  await client.opened;
  const { targetId } = await client.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await client.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (m, p) => client.send(m, p, sessionId);
  const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result.value;
  await send('Network.enable'); await send('Log.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', mobile
    ? { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }
    : { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  let n = 0;
  const shot = async (name) => {
    if (!shots) return;
    mkdirSync(shots, { recursive: true });
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(path.join(shots, `${mobile ? 'm' : 'd'}${++n}-${name}.png`), Buffer.from(data, 'base64'));
  };
  const heading = () => evaluate("document.querySelector('#screen h2')?.innerText.replace(/\\s+/g,' ') ?? ''");
  const button = () => evaluate("document.getElementById('primary')?.innerText.trim() ?? ''");
  const click = (id) => evaluate(`document.getElementById('${id}').click()`);
  const cameraOn = () => evaluate("document.getElementById('preview').srcObject !== null");
  const posts = () => client.events.filter((e) => e.method === 'Network.requestWillBeSent' && e.params.request.method === 'POST' && e.params.request.url.startsWith(ORIGIN + '/v2/'));

  // 1. signed out -> real sign-in
  await send('Page.navigate', { url: ORIGIN + '/' });
  await waitFor(async () => (await button()) === 'Sign in', 15000, 'the sign-in screen');
  check('signed-out visitor sees Sign in, and the camera is off', !(await cameraOn()));
  await shot('signin');
  await click('primary');
  await waitFor(() => evaluate("!!document.getElementById('username') && !!document.getElementById('password')"), 20000, 'the Keycloak form');
  await evaluate(`(() => { document.getElementById('username').value = ${JSON.stringify(user)};
    document.getElementById('password').value = ${JSON.stringify(password)};
    document.getElementById('kc-form-login').submit(); })()`);
  await waitFor(async () => (await evaluate('location.href')) === ORIGIN + '/' && ['Agree & continue', 'Enable camera'].includes(await button()), 30000, 'the page after sign-in');
  check('sign-in returns to the page (not the JSON session document)', true);

  // 2. consent when the server says this account is not enrolled
  if ((await button()) === 'Agree & continue') {
    check('first-time account is shown the agreement before anything else', true);
    await shot('intro');
    await click('primary');
    await waitFor(async () => (await button()) === 'I’m ready', 15000, 'consent saved by the server');
    const consent = posts().filter((e) => /\/consents\/template_authentication$/.test(e.params.request.url));
    check('consent was saved through the backend', consent.length === 1);
    check('a first-time account gets the demo by itself, with a Skip', await evaluate("!!document.getElementById('skip')"));
  } else {
    await click('demo'); // a returning account asks for it
    await waitFor(async () => (await button()) === 'I’m ready', 10000, 'the demo');
  }

  // 3. the demo: camera off, practice never advances or submits, and it can be skipped
  const before = posts().length;
  for (const i of [1, 2, 0, 1]) await evaluate(`document.getElementById('pose-track').children[${i}].click()`);
  check('practice buttons select a pose', await evaluate("document.getElementById('pose-track').children[1].getAttribute('aria-pressed') === 'true'"));
  check('the demo kept the camera off and submitted nothing', !(await cameraOn()) && posts().length === before);
  await shot('demo');
  await click('skip');
  await waitFor(async () => (await button()) === 'Enable camera', 10000, 'Prepare after the demo');
  check('camera still off before Enable camera', !(await cameraOn()));
  await shot('ready');

  // 4. camera only after the click; it takes over the whole screen
  await click('primary');
  await waitFor(async () => (await button()) === 'Start face check', 20000, 'the camera');
  await waitFor(() => evaluate("document.getElementById('preview').videoWidth > 0"), 10000, 'live video frames');
  check('live camera preview is showing real frames', await evaluate("!document.getElementById('preview').hidden"));
  check('the camera covers the whole screen', await evaluate("(() => { const r = document.getElementById('visual').getBoundingClientRect(); return r.left === 0 && r.top === 0 && r.width === innerWidth && r.height === innerHeight; })()"));
  await shot('camera');

  // 4b. the face guide. The fake camera has no face in it, so Start must stay asleep.
  await sleep(3000);
  check('no face in view: the guide keeps Start asleep and asks for a face',
    await evaluate("document.getElementById('primary').disabled && /place your face/i.test(document.getElementById('stage-cue').innerText)"));
  await click('primary');
  await sleep(500);
  check('a click on the sleeping Start submits nothing',
    (await button()) === 'Start face check' && !posts().some((e) => /\/(enroll|verify)$/.test(e.params.request.url)));
  // The rest of the journey needs Start and nothing here can show a face, so take the page's
  // own fallback: with the guide unreachable it must go back to the fixed oval.
  await click('stop');
  await send('Network.setBlockedURLs', { urls: ['*/shared/*'] });
  await send('Page.reload');
  await waitFor(async () => ['Agree & continue', 'Enable camera'].includes(await button()), 30000, 'the page after the reload');
  if ((await button()) === 'Agree & continue') { // not enrolled yet: the agreement and the demo come round again
    await click('primary');
    await waitFor(async () => (await button()) === 'I’m ready', 15000, 'the demo again');
    await click('skip');
    await waitFor(async () => (await button()) === 'Enable camera', 10000, 'Prepare again');
  }
  await click('primary');
  await waitFor(async () => (await button()) === 'Start face check', 20000, 'the camera again');
  await waitFor(() => evaluate("!document.getElementById('primary').disabled"), 10000, 'a working Start without the guide');
  check('guide unreachable: the fixed oval line and a working Start',
    /place your face/i.test(await evaluate("document.getElementById('stage-cue').innerText")));

  // 5. start: one bold SDK-driven line, the face and the close button. Nothing else.
  const cue = "document.getElementById('stage-cue').innerText.replace(/\\s+/g, ' ')";
  await evaluate("document.getElementById('primary').click(); document.getElementById('primary')?.click()"); // double click
  await waitFor(async () => /forward/i.test(await evaluate(cue)), 20000, 'the first SDK cue');
  check('during capture: no page text, no Start, no practice buttons; only the close button',
    await evaluate("document.getElementById('screen').children.length === 0 && !document.getElementById('primary') && !document.getElementById('stop').hidden && document.getElementById('pose-track').offsetParent === null"));
  await shot('capture');
  const seen = new Set();
  await waitFor(async () => {
    seen.add(await evaluate(cue));
    return evaluate("document.getElementById('stop').hidden || !document.getElementById('visual').classList.contains('stage')");
  }, 60000, 'the end of capture');
  check('all three SDK-driven movements were shown', [/forward/i, /left/i, /right/i].every((re) => [...seen].some((s) => re.test(s))), [...seen].join(' | '));

  // 6. the verdict is the server's
  await waitFor(async () => !!(await button()), 60000, 'a result');
  await sleep(300);
  const scans = posts().filter((e) => /\/(enroll|verify)$/.test(e.params.request.url));
  check('exactly one scan was submitted despite the double click', scans.length === 1, String(scans.length));
  check('the scan went to an account-bound /v2 route with an idempotency key',
    scans.length === 1 && /\/v2\/subjects\/[0-9a-f]{32}\/(enroll|verify)$/.test(scans[0].params.request.url)
    && !!scans[0].params.request.headers['Idempotency-Key'] && !scans[0].params.request.headers['X-User-Id']);
  const title = await heading();
  console.log('      result screen: "' + title + '"  action: "' + (await button()) + '"');
  check('no face in the fake camera => server refusal shown as a retry, never success', /clearer view|once more/.test(title) && (await button()) === 'Try again', title);
  check('camera released after the attempt', !(await cameraOn()) && await evaluate("document.getElementById('preview').hidden"));
  await shot('result');

  // 7. recovery goes back to Prepare with the camera off
  await click('primary');
  await waitFor(async () => (await button()) === 'Enable camera', 10000, 'Prepare again');
  check('Try again returns to Prepare with the camera off', !(await cameraOn()));

  // 8. workspace destination
  await send('Page.navigate', { url: ORIGIN + '/workspace' });
  await waitFor(() => evaluate("!document.getElementById('ws-action').hidden"), 15000, 'the workspace');
  check('workspace shows this account\'s server-side status', /set up/i.test(await evaluate("document.getElementById('ws-title').innerText")));
  await shot('workspace');

  const errors = client.events.filter((e) => (e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
    || e.method === 'Runtime.exceptionThrown').map((e) => e.params.entry?.text ?? e.params.exceptionDetails?.text);
  const unexpected = errors.filter((t) => !/status of 4\d\d/.test(t)); // 401 signed-out and 4xx refusals are expected
  check('no CSP violations or script errors in the console', unexpected.length === 0, unexpected.join(' || '));
} catch (e) {
  check('journey completed', false, e.message);
} finally {
  client?.close();
  browser.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* Windows keeps a lock briefly */ }
}
const failed = checks.filter((ok) => !ok).length;
console.log(failed ? `${failed} FAILED of ${checks.length}` : `all ${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
