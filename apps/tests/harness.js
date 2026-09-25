"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const { pathToFileURL } = require("url");

const APPS = process.env.FACETECH_APPS_DIR || path.resolve(__dirname, "..");
const REPO = path.resolve(__dirname, "..", "..");
const CONSOLE_HTML = fs.readFileSync(path.join(APPS, "console", "index.html"), "utf8");

function classicScripts(html, dir) {
  return [...html.matchAll(/<script src="\.\/([a-z0-9-]+\.js)"><\/script>/g)]
    .map((m) => ({ name: m[1], code: fs.readFileSync(path.join(dir, m[1]), "utf8") }));
}
const CONSOLE_SCRIPTS = classicScripts(CONSOLE_HTML, path.join(APPS, "console"));

function consoleDeps() {
  const load = (rel) => import(pathToFileURL(path.join(REPO, rel)).href);
  return Promise.all([load("packages/face-sdk/src/internal.ts"), load("apps/shared/verdict.js")])
    .then(([sdk, verdict]) => ({ sdk, shared: { signalVerdict: verdict.signalVerdict } }));
}

function makeClock() {
  let now = 1000;
  let seq = 0;
  const timers = new Map();
  const drain = async () => {
    for (let i = 0; i < 25; i++) await new Promise((r) => setImmediate(r));
  };
  return {
    now: () => now,
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { t: now + (ms || 0), fn, every: 0 }); return id; },
    setInterval(fn, ms) { const id = ++seq; timers.set(id, { t: now + ms, fn, every: ms }); return id; },
    clear(id) { timers.delete(id); },
    pending: () => timers.size,
    drain,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        await drain();
        let next = null;
        for (const [id, t] of timers) if (t.t <= end && (!next || t.t < next[1].t)) next = [id, t];
        if (!next) break;
        const [id, t] = next;
        now = t.t;
        if (t.every) t.t += t.every; else timers.delete(id);
        try { t.fn(); } catch (e) {                                          throw e; }
      }
      now = end;
      await drain();
    },
  };
}

function makeDom(env) {
  const bySel = new Map();
  const listeners = {};
  const toasts = [];
  function el(tag, sel) {
    const e = {
      tagName: String(tag || "div").toUpperCase(), sel: sel || null,
      children: [], dataset: {}, attrs: {}, hidden: false, disabled: false, value: "",
      parentNode: null, width: 0, height: 0, videoWidth: 640, videoHeight: 480, srcObject: null,
      _text: "", _html: "",
      style: { setProperty() {} },
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      get textContent() { return this._text; }, set textContent(v) { this._text = String(v); },
      get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
      appendChild(c) {
        this.children.push(c); c.parentNode = this;
        if (this.sel === "#toast") toasts.push(c.textContent);
        return c;
      },
      insertBefore(c) { return this.appendChild(c); },
      remove() {}, focus() {}, click() {},
      getTotalLength() { return 100; },
      addEventListener() {}, removeEventListener() {},
      closest() { return null; },
      getBoundingClientRect() { return { width: env.box.width, height: env.box.height, left: 0, top: 0 }; },
      getContext() { return env.ctx; },
      toDataURL() { return "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="; },
      play() { return Promise.resolve(); },
    };
    return e;
  }
  const document = {
    hidden: false,
    documentElement: el("html"),
    body: el("body"),
    querySelector(sel) { if (!bySel.has(sel)) bySel.set(sel, el("div", sel)); return bySel.get(sel); },
    createElement(tag) { return el(tag); },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
  };
  return { document, listeners, toasts, el: (sel) => document.querySelector(sel) };
}

function makeCamera() {
  const tracks = [];
  let gate = null;
  const cam = {
    tracks,
    denied: false,
    hold() { let release; gate = new Promise((r) => (release = r)); return () => release(); },
    getUserMedia() {
      const make = () => {
        if (cam.denied) throw new Error("NotAllowedError");
        const track = { readyState: "live", stop() { this.readyState = "ended"; } };
        tracks.push(track);
        return { get active() { return track.readyState === "live"; }, getTracks: () => [track] };
      };
      return gate ? gate.then(make) : Promise.resolve().then(make);
    },
    allEnded: () => tracks.every((t) => t.readyState === "ended"),
  };
  return cam;
}

function jsonResponse(status, body, headers) {
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (k) => (headers || {})[k.toLowerCase()] || null },
    json: async () => body,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  };
}
function challengeBody(n) {
  return {
    nonce: String(n).padStart(32, "a").slice(0, 32), action: "MOVE_CLOSER",
    params: { settle_ms: 1500, target: 0.3 }, issued_ms: 1, expires_ms: 30001,
  };
}

