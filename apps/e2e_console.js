"use strict";
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const BASE = process.argv[2];
if (!BASE) { console.error("usage: node console_e2e.js https://host"); process.exit(2); }
const AUTH = "Basic " + Buffer.from((process.env.DEMO_U || "") + ":" + (process.env.DEMO_P || "")).toString("base64");
const FIXTURES = path.resolve(__dirname, "..", "engine", "tests", "fixtures");

let fails = 0;
const ok = (m) => console.log("  PASS " + m);
const bad = (m, d) => { fails++; console.log("  FAIL " + m + (d === undefined ? "" : " <- " + d)); };
const check = (c, m, d) => c ? ok(m) : bad(m, d);

const raw = globalThis.fetch;
globalThis.fetch = (u, o = {}) => {
  const h = new Headers(o.headers || {});
  h.set("Authorization", AUTH);
  return raw(u, { ...o, headers: h });
};

function grab(html, name) {
  const re = new RegExp(
    "/\\* ===FACETECH-" + name + "-START=== \\*/([\\s\\S]*?)/\\* ===FACETECH-" + name + "-END=== \\*/");
  const m = html.match(re);
  if (!m) throw new Error("block " + name + " not found");
  return m[1];
}

(async () => {
  console.log("console wiring, live against " + BASE + "\n");

  const homeRes = await fetch(BASE + "/", { cache: "no-store" });
  const homeHtml = await homeRes.text();
  check(homeRes.status === 200 && homeHtml.includes("page.js"), "GET / serves the guided page", homeRes.status);
  const conRes = await fetch(BASE + "/console/", { cache: "no-store" });
  await conRes.text();
  check(conRes.status === 200, "GET /console/ serves the console", conRes.status);

  let sdk = null, conJs = "";
  for (const u of ["/console/console.js", "/console/capture.js", "/console/deps.js",
    "/sdk/internal.js", "/shared/verdict.js", "/shared/boot-check.js"]) {
    const r = await fetch(BASE + u, { cache: "no-store" });
    const body = await r.text();
    check(r.status === 200 && /javascript/.test(r.headers.get("content-type") || ""),
      "GET " + u + " is served as JavaScript", r.status + " " + r.headers.get("content-type"));
    if (u === "/console/console.js") conJs = body;
    if (u === "/sdk/internal.js") check(body.includes("UNSTABLE, CONSOLE ONLY"), "/sdk/internal.js is the console-only bundle");
  }

  try {
    const enc = path.resolve(__dirname, "..", "packages", "face-sdk", "src", "encoding");
    const load = (name) => import(pathToFileURL(path.join(enc, name)).href);
    const [scan, msgpack] = await Promise.all([load("scan.ts"), load("msgpack.ts")]);
    sdk = { buildFaceScan: scan.buildFaceScan, mpEncode: msgpack.mpEncode,
      bytesToB64: msgpack.bytesToB64, b64ToBytes: msgpack.b64ToBytes };
    ok("SDK encoder loads for scan building");
  } catch (e) { bad("SDK encoder loads", e.message); process.exit(1); }

  const S = { connected: true, lastNonce: null, session: null };
  let api;
  try {
    api = new Function("S", grab(conJs, "CONSOLE-API") +
      "\nreturn {api:api,errCode:errCode,errMsg:errMsg,STAGE_OF:STAGE_OF,getHealth:getHealth," +
      "getInfo:getInfo,getChallenge:getChallenge,postScan:postScan,deleteTemplates:deleteTemplates," +
      "DEAD_BASE:DEAD_BASE};")(S);
    ok("console API block evaluates");
  } catch (e) { bad("console API block evaluates", e.message); process.exit(1); }

  const realFetch = globalThis.fetch;
  globalThis.fetch = (u, o) => realFetch(String(u).startsWith("http") ? u : BASE + u, o);

  const h = await api.getHealth();
  check(h.ok && S.connected && h.data && h.data.status === "ok",
    "console getHealth -> " + JSON.stringify(h.data), h.status);
  const inf = await api.getInfo();
  check(inf.ok && inf.data && inf.data.engineVersion,
    "console getInfo -> engineVersion " + (inf.data && inf.data.engineVersion), inf.status);

  const ch = await api.getChallenge();
  check(ch && ch.nonce && ch.action, "console getChallenge -> action " + (ch && ch.action));
  check(S.session && S.session.state === "unused" && S.session.nonce === ch.nonce,
    "console stores the live nonce as unused");

  function scanFor(action, id, challenge, dup) {

    const dir = path.join(FIXTURES, "live_" + action.split("_")[1].toLowerCase());
    const files = fs.readdirSync(dir).filter((f) => f.endsWith(".jpg")).sort();
    const t0 = Date.now();
    const frames = files.map((f, i) => ({
      bytes: sdk.b64ToBytes(fs.readFileSync(path.join(dir, dup ? files[0] : f)).toString("base64")),
      ts: t0 + i * 500,
    }));
    const scan = sdk.buildFaceScan(frames, { user_agent: "console-e2e (node)",
      screen: { w: 1024, h: 768 }, tz_offset: 0 }, id, challenge);
    return sdk.bytesToB64(sdk.mpEncode(scan));
  }

  const user = "console-e2e-" + Date.now();
  let r = await api.postScan("/v1/enroll", { user_id: user, facescan: scanFor(ch.action, "c1", ch) });
  check(r.ok && r.data && r.data.template_id,
    "console postScan /v1/enroll -> 200, template " + (r.data && String(r.data.template_id).slice(0, 12)), r.status);
  check(r.data && r.data.quality && typeof r.data.quality.score === "number",
    "enrol response carries the quality the Subjects screen shows");
  check(r.data && r.data.liveness && r.data.liveness.live === true,
    "enrol response carries the liveness summary the Result card shows");

  const ch2 = await api.getChallenge();
  r = await api.postScan("/v1/verify", { user_id: user, facescan: scanFor(ch2.action, "c2", ch2) });
  check(r.ok && r.data && r.data.match === true,
    "console postScan /v1/verify -> match " + (r.data && r.data.match) +
    ", score " + (r.data && r.data.score), r.status);
  check(r.data && typeof r.data.threshold === "number",
    "verify response carries the threshold the Result card renders");

  r = await api.postScan("/v1/verify", { user_id: user, facescan: scanFor(ch2.action, "c3", ch2) });
  check(!r.ok && api.errCode(r) === "CHALLENGE_FAIL",
    "replayed nonce -> " + api.errCode(r) + " (the replay toggle is real)", r.status);
  check(api.STAGE_OF[api.errCode(r)] === "nonce", "console maps CHALLENGE_FAIL to the nonce stage");

  const ch3 = await api.getChallenge();
  r = await api.postScan("/v1/verify", { user_id: user, facescan: scanFor(ch3.action, "c4", ch3, true) });
  check(!r.ok && ["LIVENESS_FAIL", "CHALLENGE_FAIL"].includes(api.errCode(r)),
    "one frame repeated (the spoof toggle) -> " + api.errCode(r), r.status);
  check(["liveness", "nonce"].includes(api.STAGE_OF[api.errCode(r)]),
    "console maps that rejection to a real stage: " + api.STAGE_OF[api.errCode(r)]);

  const ch4 = await api.getChallenge();
  r = await api.postScan("/v1/verify", { user_id: "nobody-" + Date.now(),
    facescan: scanFor(ch4.action, "c5", ch4) });
  check(!r.ok && api.errCode(r) === "USER_NOT_FOUND",
    "verify against an unknown subject -> " + api.errCode(r), r.status);
  check(api.STAGE_OF[api.errCode(r)] === "match", "console maps USER_NOT_FOUND to the match stage");

  r = await api.deleteTemplates(user);
  check(r.ok && r.data && r.data.deleted >= 1,
    "console Revoke -> DELETE /v1/templates deleted " + (r.data && r.data.deleted), r.status);
  const ch5 = await api.getChallenge();
  r = await api.postScan("/v1/verify", { user_id: user, facescan: scanFor(ch5.action, "c6", ch5) });
  check(!r.ok && api.errCode(r) === "USER_NOT_FOUND",
    "the revoked subject is gone from the engine -> " + api.errCode(r), r.status);

  S.connected = false;
  r = await api.api("GET", "/health");
  check(!r.ok && api.errCode(r) === "UNREACHABLE",
    "backend toggle off -> " + api.errCode(r) + " (requests really go to " + api.DEAD_BASE + ")");
  S.connected = true;

  console.log(fails ? "\n" + fails + " CONSOLE CHECK(S) FAILED" : "\nALL CONSOLE CHECKS PASSED");
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error("FATAL", e); process.exit(1); });
