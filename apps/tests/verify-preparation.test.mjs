import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createPreparation, sampleOf, createTurnMeter, turnStep, yawOf, precheckReading } from '../verify/framing.js';
import { poseOfAction, TURN_SIGN } from '../verify/cues.js';
import { initial, step } from '../verify/flow.js';
import { glowOf, glowColour, reducedGlowColour } from '../verify/glow.js';
const sample = (yaw = 0, cx = .5) => ({ yaw, cx, cy: .46, h: .5 });
test('preparation waits, movement resets, then steadiness becomes ready', () => {
  const p = createPreparation();
  for (let t = 0; t < 800; t += 100) assert.equal(p.tick(sample(), t), false);
  assert.equal(p.tick(sample(.4), 800), false);
  for (let t = 900; t < 1700; t += 100) assert.equal(p.tick(sample(.05), t), false);
  assert.equal(p.tick(sample(.05), 1700), true);
});
test('early sustained movement or stale detector restarts; brief noise does not', () => {
  const p = createPreparation(); p.tick(sample(), 100); p.begin(100);
  p.tick(sample(.5), 200); assert.equal(p.disturbed(200), false);
  p.tick(sample(), 300); assert.equal(p.disturbed(300), false);
  p.tick(sample(.5), 400); assert.equal(p.disturbed(400), false);
  p.tick(sample(.5), 700); assert.equal(p.disturbed(700), true);
  p.tick(sample(), 800); assert.equal(p.disturbed(1801), true);
});
test('bad face, movement towards camera and moving sideways are not ready', () => {
  const p = createPreparation();
  assert.equal(p.tick(null, 0), false); p.tick(sample(), 100); p.begin(100);
  p.tick({ ...sample(), h: .7 }, 200); p.disturbed(200); assert.equal(p.disturbed(500), true);
  p.reset(); p.tick(sample(), 600); p.begin(600);
  p.tick(sample(0, .7), 700); p.disturbed(700); assert.equal(p.disturbed(1000), true);
  assert.equal(sampleOf({face:null}, [], {}), null);
});
test('Start only arms preparation; no capture without fresh readiness', () => {
  const camera = {...initial(), view:'camera', cameraLive:true, framing:'hold'};
  const armed = step(camera, {type:'PRIMARY'});
  assert.equal(armed.effect, 'prepare'); assert.equal(armed.state.view, 'camera');
  assert.equal(step(armed.state, {type:'PRIMARY'}).effect, null);
  assert.equal(step(armed.state, {type:'PREPARED'}).effect, 'startScan');
  assert.equal(step({...armed.state, guideOff:true}, {type:'PREPARED'}).effect, null);
  const stopped = step(armed.state, {type:'STOP'});
  assert.equal(stopped.state.preparing, false); assert.equal(stopped.effect, 'stopCamera');
  assert.equal(step(stopped.state, {type:'PREPARED'}).effect, null);
});
test('preparation retry stays on the stage and restarts the scan; hidden/stop still cancels', () => {
  const r = step({...initial(),view:'capture',cameraLive:true,busy:'scan'}, {type:'PREPARATION_RETRY'});
  assert.equal(r.effect,'restartScan');
  assert.deepEqual([r.state.view,r.state.preparing,r.state.notice,r.state.busy,r.state.cameraLive],['camera',true,'moved',null,true]);
  // the notice stays while the face is still well placed, and goes with a real problem or the fresh start
  assert.equal(step(r.state,{type:'GUIDE',line:'hold'}).state.notice,'moved');
  assert.equal(step(r.state,{type:'GUIDE',line:'closer'}).state.notice,null);
  const started = step(r.state,{type:'PREPARED'});
  assert.equal(started.effect,'startScan'); assert.equal(started.state.notice,null);
  const hidden = step(r.state, {type:'HIDDEN'});
  assert.equal(hidden.effect,'stopCamera'); assert.equal(hidden.state.preparing,false);
  assert.equal(step({...initial(),view:'processing'}, {type:'PREPARATION_RETRY'}).effect,null);
  // no guide (it died mid-watch): back to the fixed oval with Start, nothing automatic
  const off = step({...initial(),view:'capture',cameraLive:true,busy:'scan',guideOff:true}, {type:'PREPARATION_RETRY'});
  assert.equal(off.effect,null); assert.deepEqual([off.state.view,off.state.preparing,off.state.busy],['camera',false,null]);
});

