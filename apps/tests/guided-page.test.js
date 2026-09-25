"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

const { loadGuidedPage, jsonResponse, LIVE_PASS } = require("./page-harness.js");

const VERIFIED = "It is you. Verified.";
const STOPPED = "Stopped.";

test('head rotation prompts survive guide ticks and do not trigger move-closer cancellation', async () => {
  const p = start({routes:{'GET /v1/challenge':()=>jsonResponse(200,{
    nonce:'1'.repeat(32),action:'HEAD_SEQUENCE',params:{settle_ms:3200,switch_ms:9000,first_sign:1,target:.2},
    issued_ms:1000,expires_ms:31000})}});
  p.click('verify');
  await p.pump(13000);
  assert.match(p.text('instruction'), /RIGHT/);
  assert.doesNotMatch(p.log(), /moved early/);
  await p.pump(7000);
  assert.equal(p.posts().length,1);
  assertIdle(p);
});

test('attack enrollment requires a separate photo/video identity before opening the camera', async () => {
  for (const kind of ['screen_photo','screen_video']) {
    const p = start({captureOptions:{captureMeta:{case:kind}}});
    p.click('enroll');
    await p.pump(1000);
    assert.match(p.text('result'), /separate attack enrollment identity/);
    assert.equal(p.challenges(),0);
    assert.equal(p.posts().length,0);
  }
});

function start(opts) {
  const p = loadGuidedPage(opts);
  p.faces.now = [{}];
  return p;
}

async function finishRun(p) {
  await p.pump(12000);
}

function assertIdle(p) {
  assert.ok(!p.S.attempt, "no attempt is left running");
  assert.equal(p.el("enroll").disabled, false);
  assert.equal(p.el("verify").disabled, false);
  assert.equal(p.el("cancel").disabled, true);
  assert.equal(p.camera.liveStreams().length, 0, "every camera stream is stopped");
}

test("a verify with a face in the oval runs end to end and says Verified only for match plus enforced liveness", async () => {
  const p = start();
  p.click("verify");
  await finishRun(p);
  assert.equal(p.text("result"), VERIFIED);
  assert.equal(p.tone(), "ok");
  assert.equal(p.challenges(), 1);
  assert.equal(p.posts().length, 1);
  assert.match(p.log(), /requestId=req-verify-1/);
  assertIdle(p);
});

const FIXTURES = [
  ["passed", { match: true, score: 0.7, threshold: 0.5, liveness: LIVE_PASS }, VERIFIED, "ok"],
  ["enforced live false", { match: true, score: 0.7, threshold: 0.5,
    liveness: { live: false, score: 0.2, enforced: true, failed_signals: ["challenge"] } },
  "Your face matched, but the live-person check was not confirmed. You are not verified.", "alert"],
  ["not enforced", { match: true, score: 0.7, threshold: 0.5,
    liveness: { live: true, score: 0.9, enforced: false, failed_signals: [] } },
  "Your face matched, but the live-person check was not confirmed. You are not verified.", "alert"],
  ["unknown liveness", { match: true, score: 0.7, threshold: 0.5, liveness: { score: 0.9 } },
    "Your face matched, but the live-person check was not confirmed. You are not verified.", "alert"],
  ["missing liveness", { match: true, score: 0.7, threshold: 0.5 },
    "Your face matched, but the live-person check was not confirmed. You are not verified.", "alert"],
  ["no match", { match: false, score: 0.1, threshold: 0.5, liveness: LIVE_PASS },
    "That face did not match.", "alert"],
];
for (const [name, body, sentence, tone] of FIXTURES) {
  test(`verify result on the page: ${name}`, async () => {
    const p = start({ routes: { "POST /v1/verify": () => jsonResponse(200, body, { "x-request-id": "req-f" }) } });
    p.click("verify");
    await finishRun(p);
    assert.equal(p.text("result"), sentence);
    assert.equal(p.tone(), tone);
    assertIdle(p);
  });
}

