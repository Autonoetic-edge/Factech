"use strict";
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const BASE = process.argv[2] || process.env.FACETECH_GATEWAY || "http://127.0.0.1:8080";
const FIXTURES = path.resolve(__dirname, "..", "engine", "tests", "fixtures");
const ENCODING = path.resolve(__dirname, "..", "packages", "face-sdk", "src", "encoding");

let sdk = null;
async function loadEncoder() {
  const load = (name) => import(pathToFileURL(path.join(ENCODING, name)).href);
  const [scan, msgpack] = await Promise.all([load("scan.ts"), load("msgpack.ts")]);
  return { buildFaceScan: scan.buildFaceScan, mpEncode: msgpack.mpEncode,
    bytesToB64: msgpack.bytesToB64, b64ToBytes: msgpack.b64ToBytes };
}

function readJpeg(file) {
  return sdk.b64ToBytes(fs.readFileSync(file).toString("base64"));
}

function scanFromSequence(dir, challengeId, challenge) {
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".jpg")).sort();
  if (files.length !== 12) {
    throw new Error(dir + ": expected 12 frames, found " + files.length +
      " (run engine/tests/fixtures/make_fixtures.py)");
  }
  return packScan(files.map((f) => readJpeg(path.join(dir, f))), challengeId, challenge);
}

function scanForChallenge(challenge, challengeId) {
  const dir = "live_" + challenge.action.split("_")[1].toLowerCase();
  return scanFromSequence(path.join(FIXTURES, dir), challengeId, challenge);
}

function scanFromJpeg(file, challengeId, challenge) {
  const jpegBytes = readJpeg(file);
  return packScan(Array.from({ length: 12 }, () => jpegBytes), challengeId, challenge);
}

function packScan(frameBytes, challengeId, challenge) {
  const frames = [];
  const t0 = Date.now();
  frameBytes.forEach((bytes, i) => {
    frames.push({ bytes: bytes, ts: t0 + i * 500 });
  });
  const device = {
    user_agent: "facetech-e2e (node)",
    screen: { w: 1024, h: 768 },
    tz_offset: new Date().getTimezoneOffset(),
  };
  const scan = sdk.buildFaceScan(frames, device, challengeId, challenge);
  const bytes = sdk.mpEncode(scan);
  return { b64: sdk.bytesToB64(bytes), size: bytes.length };
}

async function challenge() {
  const res = await fetch(BASE + "/v1/challenge");
  const data = await res.json().catch(() => null);
  if (res.status !== 200 || !data || !data.nonce) {
    throw new Error("GET /v1/challenge failed: " + res.status + " " + JSON.stringify(data));
  }
  return data;
}

async function post(pathname, body) {
  const res = await fetch(BASE + pathname, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, data: await res.json().catch(() => null) };
}

let failures = 0;
function check(name, cond, detail) {
  console.log((cond ? "  PASS " : "  FAIL ") + name + (cond ? "" : "  <- " + detail));
  if (!cond) failures++;
}