function loadApp(which, opts) {
  opts = opts || {};
  const clock = makeClock();
  const env = {
    box: { width: 300, height: 400 },
    luma: opts.luma || (() => 128),
    ctx: null,
  };
  env.ctx = {
    drawImage() {},
    getImageData(x, y, w, h) {
      const data = new Uint8ClampedArray(w * h * 4);
      for (let yy = 0; yy < h; yy++) for (let xx = 0; xx < w; xx++) {
        const v = env.luma(xx, yy, w, h), i = (yy * w + xx) * 4;
        data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255;
      }
      return { data };
    },
  };
  const dom = makeDom(env);
  const camera = makeCamera();
  const calls = [];
  let challengeN = 0;
  const routes = Object.assign({
    "GET /health": () => jsonResponse(200, { status: "ok", engineVersion: "0.1.0" }),
    "GET /v1/info": () => jsonResponse(200, { engineVersion: "0.1.0", modelVersion: "m",
      thresholds: { match: null, liveness: null }, faceScanVersions: [1] }),
    "GET /v1/challenge": () => jsonResponse(200, challengeBody(++challengeN)),

    "GET /capture-status": () => jsonResponse(200, { enabled: false }),
  }, opts.routes || {});
  async function fetch(url, init) {
    init = init || {};
    const method = (init.method || "GET").toUpperCase();
    const p = String(url).replace(/^https?:\/\/[^/]+/, "");
    const call = { method, path: p, body: init.body ? JSON.parse(init.body) : null,
      headers: Object.assign({}, init.headers || {}), signal: init.signal || null,
      redirect: init.redirect, cache: init.cache, credentials: init.credentials };
    calls.push(call);
    if (String(url).startsWith("http://127.0.0.1:1")) throw new TypeError("fetch failed");
    const h = routes[method + " " + p] || routes[method + " *"];
    if (!h) return jsonResponse(404, { error: { code: "USER_NOT_FOUND", message: "no route " + p } });
    const signal = init.signal;
    if (!signal) return h(call);
    return new Promise((resolve, reject) => {
      const abort = () => reject(Object.assign(new Error("The operation was aborted"), { name: "AbortError" }));
      if (signal.aborted) { abort(); return; }
      signal.addEventListener("abort", abort, { once: true });
      Promise.resolve().then(() => h(call)).then(resolve, reject);
    });
  }
  const rafs = new Map();
  let rafSeq = 0;
  const windowListeners = {};
  const sandbox = {
    console, TextEncoder, TextDecoder, URLSearchParams, URL, Blob, atob, btoa, crypto, AbortController,
    setImmediate,
    document: dom.document,
    navigator: {
      userAgent: "node-test",
      mediaDevices: { getUserMedia: () => camera.getUserMedia() },
      vibrate() { return true; },
    },
    location: { search: opts.search || "" },
    localStorage: (() => {
      const m = new Map(Object.entries(opts.storage || {}));
      return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
    })(),
    performance: { now: () => clock.now() },
    setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
    clearTimeout: (id) => clock.clear(id),
    setInterval: (fn, ms) => clock.setInterval(fn, ms),
    clearInterval: (id) => clock.clear(id),
    requestAnimationFrame: (fn) => { const id = ++rafSeq; rafs.set(id, fn); return id; },
    cancelAnimationFrame: (id) => rafs.delete(id),
    fetch,
    facetechConsoleDeps: consoleDeps,
    innerWidth: 390, innerHeight: 844,
  };
  sandbox.window = sandbox;
  sandbox.window.matchMedia = () => ({ matches: false });
  sandbox.window.addEventListener = (t, fn) => { (windowListeners[t] = windowListeners[t] || []).push(fn); };
  sandbox.window.removeEventListener = (t, fn) => {
    windowListeners[t] = (windowListeners[t] || []).filter((f) => f !== fn);
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  if (which !== "console") throw new Error("the harness runs the console only (apps/demo is gone)");
  for (const s of CONSOLE_SCRIPTS) vm.runInContext(s.code, ctx, { filename: "console/" + s.name });
  const run = (code) => vm.runInContext(code, ctx);

  const app = {
    ctx, run, clock, dom, camera, calls, env, routes,
    get S() { return run("S"); },
    click(id, dataset) {
      const target = { id: id || "", dataset: dataset || {} };
      const ev = { target: { closest: () => target, id: "" } };
      return Promise.all((dom.listeners.click || []).map((fn) => fn(ev)));
    },
    key(k, tag) {
      const ev = { key: k, target: { tagName: tag || "BODY" } };
      (dom.listeners.keydown || []).forEach((fn) => fn(ev));
    },
    setHidden(hidden) {
      dom.document.hidden = hidden;
      (dom.listeners.visibilitychange || []).forEach((fn) => fn());
    },
    pagehide() { (windowListeners.pagehide || []).forEach((fn) => fn()); },
    raf() { const fns = [...rafs.values()]; rafs.clear(); fns.forEach((f) => f()); },
    posts: () => calls.filter((c) => c.method === "POST"),
    body: () => dom.el("#body").innerHTML,
    main: () => dom.el("#main").innerHTML,
    settle: () => clock.drain(),
  };
  return app;
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

module.exports = {
  CONSOLE_HTML, CONSOLE_SCRIPTS, classicScripts, loadApp, jsonResponse, deferred, challengeBody, makeClock,
};