const ERRORS = [
  ["liveness fail", 422, { error: { code: "LIVENESS_FAIL", message: "liveness check failed: challenge" } },
    "Hold still at the start, then come closer until your face fills the oval."],
  ["challenge fail", 422, { error: { code: "CHALLENGE_FAIL", message: "reused_nonce" } },
    "That attempt timed out or was already used. Please try again."],
  ["server error", 500, { error: { code: "INTERNAL", message: "boom" } }, "Something went wrong. Please try again."],
  ["busy", 503, { error: { code: "BUSY", message: "queue full" } },
    "The service is busy. Wait a few seconds and try again."],
];
for (const [name, status, body, sentence] of ERRORS) {
  test(`verify error on the page: ${name}`, async () => {
    const p = start({ routes: { "POST /v1/verify": () => jsonResponse(status, body, { "x-request-id": "req-error-0001" }) } });
    p.click("verify");
    await finishRun(p);
    assert.equal(p.text("result"), sentence);
    assert.equal(p.tone(), "alert");
    assert.match(p.log(), /requestId=req-error-0001/);
    assertIdle(p);
  });
}

test("a slow permission prompt holds the attempt without fetching a challenge, then carries on", async () => {
  const p = start();
  p.camera.manual = true;
  p.click("verify");
  await p.pump(7000);
  assert.equal(p.S.stage, "preview");
  assert.equal(p.challenges(), 0);
  assert.equal(p.el("cancel").disabled, false);
  p.camera.manual = false;
  p.camera.requests[0].grant();
  await finishRun(p);
  assert.equal(p.text("result"), VERIFIED);
  assertIdle(p);
});

test("a refused camera ends with the permission message, no challenge and nothing running", async () => {
  const p = start();
  p.camera.deny = "NotAllowedError";
  p.click("verify");
  await finishRun(p);
  assert.equal(p.text("result"),
    "Camera access is blocked. Allow the camera in your browser settings and try again.");
  assert.equal(p.tone(), "alert");
  assert.equal(p.challenges(), 0, "no nonce is burned");
  assert.equal(p.posts().length, 0);
  assertIdle(p);
});

test("cancel while the permission prompt is open ends the attempt; a late stream is stopped (finding 7)", async () => {
  const p = start();
  p.camera.manual = true;
  p.click("verify");
  await p.pump(500);
  p.click("cancel");
  await p.settle();
  assert.equal(p.text("result"), STOPPED);
  assertIdle(p);

  const late = p.camera.requests[0].grant();
  await finishRun(p);
  assert.equal(late.live, false, "the stream that arrived after cancel was stopped");
  assert.equal(p.el("preview").srcObject, null);
  assert.equal(p.challenges(), 0, "the SDK never ran for the cancelled attempt");
  assert.equal(p.posts().length, 0);
  assert.equal(p.camera.requests.length, 1);
  assert.equal(p.text("result"), STOPPED, "no later result replaced the stop");
});

for (const order of ["new-first", "old-first"]) {
  test(`cancel and retry with the two camera prompts finishing in reverse order (${order})`, async () => {
    const p = start();
    p.camera.manual = true;
    p.click("verify");
    await p.pump(300);
    p.click("cancel");
    await p.settle();
    p.click("verify");
    await p.pump(300);
    assert.equal(p.camera.requests.length, 2);
    let older, newer;
    if (order === "new-first") {
      newer = p.camera.requests[1].grant(); await p.settle();
      older = p.camera.requests[0].grant(); await p.settle();
    } else {
      older = p.camera.requests[0].grant(); await p.settle();
      newer = p.camera.requests[1].grant(); await p.settle();
    }
    assert.equal(older.live, false, "the abandoned attempt's stream is stopped");
    assert.equal(p.el("preview").srcObject, newer, "the current attempt keeps its preview");
    p.camera.manual = false;
    await finishRun(p);
    assert.equal(p.text("result"), VERIFIED);
    assert.equal(p.challenges(), 1);
    assertIdle(p);
  });
}