(async () => {
  sdk = await loadEncoder();
  console.log("gateway: " + BASE);

  const health = await fetch(BASE + "/health").then((r) => r.json()).catch(() => null);
  check("gateway /health reachable", !!health && health.status === "ok", JSON.stringify(health));

  const userId = "e2e.synth." + Date.now();

  const ch0 = await challenge();
  check("GET /v1/challenge shape", !!ch0.nonce && ch0.nonce.length === 32 &&
    ["MOVE_CLOSER", "LOOK_LEFT", "LOOK_RIGHT"].includes(ch0.action) &&
    ch0.expires_ms > ch0.issued_ms, JSON.stringify(ch0));
  const good = scanForChallenge(ch0, "ch-e2e-enroll");

  console.log("enroll " + userId + " (" + ch0.action + " sequence, msgpack " + good.size + " B)");
  const en = await post("/v1/enroll", { user_id: userId, facescan: good.b64 });
  check("enroll 200", en.status === 200, JSON.stringify(en));
  check("enroll template_id", en.status === 200 && !!en.data.template_id, JSON.stringify(en.data));
  check("enroll quality.score > 0.5", en.status === 200 && en.data.quality.score > 0.5,
    JSON.stringify(en.data && en.data.quality));
  check("enroll liveness live=true", en.status === 200 && en.data.liveness.live === true,
    JSON.stringify(en.data && en.data.liveness));

  console.log("verify " + userId + " (same face, fresh challenge -> match)");
  const ch1 = await challenge();
  const good1 = scanForChallenge(ch1, "ch-e2e-verify");
  const ve = await post("/v1/verify", { user_id: userId, facescan: good1.b64 });
  check("verify 200 match=true", ve.status === 200 && ve.data.match === true, JSON.stringify(ve));
  check("verify score >= threshold", ve.status === 200 && ve.data.score >= ve.data.threshold,
    JSON.stringify(ve.data));

  console.log("replay: send that exact scan again (contract §2.5)");
  const rp = await post("/v1/verify", { user_id: userId, facescan: good1.b64 });
  check("replayed scan -> 422 CHALLENGE_FAIL (nonce spent)",
    rp.status === 422 && rp.data.error.code === "CHALLENGE_FAIL", JSON.stringify(rp));
  check("rejected replay discloses no score", rp.data && rp.data.score === undefined,
    JSON.stringify(rp.data));

  console.log("replay: a nonce the engine never issued");
  const stale = scanForChallenge({ action: ch1.action, nonce: "0".repeat(32) }, "ch-e2e-stale");
  const st = await post("/v1/verify", { user_id: userId, facescan: stale.b64 });
  check("unknown nonce -> 422 CHALLENGE_FAIL",
    st.status === 422 && st.data.error.code === "CHALLENGE_FAIL", JSON.stringify(st));

  console.log("challenge: a live nonce claiming the other action");
  const ch2 = await challenge();
  const other = ch2.action === "LOOK_LEFT" ? "LOOK_RIGHT" : "LOOK_LEFT";
  const swapped = scanForChallenge({ action: other, nonce: ch2.nonce }, "ch-e2e-swap");
  const sw = await post("/v1/verify", { user_id: userId, facescan: swapped.b64 });
  check("action mismatch -> 422 CHALLENGE_FAIL",
    sw.status === 422 && sw.data.error.code === "CHALLENGE_FAIL", JSON.stringify(sw));

  console.log("challenge: a live capture that ignored the instruction");
  const ch3 = await challenge();
  const still = scanFromSequence(path.join(FIXTURES, "live"), "ch-e2e-still", ch3);
  const stl = await post("/v1/verify", { user_id: userId, facescan: still.b64 });
  check("action not performed -> 422 LIVENESS_FAIL (the challenge signal)",
    stl.status === 422 && stl.data.error.code === "LIVENESS_FAIL" &&
    /challenge/.test(stl.data.error.message), JSON.stringify(stl));

  console.log("challenge: a scan with no nonce at all (the pre-S6 shape)");

  const noNonce = scanFromSequence(path.join(FIXTURES, "live_left"), "ch-e2e-bare");
  const nn = await post("/v1/verify", { user_id: userId, facescan: noNonce.b64 });
  check("no challenge map -> 422 CHALLENGE_FAIL (no tier-1 fall-back)",
    nn.status === 422 && nn.data.error.code === "CHALLENGE_FAIL", JSON.stringify(nn));

  console.log("verify as a user nobody enrolled");
  const ch5 = await challenge();
  const nf = await post("/v1/verify", {
    user_id: "zz.nobody", facescan: scanForChallenge(ch5, "ch-e2e-ghost").b64 });
  check("verify unknown -> 404 USER_NOT_FOUND",
    nf.status === 404 && nf.data.error.code === "USER_NOT_FOUND", JSON.stringify(nf));

  console.log("enroll blank.jpg (no face)");
  const ch6 = await challenge();
  const blank = scanFromJpeg(path.join(FIXTURES, "blank.jpg"), "ch-e2e-blank", ch6);
  const bl = await post("/v1/enroll", { user_id: "e2e.blank", facescan: blank.b64 });
  check("blank -> 422 NO_FACE",
    bl.status === 422 && bl.data.error.code === "NO_FACE", JSON.stringify(bl));

  const reuse = await post("/v1/enroll", { user_id: "e2e.blank", facescan: blank.b64 });
  check("a NO_FACE scan did not spend its challenge",
    reuse.status === 422 && reuse.data.error.code === "NO_FACE", JSON.stringify(reuse));

  console.log("liveness: the presenter spoof, one frame x12 (contract §2.4)");
  const ch7 = await challenge();
  const spoof = scanFromJpeg(path.join(FIXTURES, "face_synth.jpg"), "ch-e2e-spoof", ch7);
  const lv = await post("/v1/liveness", { facescan: spoof.b64 });
  check("/v1/liveness reports the replay (200, live=false)",
    lv.status === 200 && lv.data.live === false &&
    lv.data.signals.duplicates.largest_identical_group === 12, JSON.stringify(lv.data));
  const sp = await post("/v1/verify", { user_id: userId, facescan: spoof.b64 });
  check("replayed verify -> 422 LIVENESS_FAIL",
    sp.status === 422 && sp.data.error.code === "LIVENESS_FAIL", JSON.stringify(sp));

  console.log("liveness: a challenge-passing capture passes, and keeps its nonce");
  const ch8 = await challenge();
  const good2 = scanForChallenge(ch8, "ch-e2e-liveness");
  const lg = await post("/v1/liveness", { facescan: good2.b64 });
  check("/v1/liveness passes a capture (200, live=true)",
    lg.status === 200 && lg.data.live === true, JSON.stringify(lg.data));
  check("/v1/liveness reports the challenge signal",
    lg.status === 200 && lg.data.signals.challenge &&
    lg.data.signals.challenge.action === ch8.action, JSON.stringify(lg.data.signals));

  const after = await post("/v1/verify", { user_id: userId, facescan: good2.b64 });
  check("/v1/liveness left the nonce spendable",
    after.status === 200 && after.data.match === true, JSON.stringify(after));

  console.log("enroll two_face_like.jpg (two faces)");
  const ch9 = await challenge();
  const two = scanFromJpeg(path.join(FIXTURES, "two_face_like.jpg"), "ch-e2e-two", ch9);
  const tw = await post("/v1/enroll", { user_id: "e2e.two", facescan: two.b64 });
  check("two faces -> 422 MULTI_FACE",
    tw.status === 422 && tw.data.error.code === "MULTI_FACE", JSON.stringify(tw));

  console.log(failures === 0 ? "ALL E2E CHECKS PASSED" : failures + " E2E CHECK(S) FAILED");
  process.exit(failures === 0 ? 0 : 1);
})().catch((err) => {
  console.error("FATAL: " + (err && err.message ? err.message : err));
  process.exit(2);
});
