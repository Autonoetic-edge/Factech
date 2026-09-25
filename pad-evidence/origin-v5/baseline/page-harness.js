"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const { makeClock, jsonResponse, challengeBody } = require("./harness.js");

const APPS = process.env.FACETECH_APPS_DIR || path.resolve(__dirname, "..");
const REPO = path.resolve(__dirname, "..", "..");

const SOURCES = {
  "/sdk/index.js": path.join(REPO, "packages", "face-sdk", "dist", "index.js"),
  "/shared/face-detector.js": path.join(APPS, "shared", "face-detector.js"),
  "/shared/face-guide.js": path.join(APPS, "shared", "face-guide.js"),
  "/shared/messages.js": path.join(APPS, "shared", "messages.js"),
  page: path.join(APPS, "integration-demo", "page.js"),
};

function toScript(code) {
  const aliases = [];
  code = code.replace(/^import\s*\{([^}]*)\}\s*from\s*'([^']+)';/gm,
    (m, names, from) => `const {${names.replace(/\s+as\s+/g, ": ")}} = __require(${JSON.stringify(from)});`);
  code = code.replace(/export\s*\{([^}]*)\};?/g, (m, names) => {
    for (const n of names.split(",").map((s) => s.trim()).filter(Boolean)) {
      const [local, exported] = n.split(/\s+as\s+/);
      aliases.push([exported || local, local]);
    }
    return "";
  });
  code = code.replace(/^export\s+/gm, "");
  code = code.replace(/\bimport\(/g, "__import(");
  const aliasMap = JSON.stringify(Object.fromEntries(aliases));
  return `(function () { "use strict";\n${code}\n;const __alias = ${aliasMap};` +
    `return (n) => eval(Object.hasOwn(__alias, n) ? __alias[n] : n); })()`;
}

const SCRIPTS = Object.fromEntries(Object.entries(SOURCES)
  .map(([k, file]) => [k, toScript(fs.readFileSync(file, "utf8"))]));

function makeElement(id, env) {
  const e = {
    id, tagName: id === "preview" ? "VIDEO" : "DIV",
    dataset: {}, style: {}, hidden: false, disabled: false, value: "",
    srcObject: null, videoWidth: 640, videoHeight: 480, readyState: 4,
    scrollTop: 0, scrollHeight: 0, onclick: null, width: 0, height: 0,
    checked: false, listeners: {},
    addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); },
    dispatchEvent(event) { for (const fn of this.listeners[event.type] || []) fn(event); },
    _text: "",
    get textContent() { return this._text; }, set textContent(v) { this._text = String(v); },
    play() { return Promise.resolve(); },
    select() {},
    getBoundingClientRect() { return { width: env.box.width, height: env.box.height, left: 0, top: 0 }; },
    getContext() { return env.ctx; },
    toDataURL() { return "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="; },
  };
  return e;
}

function makeCamera() {
  const requests = [];
  const streams = [];
  const cam = {
    requests, streams,
    manual: false,
    deny: null,
    error: (name) => Object.assign(new Error(name), { name }),
    getUserMedia() {
      if (cam.deny) return Promise.reject(cam.error(cam.deny));
      const req = {};
      const p = new Promise((resolve, reject) => { req.resolve = resolve; req.reject = reject; });
      req.grant = () => { const s = cam.stream(); req.resolve(s); return s; };
      req.refuse = (name = "NotAllowedError") => req.reject(cam.error(name));
      requests.push(req);
      if (!cam.manual) req.grant();
      return p;
    },
    stream() {
      const listeners = new Set();
      const track = {
        readyState: "live",
        stop() { track.readyState = "ended"; },
        addEventListener(_t, fn) { listeners.add(fn); },
        removeEventListener(_t, fn) { listeners.delete(fn); },
        end() { track.readyState = "ended"; for (const fn of [...listeners]) fn(); },
      };
      const s = { track, getTracks: () => [track], get live() { return track.readyState === "live"; } };
      streams.push(s);
      return s;
    },
    liveStreams: () => streams.filter((s) => s.live),
  };
  return cam;
}

function detection({ cx = 0.5, cy = 0.4625, h = 0.5, score = 0.95 } = {}, frame) {
  const s = Math.max(frame.viewW / frame.videoW, frame.viewH / frame.videoH);
  const ox = (frame.viewW - frame.videoW * s) / 2, oy = (frame.viewH - frame.videoH * s) / 2;
  const H = (h * frame.viewH) / s, W = H * 0.9;
  const vx = (cx * frame.viewW - ox) / s, vy = (cy * frame.viewH - oy) / s;
  const x0 = vx - W / 2, y0 = vy - H / 2, d = 0.4 * W;
  const kp = [
    { x: vx - d / 2, y: y0 + 0.38 * H }, { x: vx + d / 2, y: y0 + 0.38 * H },
    { x: vx, y: y0 + 0.58 * H }, { x: vx, y: y0 + 0.78 * H },
  ].map((p) => ({ x: p.x / frame.videoW, y: p.y / frame.videoH }));
  return {
    categories: [{ score }], keypoints: kp,
    boundingBox: { originX: x0, originY: y0, width: W, height: H },
  };
}

const LIVE_PASS = { live: true, score: 0.9, enforced: true, failed_signals: [] };