test("cancel while the face guide is still loading leaves no guide loop running", async () => {
  const p = start();
  const release = p.holdVendor();
  p.click("verify");
  await p.pump(300);
  p.click("cancel");
  await p.settle();
  release();
  await p.pump(2000);
  assert.equal(p.challenges(), 0);
  assert.ok(!p.S.attempt);
  const ticks = p.S.lastTickAt;
  await p.pump(500);
  assert.equal(p.S.lastTickAt, ticks, "the detector does not keep ticking for a dead attempt");
  assertIdle(p);
});

test("cancel while waiting for a good face stops the preview", async () => {
  const p = start();
  p.faces.now = [];
  p.click("verify");
  await p.pump(2000);
  assert.equal(p.text("cue"), "Positioning only: We cannot see a face. Look straight at the screen.");
  p.click("cancel");
  await p.settle();
  assert.equal(p.text("result"), STOPPED);
  assert.equal(p.challenges(), 0);
  assertIdle(p);
});

test("cancel during recording stops the scan before upload", async () => {
  const p = start();
  p.click("verify");
  await p.pump(3000);
  assert.equal(p.S.stage, "record");
  p.click("cancel");
  await finishRun(p);
  assert.equal(p.posts().length, 0);
  assert.equal(p.text("result"), STOPPED);
  assertIdle(p);
});

test("a result that lands after cancel is ignored", async () => {
  let answer;
  const p = start({ routes: { "POST /v1/verify": () => new Promise((r) => { answer = r; }) } });
  p.click("verify");
  for (let i = 0; i < 200 && !answer; i++) await p.pump(100);
  assert.ok(answer, "the upload started");
  p.click("cancel");
  await p.settle();
  answer(jsonResponse(200, { match: true, score: 0.7, threshold: 0.5, liveness: LIVE_PASS }));
  await finishRun(p);
  assert.equal(p.text("result"), STOPPED);
  assert.notEqual(p.tone(), "ok");
  assertIdle(p);
});

test("the tab going to the background during preview stops the attempt; coming back allows a retry", async () => {
  const p = start();
  p.faces.now = [];
  p.click("verify");
  await p.pump(1000);
  p.setHidden(true);
  await p.settle();
  assert.equal(p.text("result"),
    "Stopped because the page went to the background. Press enroll or verify to try again.");
  assertIdle(p);
  p.setHidden(false);
  p.faces.now = [{}];
  p.click("verify");
  await finishRun(p);
  assert.equal(p.text("result"), VERIFIED);
  assertIdle(p);
});

test("the tab going to the background during recording stops the scan with no upload", async () => {
  const p = start();
  p.click("verify");
  await p.pump(3000);
  assert.equal(p.S.stage, "record");
  p.setHidden(true);
  await finishRun(p);
  assert.equal(p.posts().length, 0);
  assert.match(p.text("result"), /background/);
  assertIdle(p);
});

test("the camera ending mid-recording is reported as an interruption", async () => {
  const p = start();
  p.click("verify");
  await p.pump(3000);
  assert.equal(p.S.stage, "record");
  const sdkStream = p.camera.liveStreams()[0];
  sdkStream.track.end();
  await finishRun(p);
  assert.equal(p.text("result"), "The camera stopped. Please try again.");
  assert.equal(p.posts().length, 0);
  assertIdle(p);
});

test("leaving the page during preview releases the camera", async () => {
  const p = start();
  p.faces.now = [];
  p.click("verify");
  await p.pump(1000);
  assert.equal(p.camera.liveStreams().length, 1);
  p.pagehide();
  await p.settle();
  assert.equal(p.camera.liveStreams().length, 0);
  assert.ok(!p.S.attempt);
});

test("a bystander outside the visible crop blocks Ready, because the engine sees the whole frame (finding 9)", async () => {
  const p = start();
  const bystander = { cx: -0.2, cy: 0.45, h: 0.3 };
  p.faces.now = [{}, bystander];
  p.click("verify");
  await p.pump(3000);
  assert.equal(p.text("cue"),
    "Positioning only: Someone else is in the camera view, just outside the picture. Make sure only you are in view.");
  assert.equal(p.challenges(), 0, "the page never said Ready");
  p.faces.now = [{}];
  await finishRun(p);
  assert.equal(p.text("result"), VERIFIED);
});