// Exercise actual main.js wiring with a fake camera/SDK, without biometrics or HTTP.
async function harness({ glow = null, reducedMotion = false, storage = new Map() } = {}) {
  const rafs = new Map(); let rafSeq = 0;
  let now = 0, detectorOptions, sdkOptions, complete, runs = 0, posts = 0;
  const intervals = new Set();
  const element = () => ({children:[],addEventListener(){},setAttribute(){},focus(){},classList:{contains(){return false}},innerHTML:'',textContent:'',
    style:{props:{},setProperty(k,v){this.props[k]=v},removeProperty(k){delete this.props[k]}}});
  const elements = new Map();
  const doc = { hidden:false, activeElement:null, getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)}, querySelectorAll(){return []}, addEventListener(){} };
  const detector = {reset(){}, stop(){}, async start(){return true}};
  let opens = 0, reattaches = 0;
  const camera = {async open(){opens++},stop(){},handoff(){},reattach(){reattaches++},live:true};
  const bridge = {forget(){},sent:false,fetch(){posts++;},reply:null,glow};
  const context = vm.createContext({
    document:doc, navigator:{mediaDevices:{}}, window:{addEventListener(){},isSecureContext:true},
    performance:{now:()=>now}, setTimeout:()=>1,clearTimeout(){},
    requestAnimationFrame(fn){rafs.set(++rafSeq,fn);return rafSeq},cancelAnimationFrame(id){rafs.delete(id)},
    localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)},glowColour,reducedGlowColour,
    setInterval(fn){intervals.add(fn);return fn},clearInterval(fn){intervals.delete(fn)},
    createPreparation,sampleOf,initial,step,createTurnMeter,turnStep,yawOf,poseOfAction,TURN_SIGN,precheckReading,
    createFraming:()=>({}), framingStep:()=> 'good',
    createHeadGuide:()=>({setPaused(){},destroy(){},reducedMotion}), createCameraOwner:()=>camera,
    createBridge:()=>bridge, poseOf:t=>t, POSE_ORDER:[], icon:()=>'',render(){},renderMotion(){},
    classifyReply(){},decide:()=>({outcome:'cancelled'}),errorOutcome(){},
    loadTestGuide:async()=>({createFaceDetector(opts){detectorOptions=opts;return detector}}),
    createFaceSession:(opts)=>(sdkOptions=opts,{
      verify(){runs++;return new Promise(r=>complete=r)},
      cancel(){complete({ok:false,code:'CANCELLED'})},dispose(){},
    }),
  });
  let source = fs.readFileSync(new URL('../verify/main.js',import.meta.url),'utf8');
  source = source.replace(/^import .*;\r?\n/gm,'')
    .replace("const loadGuide = () => import('/shared/face-detector.js');", 'const loadGuide = loadTestGuide;')
    .replace('effects.load();', `globalThis.h = { dispatch, effects, state:()=>state, init() {state={...initial(),view:'camera',cameraLive:true,mode:'verify'};account={subjectId:'test'};bridge=createBridge();} };`);
  vm.runInContext(source,context);
  context.h.init(); await context.h.effects.startGuide();
  const tick = (t, yaw=0) => {
    now=t;
    detectorOptions.onTick({face:sample(),shape:{ok:true},cue:'good'},[{keypoints:[{x:.4,y:.4},{x:.6,y:.4},{x:.5+yaw*.05,y:.5}]}],{videoW:640,videoH:480});
    for(const fn of [...intervals])fn();
  };
  const flush = async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
  const frameAt = (t) => { now=t; for (const [id, fn] of [...rafs]) { rafs.delete(id); fn(); } };
  const glowNow = () => doc.getElementById('stage-ui').style.props['--glow'] ?? null;
  return {h:context.h,bridge,tick,flush,frameAt,glowNow,get rafs(){return rafs.size},event:e=>sdkOptions.onEvent(e),get runs(){return runs},get posts(){return posts},get opens(){return opens},get reattaches(){return reattaches}};
}
test('main wiring: early movement recovers automatically with zero submissions', async()=>{
  const h=await harness();h.h.dispatch({type:'PRIMARY'});
  for(let t=0;t<=700;t+=100)h.tick(t);
  assert.equal(h.runs,0); h.tick(800); assert.equal(h.runs,1);
  h.tick(900,.6);h.tick(1200,.6);await h.flush();
  assert.equal(h.posts,0);assert.equal(h.h.state().view,'camera');assert.equal(h.h.state().preparing,true);
  assert.equal(h.h.state().notice,'moved');
  assert.equal(h.opens,0);assert.equal(h.reattaches,1); // restarted in place: no camera reopen
  for(let t=1300;t<=2100;t+=100)h.tick(t);
  assert.equal(h.runs,2); assert.equal(h.posts,0);
  assert.equal(h.h.state().view,'capture');assert.equal(h.h.state().notice,null);
  h.h.dispatch({type:'STOP'});await h.flush();
  assert.equal(h.h.state().view,'result');assert.equal(h.h.state().outcome,'cancelled');
});
test('main wiring: requested left/right movements do not restart preparation', async()=>{
  const h=await harness();h.h.dispatch({type:'PRIMARY'});
  for(let t=0;t<=1200;t+=100)h.tick(t);
  h.event({type:'action',text:'left'});
  h.tick(1300,.8);h.tick(1700,.8);await h.flush();
  assert.equal(h.h.state().view,'capture');assert.equal(h.runs,1);
  h.h.dispatch({type:'STOP'});await h.flush();
});
test('main wiring: single turn keeps the hold watch until the SDK says move, then only meters the turn', async()=>{
  const h=await harness();h.h.dispatch({type:'PRIMARY'});
  for(let t=0;t<=800;t+=100)h.tick(t);
  assert.equal(h.runs,1);
  h.event({type:'instruction',instruction:{action:'LOOK_LEFT',settleMs:1500,target:.2}});
  h.event({type:'phase',phase:'hold'}); h.tick(850);h.tick(870); // hold samples give the meter its baseline
  assert.deepEqual(h.h.state().challenge,{pose:'left',settleMs:1500});
  h.event({type:'phase',phase:'move'});
  for(let t=900;t<=1600;t+=100)h.tick(t,.6); // a real turn: not "you moved"
  await h.flush();
  assert.equal(h.h.state().view,'capture');assert.equal(h.runs,1);assert.equal(h.posts,0);
  assert.equal(h.h.state().phase,'move');assert.equal(h.h.state().turn,'good');
  h.h.dispatch({type:'STOP'});await h.flush();
});
test('main wiring: HEAD_SEQUENCE instruction leaves the sequence screen in charge', async()=>{
  const h=await harness();h.h.dispatch({type:'PRIMARY'});
  for(let t=0;t<=800;t+=100)h.tick(t);
  h.event({type:'instruction',instruction:{action:'HEAD_SEQUENCE',settleMs:3200}});
  h.event({type:'phase',phase:'move'});
  assert.equal(h.h.state().challenge,null);assert.equal(h.h.state().phase,null);
  h.h.dispatch({type:'STOP'});await h.flush();
});