function loadGuidedPage(opts) {
  opts = opts || {};
  const clock = makeClock();
  const env = { box: { width: 300, height: 400 }, ctx: null };
  env.ctx = {
    drawImage() {},
    getImageData(x, y, w, h) {
      const data = new Uint8ClampedArray(w * h * 4).fill(128);
      return { data };
    },
  };
  const els = new Map();
  const el = (id) => { if (!els.has(id)) els.set(id, makeElement(id, env)); return els.get(id); };
  el("userId").value = "demo.user";
  const docListeners = {};
  const winListeners = {};
  const document = {
    hidden: false,
    documentElement: el("html"),
    getElementById: el,
    createElement: () => makeElement("", env),
    addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
    removeEventListener(t, fn) { docListeners[t] = (docListeners[t] || []).filter((f) => f !== fn); },
  };
  const camera = makeCamera();
  const calls = [];
  let challengeN = 0;
  const routes = Object.assign({
    "GET /v1/challenge": () => jsonResponse(200, challengeBody(++challengeN)),
    "POST /v1/enroll": () => jsonResponse(200, { template_id: "tpl-1", quality: { score: 0.8 }, liveness: LIVE_PASS },
      { "x-request-id": "req-enroll-1" }),
    "POST /v1/verify": () => jsonResponse(200, { match: true, score: 0.7, threshold: 0.5, liveness: LIVE_PASS },
      { "x-request-id": "req-verify-1" }),
  }, opts.routes || {});
  async function fetch(url, init) {
    init = init || {};
    const method = (init.method || "GET").toUpperCase();
    const p = String(url).replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    calls.push({ method, path: p, headers: init.headers });
    const h = routes[method + " " + p];
    if (!h) return jsonResponse(404, { error: { code: "USER_NOT_FOUND", message: "no route " + p } });
    return h();
  }
  const faces = { now: [], fail: false };
  const detector = {
    detectForVideo() {
      if (faces.fail) throw new Error("detector broke");
      const box = env.box;
      const frame = { videoW: 640, videoH: 480, viewW: box.width, viewH: box.height };
      return { detections: faces.now.map((f) => (f.boundingBox ? f : detection(f, frame))) };
    },
  };
  const vendor = {
    FilesetResolver: { forVisionTasks: async () => ({}) },
    FaceDetector: { createFromOptions: async () => detector },
  };
  let vendorGate = null;
  const rafs = new Map();
  let rafSeq = 0;
  const sandbox = {
    facetechCaptureOptions: opts.formMissing ? undefined : (opts.prepareCapture || (() => ({captureMeta:{consent:'storage-consent-v1', ...opts.captureOptions?.captureMeta}}))),
    console, Event, TextEncoder, TextDecoder, URLSearchParams, URL, atob, btoa, crypto,
    AbortController, AbortSignal, DOMException, queueMicrotask,
    document,
    navigator: { userAgent: "node-test", mediaDevices: { getUserMedia: () => camera.getUserMedia() } },
    location: { search: opts.search || "" },
    performance: { now: () => clock.now() },
    setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
    clearTimeout: (id) => clock.clear(id),
    requestAnimationFrame: (fn) => { const id = ++rafSeq; rafs.set(id, fn); return id; },
    cancelAnimationFrame: (id) => rafs.delete(id),
    fetch,
    innerWidth: 390, innerHeight: 844,
    addEventListener(t, fn) { (winListeners[t] = winListeners[t] || []).push(fn); },
    removeEventListener(t, fn) { winListeners[t] = (winListeners[t] || []).filter((f) => f !== fn); },
    __import: () => {
      if (opts.noDetector) return Promise.reject(new Error("not served"));
      return vendorGate ? vendorGate.then(() => vendor) : Promise.resolve(vendor);
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  const RealmError = vm.runInContext("Error", ctx);
  camera.error = (name) => Object.assign(new RealmError(name), { name });
  const modules = {};
  sandbox.__require = (requested) => {
    requested = requested.split('?')[0];
    const id = requested === "./face-guide.js" ? "/shared/face-guide.js" : requested;
    if (!modules[id]) {
      const get = vm.runInContext(SCRIPTS[id], ctx, { filename: id });
      modules[id] = new Proxy({}, { get: (_t, n) => (typeof n === "string" ? get(n) : undefined) });
    }
    return modules[id];
  };
  if (opts.realForm) {
    vm.runInContext(fs.readFileSync(path.join(APPS, 'integration-demo/evaluation-form.js'), 'utf8'), ctx);
    el('testSubject').value = 'T01';
    el('testCase').value = 'self';
    el('testLighting').value = 'daylight';
  }
  const pageScope = vm.runInContext(SCRIPTS.page, ctx, { filename: "integration-demo/page.js" });

  const page = {
    clock, camera, calls, faces, el, env,
    get S() { return pageScope("S"); },
    text: (id) => el(id).textContent,
    tone: () => el("view").dataset.tone,
    log: () => el("log").textContent,
    posts: () => calls.filter((c) => c.method === "POST"),
    challenges: () => calls.filter((c) => c.path === "/v1/challenge").length,
    holdVendor() { let release; vendorGate = new Promise((r) => (release = r)); return () => release(); },
    click(id) { const fn = el(id).onclick; return fn ? fn() : undefined; },
    raf() { const fns = [...rafs.values()]; rafs.clear(); fns.forEach((f) => f()); },
    async pump(ms, step = 50) {
      for (let t = 0; t < ms; t += step) { await clock.advance(step); page.raf(); }
      await clock.drain();
    },
    setHidden(hidden) {
      document.hidden = hidden;
      (docListeners.visibilitychange || []).forEach((fn) => fn());
    },
    pagehide() { (winListeners.pagehide || []).forEach((fn) => fn()); },
    settle: () => clock.drain(),
  };
  return page;
}

module.exports = { loadGuidedPage, detection, jsonResponse, LIVE_PASS };