test("the oval does not move between the preflight and the hold, so the baseline is where the user lined up (finding 6)", async () => {
  const p = start();
  const scales = [];
  p.click("verify");
  for (let i = 0; i < 120; i++) {
    await p.pump(100);
    const t = p.el("oval").style.transform;
    if (scales[scales.length - 1] !== t) scales.push(t + "@" + (p.S.sdkPhase || p.S.stage));
  }
  const first = scales[0].split("@")[0];
  const before = scales.filter((s) => !s.endsWith("@move") && !s.endsWith("@landing") && !s.endsWith("@idle"));
  assert.ok(before.every((s) => s.startsWith(first)), "no oval change before the move phase: " + scales.join(", "));
  assert.match(p.log(), /oval=0\.869->/);
});

test("the page shows measured progress: it says Hold there once the SDK guide reaches the target (finding 6)", async () => {
  const p = start();
  p.faces.now = [{ h: 0.45 }];
  p.click("verify");
  let sawReached = false;
  for (let i = 0; i < 160; i++) {
    await p.pump(50);
    if (p.S.sdkPhase === "move") p.faces.now = [{ h: 0.45 * Math.exp(0.3 * 1.15) * 1.05 }];
    if (p.text("cue") === "Close enough. Hold there.") sawReached = true;
  }
  await finishRun(p);
  assert.ok(sawReached, "the cue followed the measured progress");
  assert.match(p.log(), /measured approach 1\.\d\d of the guide target/);
});

test('head sequence rejection gives head-turn guidance instead of move-closer instructions', async () => {
  const p = start({routes:{
    'GET /v1/challenge':()=>jsonResponse(200,{
      nonce:'1'.repeat(32),action:'HEAD_SEQUENCE',params:{settle_ms:3200,switch_ms:9000,first_sign:1,target:.2},
      issued_ms:1000,expires_ms:31000}),
    'POST /v1/verify':()=>jsonResponse(422,{error:{code:'LIVENESS_FAIL',message:'liveness check failed: challenge'}})
  }});
  p.click('verify');
  await p.pump(22000);
  assert.match(p.text('result'), /small head-turn/);
  assert.doesNotMatch(p.text('result'), /closer/);
  assertIdle(p);
});


test('a permanently far face leaves positioning and gets both rotation prompts', async () => {
  const p = start({routes:{'GET /v1/challenge':()=>jsonResponse(200,{
    nonce:'1'.repeat(32),action:'HEAD_SEQUENCE',params:{settle_ms:3200,switch_ms:9000,first_sign:1,target:.2},
    issued_ms:1000,expires_ms:31000})}});
  p.faces.now = [{h:0.2}];
  p.click('enroll');
  await p.pump(3000);
  assert.match(p.text('cue'),/Positioning only: Come a little closer/);
  assert.equal(p.challenges(),0);
  await p.pump(10000);
  assert.match(p.text('cue'),/LEFT/);
  await p.pump(7000);
  assert.match(p.text('cue'),/RIGHT/);
  await p.pump(7000);
  assert.equal(p.posts().length,1);
  assertIdle(p);
});

test('missing recording form cannot silently open camera without a consent choice', async () => {
  const p = start({formMissing:true});
  p.click('enroll');
  await p.pump(1000);
  assert.match(p.text('result'),/recording form did not load/);
  assert.equal(p.camera.requests.length,0);
  assert.equal(p.posts().length,0);
});


test('positioning timeout never starts recording with a detected bystander', async () => {
  const p = start();
  p.faces.now = [{}, {cx:-0.2,cy:0.45,h:0.3}];
  p.click('enroll');
  await p.pump(10000);
  assert.equal(p.challenges(),0);
  assert.equal(p.posts().length,0);
  assert.match(p.text('result'),/only the consenting test subject/);
  assertIdle(p);
});