test('main wiring: the scan carries the last pre-check reading (trace only)', async()=>{
  const r = await harness();
  r.h.dispatch({type:'PRIMARY'}); r.tick(0); r.tick(800);
  assert.equal(r.runs,1);
  assert.deepEqual(Object.keys(r.bridge.precheck).sort(), ['backlight','capped','frame_luma','hint','luma','sharp']);
  r.h.effects.cancelScan(); await r.flush();
});

// Phase 4 glow: neutral from the instruction, the schedule's clock starts at the SDK's frame-0
// event (not a page timer), and no glow at all when the challenge has none.
const GLOW = glowOf({ glow: { neutral: [255, 255, 255], fade_ms: 400, end_ms: 2200, steps: [
  { rgb: [111, 168, 255], start_ms: 400 }, { rgb: [255, 192, 97], start_ms: 1000 }, { rgb: [111, 224, 160], start_ms: 1550 }] } });
async function glowRun(opts) {
  const h = await harness(opts); h.h.dispatch({type:'PRIMARY'});
  for (let t=0;t<=800;t+=100) h.tick(t);
  h.event({type:'instruction',instruction:{action:'LOOK_LEFT',settleMs:1500,target:.2}});
  h.event({type:'phase',phase:'hold'});
  return h;
}
test('main wiring: glow is neutral before frame 0 and follows the schedule from frame 0', async()=>{
  const h = await glowRun({ glow: GLOW });
  assert.equal(h.glowNow(), '255 255 255');
  assert.deepEqual(h.h.state().challenge, {pose:'left',settleMs:1500,glow:true,glowNote:true});
  h.frameAt(5000); assert.equal(h.glowNow(), '255 255 255', 'no colour before frame 0, however long the wait');
  h.event({type:'frame',index:1,total:12});
  h.frameAt(5399); assert.equal(h.glowNow(), '255 255 255');
  h.frameAt(5600); assert.equal(h.glowNow(), '183 212 255');
  h.event({type:'frame',index:2,total:12}); // later frames never move the clock
  h.frameAt(5800); assert.equal(h.glowNow(), '111 168 255');
  h.frameAt(6750); assert.equal(h.glowNow(), '183 208 129'); // JS rounds 128.5 up (engine: float)
  h.frameAt(7700); assert.equal(h.glowNow(), '255 255 255'); // end_ms + fade: neutral again
  h.h.dispatch({type:'STOP'}); await h.flush();
  assert.equal(h.glowNow(), null); assert.equal(h.rafs, 0);
});
test('main wiring: reduced motion makes one slow change; the line is shown once per browser', async()=>{
  const storage = new Map();
  const h = await glowRun({ glow: GLOW, reducedMotion: true, storage });
  h.frameAt(1000); h.event({type:'frame',index:1,total:12});
  h.frameAt(2300); assert.equal(h.glowNow(), '183 212 255');
  h.frameAt(3200); assert.equal(h.glowNow(), '111 168 255');
  h.frameAt(9000); assert.equal(h.glowNow(), '111 168 255');
  h.h.dispatch({type:'STOP'}); await h.flush();
  const again = await glowRun({ glow: GLOW, storage });
  assert.equal(again.h.state().challenge.glowNote, false);
  again.h.dispatch({type:'STOP'}); await again.flush();
});
test('main wiring: no glow in the challenge = no glow, no animation frame, today\'s state', async()=>{
  const h = await glowRun({});
  h.event({type:'frame',index:1,total:12}); h.frameAt(1500);
  assert.equal(h.glowNow(), null); assert.equal(h.rafs, 0);
  assert.deepEqual(h.h.state().challenge, {pose:'left',settleMs:1500});
  h.h.dispatch({type:'STOP'}); await h.flush();
});