const RECORDING_READY = {enabled:true,evaluation_only:true,retention_days:7,consent_version:'storage-consent-v1'};
const recordingResponse = value => jsonResponse(200,value,{'content-type':'application/json'});
function recordedPage(reply = () => recordingResponse(RECORDING_READY)) {
  return start({realForm:true,routes:{
    'GET /health':()=>jsonResponse(200,{buildId:'test-build'}),
    'GET /capture-status':reply,
    'GET /review/api/receipt/req-enroll-1':()=>jsonResponse(200,{stored:true,id:13,diagnostics:true}),
  }});
}
test('actual form plus page: checked consent reaches enrollment metadata and receipt then remains consented',async()=>{
  const p=recordedPage();await p.settle();
  p.el('captureConsent').checked=true;p.el('captureConsent').dispatchEvent(new Event('change'));
  p.click('enroll');await finishRun(p);
  assert.equal(p.posts().length,1);
  const meta=JSON.parse(p.posts()[0].headers['X-Capture-Meta']);
  assert.equal(meta.consent,'storage-consent-v1');assert.equal(meta.subject,'T01');
  assert.match(p.log(),/Recording saved: capture 13; diagnostics saved/);
  assert.equal(p.el('captureConsent').checked,true);
  p.click('verify');await finishRun(p);
  assert.equal(p.posts().length,2);assertIdle(p);
});
test('actual form plus page: failed recording check preserves selection and retry recovers',async()=>{
  let enabled=false;const p=recordedPage(()=>recordingResponse(enabled?RECORDING_READY:{enabled:false}));
  await p.settle();p.el('captureConsent').checked=true;
  await p.click('enroll');assert.equal(p.camera.requests.length,0);assert.equal(p.el('captureConsent').checked,true);
  assert.match(p.text('result'),/did not confirm/);
  enabled=true;p.click('enroll');await finishRun(p);assert.equal(p.posts().length,1);assertIdle(p);
});
for(const action of ['cancel','hide','pagehide','withdraw']) test('pending preflight stops on '+action+' without opening camera',async()=>{
  let release;const p=recordedPage(()=>new Promise(r=>{release=r}));await p.settle();
  p.el('captureConsent').checked=true;p.click('enroll');p.click('enroll');await p.settle();
  if(action==='cancel') p.click('cancel');
  if(action==='hide') p.setHidden(true);
  if(action==='pagehide') p.pagehide();
  if(action==='withdraw') {p.el('captureConsent').checked=false;p.el('captureConsent').dispatchEvent(new Event('change'));}
  release(recordingResponse(RECORDING_READY));await p.settle();
  assert.equal(p.camera.requests.length,0);assert.equal(p.posts().length,0);assertIdle(p);
});
test('async preflight cannot start two attempts on repeated taps',async()=>{
  let release;const p=recordedPage(()=>new Promise(r=>{release=r}));await p.settle();
  p.el('captureConsent').checked=true;p.click('enroll');p.click('verify');await p.settle();
  release(recordingResponse(RECORDING_READY));await finishRun(p);
  assert.equal(p.posts().length,1);assert.equal(p.posts()[0].path,'/v1/enroll');assertIdle(p);
});
test('a form returning no recording metadata cannot open the camera',async()=>{
  const p=start({prepareCapture:async()=>null});await p.click('enroll');
  assert.match(p.text('result'),/Consented recording is required/);assert.equal(p.camera.requests.length,0);assertIdle(p);
});

for(const id of ['testSubject','userId']) test('changing '+id+' cancels active capture before submission',async()=>{
  const p=recordedPage();await p.settle();
  p.el('captureConsent').checked=true;p.el('captureConsent').dispatchEvent(new Event('change'));
  p.click('enroll');await p.settle();
  p.el(id).value='T02';p.el(id).dispatchEvent(new Event('input'));
  await p.settle();assert.equal(p.posts().length,0);assertIdle(p);
});
test('user cancellation keeps consent for next attempt',async()=>{
  const p=recordedPage();await p.settle();
  p.el('captureConsent').checked=true;p.el('captureConsent').dispatchEvent(new Event('change'));
  p.click('enroll');await p.settle();p.click('cancel');await p.settle();
  assert.equal(p.el('captureConsent').checked,true);
  p.click('enroll');await finishRun(p);assert.equal(p.posts().length,1);assertIdle(p);
});
