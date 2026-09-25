const SCREENS=[
  {id:'overview', label:'Overview',              grp:'CONSOLE'},
  {id:'enrol',    label:'Enrol a subject',       grp:'CONSOLE'},
  {id:'verify',   label:'Verify',                grp:'CONSOLE'},
  {id:'subjects', label:'Subjects and templates',grp:'CONSOLE'},
  {id:'bundle',   label:'Capture bundle',        grp:'SECURITY'},
  {id:'session',  label:'Session and crypto',    grp:'SECURITY'},
  {id:'liveness', label:'Liveness analysis',     grp:'SECURITY'},
  {id:'results',  label:'Test results',          grp:'SECURITY'},
  {id:'log',      label:'Activity log',          grp:'SECURITY'}
];

const PIPE=[
  {k:'capture',  live:true,  n:'Capture the burst and pack a FaceScan v1',
   s:'12 JPEG frames, msgpack, under 350 KB (contract 1.1)'},
  {k:'transport',live:false, n:'ML-KEM-768 handshake and mutual authentication',
   s:'Gateway not deployed. The browser reaches the engine over TLS only.'},
  {k:'signature',live:false, n:'Verify ML-DSA-44 signature over the manifest',
   s:'Gateway not deployed. Nothing signs the capture bundle in this build.'},
  {k:'decap',    live:false, n:'Decapsulate and decrypt in volatile memory',
   s:'Gateway not deployed. The payload is not encapsulated in this build.'},
  {k:'nonce',    live:true,  n:'Check and consume the challenge nonce, atomic',
   s:'Single use, 30 second window, spent by the engine (contract 2.5)'},
  {k:'liveness', live:true,  n:'Liveness signals across the burst',
   s:'Duplicates, motion, landmarks, timing and challenge (contract 2.4)'},
  {k:'match',    live:true,  n:'Generate template and match, cosine distance',
   s:'1:1 against the enrolled reference template'}
];

const ROSTER_KEY='facetech.console.subjects.v1';
function loadRoster(){
  try{
    const v=JSON.parse(localStorage.getItem(ROSTER_KEY));
    if(!Array.isArray(v)) return [];

    const seen={}, out=[];
    v.forEach(r=>{
      if(!r||typeof r.id!=='string') return;
      const id=r.id.trim().toLowerCase();
      if(!id||seen[id]) return;
      seen[id]=1; out.push(Object.assign({},r,{id:id}));
    });
    return out;
  }
  catch(e){ return []; }
}
function saveRoster(){
  try{ localStorage.setItem(ROSTER_KEY,JSON.stringify(S.subjects)); }catch(e){}
}

const S={
  screen:'overview', connected:true,
  spoof:false, replayNonce:false, verifyAsOther:false, cameraOff:false,
  mode:'verify',
  capture:{state:'idle',frame:0,total:12,guide:null},
  starting:false,
  run:{state:'idle',step:-1,results:{},failAt:null,ms:{}},
  result:null,
  subjects:loadRoster(),
  target:null,
  pendingId:null,
  pendingName:null,
  health:null,
  info:null,
  session:null,
  lastNonce:null,
  lastScan:null,
  lastLiveness:null,
  bundleTab:'manifest',
  logFilter:'all',
  log:[]
};
S.target=(S.subjects.filter(function(s){return !s.revoked;})[0]||{}).id||null;

const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sub=id=>S.subjects.find(x=>x.id===id);
function now(){const d=new Date();return [d.getHours(),d.getMinutes(),d.getSeconds()]
  .map(n=>String(n).padStart(2,'0')).join(':');}
function hhmmss(ms){const d=new Date(ms);return [d.getHours(),d.getMinutes(),d.getSeconds()]
  .map(n=>String(n).padStart(2,'0')).join(':');}
function toast(m,w){const e=document.createElement('div');e.className='toast'+(w?' warn':'');
  e.textContent=m;$('#toast').appendChild(e);setTimeout(()=>e.remove(),3400);}
function addLog(e){S.log.unshift(Object.assign({t:now()},e));}
const secs=ms=>(ms/1000).toFixed(1)+' s';

const num=(v,d)=> typeof v==='number' && isFinite(v) ? v.toFixed(d===undefined?2:d) : '?';

const DEAD_BASE='http://127.0.0.1:1';

const NET={fetch:(u,i)=>fetch(u,i),setTimeout:(f,ms)=>setTimeout(f,ms),clearTimeout:id=>clearTimeout(id)};
const NO_RUN=new AbortController().signal;

async function api(method,path,body,signal,long){
  const base=S.connected?'':DEAD_BASE;
  const t0=Date.now();
  let sdk;
  try{ sdk=await loadSDK(); }
  catch(e){ return {ok:false,status:0,data:null,kind:'SDK',requestId:null,ms:Date.now()-t0}; }
  const r=await sdk.rawRequest(NET,base,method,path,long?sdk.SCAN_TIMEOUT_MS:sdk.CHALLENGE_TIMEOUT_MS,
    signal||NO_RUN,body);
  const ms=Date.now()-t0;
  if(!r.ok) return {ok:false,status:0,data:null,kind:r.kind,netfail:r.kind==='NETWORK',requestId:r.requestId,ms:ms};
  const v=r.value;
  return {ok:v.status>=200&&v.status<300,status:v.status,data:v.bodyIsJson?v.body:null,requestId:v.requestId,ms:ms};
}

function withId(msg,r){ return r&&r.requestId?msg+' (request '+r.requestId+')':msg; }

function errCode(r){
  if(r.kind==='SDK') return 'SDK_UNAVAILABLE';
  if(r.kind==='TIMEOUT') return 'TIMEOUT';
  if(r.kind==='CANCELLED') return 'CANCELLED';
  if(r.netfail) return 'UNREACHABLE';
  if(r.data&&r.data.error&&r.data.error.code) return r.data.error.code;
  if(r.data&&r.data.detail) return 'REQUEST_INVALID';
  return 'HTTP_'+r.status;
}
function errMsg(r){
  if(r.kind==='SDK') return 'the console SDK module did not load (/sdk/internal.js)';
  if(r.kind==='TIMEOUT') return 'no answer from the engine before the deadline';
  if(r.kind==='CANCELLED') return 'request cancelled';
  if(r.netfail) return 'engine unreachable from the browser';
  if(r.data&&r.data.error&&r.data.error.message) return r.data.error.message;
  if(r.data&&Array.isArray(r.data.detail)&&r.data.detail[0]) return r.data.detail[0].msg||'invalid request';
  return 'HTTP '+r.status;
}

const STAGE_OF={
  UNAUTHORIZED:'transport', ENGINE_UNREACHABLE:'transport',
  PAYLOAD_TOO_LARGE:'capture', MALFORMED_SCAN:'capture',
  NO_FACE:'capture', MULTI_FACE:'capture',
  CHALLENGE_FAIL:'nonce',
  LIVENESS_FAIL:'liveness', LOW_QUALITY:'liveness',
  USER_NOT_FOUND:'match',
  MODEL_UNAVAILABLE:'unknown', BUSY:'unknown'
};

const LOCAL_STAGE_OF={UNREACHABLE:'transport', TIMEOUT:'transport', CANCELLED:'transport',
  SDK_UNAVAILABLE:'transport', REQUEST_INVALID:'capture'};
function stageOf(code){ return STAGE_OF[code] || LOCAL_STAGE_OF[code] || 'unknown'; }

function pipelineOutcome(r){
  const out={capture:'passed', nonce:'not-reached', liveness:'not-reached', match:'not-reached'};
  if(!r.ok){
    const stage=stageOf(errCode(r));
    if(stage==='capture') out.capture='failed';
    else if(stage==='nonce') out.nonce='failed';
    else if(stage==='liveness'){ out.nonce='passed'; out.liveness='failed'; }
    else if(stage==='match') out.match='failed';
    return out;
  }
  const l=r.data&&r.data.liveness;
  const enforced=!!(l&&l.enforced===true);
  out.nonce = enforced ? 'passed' : 'advisory';
  out.liveness = !l ? 'not-reached' : enforced ? (l.live?'passed':'failed') : 'advisory';
  if(r.data&&typeof r.data.match==='boolean') out.match = r.data.match?'passed':'failed';
  else if(r.data&&r.data.template_id!==undefined) out.match='passed';
  return out;
}

async function getHealth(){ const r=await api('GET','/health'); S.health=r.ok?r.data:null; return r; }
async function getInfo(){ const r=await api('GET','/v1/info'); S.info=r.ok?r.data:null; return r; }

async function getChallenge(signal){
  const r=await api('GET','/v1/challenge',null,signal);
  if(!r.ok||!r.data||!r.data.nonce) return {challenge:null,reply:r};
  S.lastNonce=r.data;
  S.session={nonce:r.data.nonce,action:r.data.action,params:r.data.params,
             issued_ms:r.data.issued_ms,expires_ms:r.data.expires_ms,state:'unused'};
  return {challenge:r.data,reply:r};
}
async function postScan(path,body,signal){ return api('POST',path,body,signal,true); }
async function deleteTemplates(userId){ return api('DELETE','/v1/templates/'+encodeURIComponent(userId)); }

let SDK=null, SDK_LOADING=null;
function loadSDK(){
  if(SDK) return Promise.resolve(SDK);
  if(!SDK_LOADING) SDK_LOADING=(async()=>{
    if(typeof facetechConsoleDeps!=='function'&&document.readyState==='loading')
      await new Promise(r=>document.addEventListener('DOMContentLoaded',r,{once:true}));
    if(typeof facetechConsoleDeps!=='function')
      throw new Error('the console modules did not load (/sdk/internal.js, /shared/verdict.js)');
    const deps=await facetechConsoleDeps();
    SDK=createConsoleCapture(deps.sdk,deps.shared,S);
    return SDK;
  })().catch(e=>{ SDK_LOADING=null; throw e; });
  return SDK_LOADING;
}

let RUNS=null, UPLOADING=null;
function runSignal(run){
  const ac=new AbortController();
  RUNS.own(run,()=>ac.abort());
  return ac.signal;
}
function runs(sdk){
  if(RUNS) return RUNS;
  RUNS=sdk.createRunController({
    cleanup(run,reason){
      sdk.stopCamera();
      if(S.capture.state==='running') S.capture.state=reason==='finished'?'done':'idle';
      if(S.run.state==='running') S.run.state='idle';
      S.capture.guide=null;
    },
    onCancel(run,reason){
      toast('Capture cancelled ('+reason+'). Nothing from it will be shown.'+(UPLOADING===run.id
        ?' The scan was already sent: if the engine acted on it, cancelling does not undo that.':''),true);
    }
  });
  sdk.bindLifecycle(document,window,reason=>{ if(RUNS.current()){ RUNS.cancel(reason); render(); } });
  return RUNS;
}

function bundleSummary(enc,ch){
  return {version:enc.scan.version,size:enc.size,quality:enc.quality,longEdge:enc.longEdge,
    frameCount:enc.scan.frames.length,action:ch.action,nonce:ch.nonce,params:ch.params,
    device:enc.scan.device,challengeId:enc.scan.challenge.id,frames:null};
}

async function hashFrames(scan){
  const out=[];
  let off=0;
  for(let i=0;i<scan.frames.length;i++){
    const b=scan.frames[i].jpeg_bytes;
    let hex='unavailable';
    if(typeof crypto!=='undefined'&&crypto.subtle){
      try{
        const d=await crypto.subtle.digest('SHA-256',b);
        hex=Array.from(new Uint8Array(d)).map(x=>x.toString(16).padStart(2,'0')).join('');
      }catch(e){}
    }
    out.push({n:i,off:off,len:b.length,h:hex,ts:scan.frames[i].ts_ms});
    off+=b.length;
  }
  return out;
}

function resetRun(){
  S.capture={state:'idle',frame:0,total:(SDK&&SDK.FRAME_COUNT)||12,guide:null};
  S.run={state:'idle',step:-1,results:{},failAt:null,ms:{}};
  S.result=null;
}

function applyOutcome(o,ms){
  Object.assign(S.run.results,o);
  if(ms!==undefined) S.run.ms.match=ms;
}
function stageFail(k,ms){ S.run.results[k]='failed'; S.run.state='failed';
  S.run.step=PIPE.findIndex(p=>p.k===k); if(ms!==undefined) S.run.ms[k]=ms; }

function fail(run,stage,msg,extra){
  if(stage!=='unknown'&&stage!=='transport') stageFail(stage); else S.run.state='failed';
  S.result=Object.assign({ok:false,stage:stage,msg:msg},extra||{});
  addLog({op:run.op==='enroll'?'Enrol':run.op==='liveness'?'Liveness':'Verify',
    sub:run.userId||'unknown',ok:false,ms:secs(S.run.totalMs||0),d:msg});
  toast(msg,true);
  render();
}

function snapshotSubject(op,sdk){
  if(op==='enroll'){
    const el=$('#eId');
    const id=sdk.canonicalUserId(el?el.value:S.pendingId);
    const nm=$('#eName'); if(nm&&nm.value.trim()) S.pendingName=nm.value.trim();
    return {id:id, error:!id?'Enter a subject id before capturing.'
      :!sdk.validUserId(id)?'Subject id must be 1-64 characters of a-z, 0-9, dot, dash or underscore (case-insensitive).':null};
  }
  if(S.verifyAsOther){
    const other=otherTarget();
    return {id:other, error:other?null:'Verify against a different subject needs at least two enrolled subjects.'};
  }
  return {id:S.target, error:S.target?null:'No enrolled subject selected to verify against.'};
}

async function startCapture(){
  if(S.run.state==='running'||S.capture.state==='running'||S.starting) return;
  const op=S.mode==='enrol'?'enroll':'verify';
  const screenAtClick=S.screen;
  S.starting=true;
  let sdk;
  try{ sdk=await loadSDK(); }
  catch(e){ S.starting=false; toast('Capture SDK unavailable: '+e.message,true); return render(); }
  S.starting=false;
  if(S.screen!==screenAtClick) return;
  const subject=snapshotSubject(op,sdk);
  if(op==='enroll') S.pendingId=subject.id||null;
  resetRun();
  const run=runs(sdk).start(op,subject.id);
  if(subject.error){ runs(sdk).end(run); return fail(run,'capture',subject.error); }
  S.lastScan=null;
  S.run.state='running';
  const tRun=Date.now();
  S.capture.total=sdk.FRAME_COUNT;
  const signal=runSignal(run);
  await runs(sdk).guard(run,async()=>{
    try{ await captureAndSend(sdk,run,tRun,signal); }
    catch(e){ if(!(e&&e.cancelled)) throw e; }
    finally{ if(UPLOADING===run.id) UPLOADING=null; }
  });
  render();
}

async function captureAndSend(sdk,run,tRun,signal){
  const live=()=>sdk.assertLive(RUNS,run);
  try{ await sdk.openCamera(); }
  catch(e){
    live();
    S.cameraOff=true; S.run.totalMs=Date.now()-tRun;
    return fail(run,'capture','Camera unavailable. Grant camera access and run the capture again.');
  }

  RUNS.own(run,()=>{ if(!RUNS.current()) sdk.stopCamera(); });
  live();

  const tCh=Date.now();
  let ch;
  if(S.replayNonce&&S.lastNonce){
    ch=S.lastNonce;
    if(S.session) S.session.state='replayed';
  } else {
    const got=await getChallenge(signal);
    live();
    ch=got.challenge;
    if(!ch){ S.run.totalMs=Date.now()-tRun;
      return fail(run,'nonce',withId('Could not obtain a challenge nonce from the engine ('+errCode(got.reply)+').',
        got.reply),{requestId:got.reply.requestId}); }
  }
  S.run.ms.nonce=Date.now()-tCh;

  let frames;
  try{ frames=await recordWithGuide(sdk,run,ch); }
  catch(e){
    if(e&&e.cancelled) throw e;
    S.run.totalMs=Date.now()-tRun;
    return fail(run,'capture',e.message);
  }
  live();

  const payload=S.spoof?frames.map(f=>({canvas:frames[0].canvas,ts:f.ts})):frames;

  let enc;
  try{ enc=sdk.encodeScan(payload,ch); }
  catch(e){ S.run.totalMs=Date.now()-tRun; return fail(run,'capture','Could not pack the FaceScan: '+e.message); }
  finally{ sdk.releaseFrames(frames); }
  S.run.results.capture='passed'; S.run.ms.capture=Date.now()-tRun;
  const bundle=bundleSummary(enc,ch);
  S.lastScan=bundle;
  hashFrames(enc.scan).then(f=>{ if(S.lastScan===bundle) bundle.frames=f; if(S.screen==='bundle') render(); });
  render();

  const userId=run.userId;
  const b64=enc.b64;
  enc=null;
  UPLOADING=run.id;
  const r=await postScan(run.op==='enroll'?'/v1/enroll':'/v1/verify',{user_id:userId,facescan:b64},signal);
  live();
  UPLOADING=null;
  S.run.totalMs=Date.now()-tRun;
  if(S.session&&S.session.state==='unused') S.session.state='consumed';

  const outcome=pipelineOutcome(r);
  applyOutcome(outcome);
  if(!r.ok){
    const code=errCode(r), stage=stageOf(code);
    return fail(run,stage,withId(code+': '+errMsg(r),r),{code:code,liveness:null,requestId:r.requestId});
  }

  const d=r.data;
  if(run.op==='enroll'){
    applyOutcome({},r.ms);
    S.run.state='done';
    upsertSubject(userId,d);
    S.result={ok:true,mode:'enrol',subject:userId,template_id:d.template_id,
      quality:d.quality&&d.quality.score,frames_embedded:d.quality&&d.quality.frames_embedded,
      liveness:d.liveness,ms:secs(S.run.totalMs)};
    addLog({op:'Enrol',sub:userId,ok:true,ms:secs(S.run.totalMs),
      d:'Template '+String(d.template_id).slice(0,12)+' stored, quality '+
        (d.quality?d.quality.score.toFixed(2):'n/a')});
    toast('Subject enrolled. Template stored in the engine.');
  } else {
    const verified=d.match===true&&sdk.livenessVerdict(d.liveness)==='passed';
    if(d.match) applyOutcome({},r.ms); else stageFail('match',r.ms);
    S.run.state=d.match?'done':'failed';
    S.result={ok:!!d.match,verified:verified,mode:'verify',subject:userId,score:d.score,threshold:d.threshold,
      liveness:d.liveness,ms:secs(S.run.totalMs),
      msg:d.match?null:('No match. Score '+num(d.score)+' is below the threshold '+num(d.threshold)+'.')};
    addLog({op:'Verify',sub:userId,ok:verified,ms:secs(S.run.totalMs),
      d:!d.match?('No match, score '+num(d.score)+' below threshold '+num(d.threshold))
        :verified?('Verified, score '+num(d.score))
        :('Face match only, score '+num(d.score)+', liveness not confirmed')});
    toast(!d.match?('No match, score '+num(d.score)+'.')
      :verified?('Verified: match score '+num(d.score)+' against threshold '+num(d.threshold)+', liveness passed.')
      :('Face match, score '+num(d.score)+', but liveness was not confirmed. Not verified.'),!verified);
  }
  render();
}

async function recordWithGuide(sdk,run,ch){
  S.capture={state:'running',frame:0,total:sdk.FRAME_COUNT,guide:sdk.createGuide(ch)};
  render(); sdk.mountVideo();
  try{
    return await sdk.captureFrames(i=>{
      if(!RUNS.live(run)) return;
      S.capture.frame=i;
      if(S.capture.guide) sdk.guideStep(S.capture.guide,(i-1)*sdk.CAPTURE_MS,null);
      render(); sdk.mountVideo();
    },()=>!RUNS.live(run));
  }catch(e){
    sdk.assertLive(RUNS,run);
    throw new Error('Capture failed: '+e.message);
  }finally{
    if(S.capture.state==='running') S.capture.state='done';
  }
}

function otherTarget(){
  const live=S.subjects.filter(s=>!s.revoked);
  const other=live.find(s=>s.id!==S.target);
  return other?other.id:null;
}

function upsertSubject(id,d){
  const s=sub(id);
  const rec={id:id,name:S.pendingName||(s&&s.name)||id,
    enrolled:new Date().toISOString().slice(0,10),
    quality:(d.quality&&d.quality.score)||0,
    template:String(d.template_id||'').slice(0,12),revoked:false};
  if(s) Object.assign(s,rec); else S.subjects.push(rec);
  if(!S.target) S.target=id;
  S.pendingName=null;
  saveRoster();
}

async function runLivenessOnly(){
  if(S.run.state==='running'||S.starting) return;
  S.starting=true;
  let sdk;
  try{ sdk=await loadSDK(); }
  catch(e){ S.starting=false; toast('Capture SDK unavailable.',true); return render(); }
  S.starting=false;
  const run=runs(sdk).start('liveness',null);
  S.run.state='running'; render();
  const signal=runSignal(run);

  await runs(sdk).guard(run,async()=>{
    try{
      try{ await sdk.openCamera(); }
      catch(e){ sdk.assertLive(RUNS,run); S.cameraOff=true; toast('Camera unavailable.',true); return; }
      RUNS.own(run,()=>{ if(!RUNS.current()) sdk.stopCamera(); });
      sdk.assertLive(RUNS,run);
      const got=await getChallenge(signal);
      sdk.assertLive(RUNS,run);
      const ch=got.challenge;
      if(!ch){ toast(withId('Could not obtain a challenge nonce ('+errCode(got.reply)+').',got.reply),true); return; }
      let frames;
      try{ frames=await recordWithGuide(sdk,run,ch); }
      catch(e){ if(e&&e.cancelled) throw e; toast(e.message+'. Run it again.',true); return; }
      sdk.assertLive(RUNS,run);
      const payload=S.spoof?frames.map(f=>({canvas:frames[0].canvas,ts:f.ts})):frames;
      let b64;
      try{ b64=sdk.encodeScan(payload,ch).b64; }
      catch(e){ toast('Could not pack the FaceScan: '+e.message,true); return; }
      finally{ sdk.releaseFrames(frames); }
      const r=await postScan('/v1/liveness',{facescan:b64},signal);
      b64=null;
      sdk.assertLive(RUNS,run);
      if(!r.ok){ S.lastLiveness=null; toast(withId(errCode(r)+': '+errMsg(r),r),true); return; }
      S.lastLiveness=r.data;
      const sig=r.data.signals||{};
      const failed=Object.keys(sig).filter(k=>sdk.signalVerdict(sig[k])==='failed');
      const verdict=sdk.livenessVerdict(r.data);
      addLog({op:'Liveness',sub:'n/a',ok:verdict==='passed',ms:secs(r.ms),
        d:(r.data.live?('Live, score '+num(r.data.score)):('Not live, failed signals: '+(failed.join(', ')||'none')))
          +(verdict==='not-enforced'?' (liveness not enforced on this engine)':'')});
      toast(verdict==='passed'?'Liveness passed.'
        : verdict==='not-enforced'?'Liveness measured, but NOT enforced on this engine.'
        : 'Liveness failed.', verdict!=='passed');
    }catch(e){ if(!(e&&e.cancelled)) throw e; }
  });
  S.run.state='idle';
  render();
}

function renderPills(){
  let p=[];
  if(!S.connected||!S.health){
    p=['<span class="pill off">Backend unavailable</span>','<span class="pill warn">Disconnected</span>'];
  } else {
    p.push('<span class="pill">engine '+esc(S.health.engineVersion||'?')+'</span>');
    p.push('<span class="pill warn">No gateway</span>');
    p.push(S.cameraOff?'<span class="pill warn">Camera unavailable</span>':'<span class="pill solid">Ready</span>');
  }
  $('#topPills').innerHTML='<div style="display:flex;gap:10px">'+p.join('')+'</div>';
}
function renderNav(){
  let h='',g='';
  SCREENS.forEach(s=>{
    if(s.grp!==g){ if(g) h+='<div class="sep"></div>'; h+='<div class="grp">'+s.grp+'</div>'; g=s.grp; }
    h+=`<button data-go="${s.id}" class="${S.screen===s.id?'on':''}">${s.label}</button>`;
  });
  $('#nav').innerHTML=h;
}

const TOGGLES=[
  {k:'connected',    label:'Backend reachable',                  inv:true},
  {k:'cameraOff',    label:'Skip the camera'},
  {k:'spoof',        label:'Send one frame repeated (replay check only)'},
  {k:'replayNonce',  label:'Replay the consumed nonce'},
  {k:'verifyAsOther',label:'Verify against a different subject'}
];
function renderDemo(){

  const canOther=!!otherTarget();
  if(!canOther) S.verifyAsOther=false;
  $('#demoBody').innerHTML=TOGGLES.map(t=>{
    const on=t.inv?!S[t.k]:S[t.k];
    const off=t.k==='verifyAsOther'&&!canOther;
    return `<div class="tog">${t.label}${off?' <span style="font-size:11px;color:var(--ink3)">(needs 2 subjects)</span>':''}<div class="spacer"></div>
     <button class="sw ${on?'on':''}" data-tog="${t.k}" aria-pressed="${!!on}" ${off?'disabled':''}><i></i></button></div>`;
  }).join('')+`<div class="hint">These change the request that is actually sent. The engine returns the
    rejection, this console only reports it. There is no soft-fail path.</div>`;
}

function pill(ok,txt){ return `<span class="pill ${ok?'solid':'warn'}">${txt}</span>`; }
const NOT_DEPLOYED='<span class="pill warn">Not deployed</span>';

function scrOverview(){
  const ok=S.log.filter(e=>e.ok).length, bad=S.log.filter(e=>!e.ok).length;
  const durations=S.log.map(e=>parseFloat(e.ms)).filter(v=>!isNaN(v)).sort((a,b)=>a-b);
  const median=durations.length?durations[Math.floor(durations.length/2)].toFixed(1)+' s':'no runs yet';
  const th=S.info&&S.info.thresholds||{};
  return `
  <div class="h">Overview</div>
  <div class="sh">Live state of the verification service, read from the engine on this page load.</div>
  <div class="row" style="margin-bottom:16px">
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">ENGINE STATUS</div>
      <div class="val big" ${S.health?'':'style="color:var(--warn)"'}>${S.health?'Running':'Unreachable'}</div>
      <div class="val" style="color:var(--ink2);margin-top:6px;font-size:12px">
        ${S.health?('GET /health, version '+esc(S.health.engineVersion||'?')):'GET /health failed'}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">SUBJECTS ENROLLED HERE</div>
      <div class="val big">${S.subjects.filter(s=>!s.revoked).length}</div>
      <div class="val" style="color:var(--ink2);margin-top:6px;font-size:12px">
        In-memory in the engine, lost on restart</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">ATTEMPTS THIS SESSION</div>
      <div class="val big">${ok+bad}</div>
      <div class="val" style="color:var(--ink2);margin-top:6px;font-size:12px">${ok} accepted, ${bad} rejected</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">CAPTURE TO VERDICT</div>
      <div class="val big">${median}</div>
      <div class="val" style="color:var(--ink2);margin-top:6px;font-size:12px">Median of this session, measured in the browser</div></div></div>
  </div>
  <div class="row">
    <div class="card warn" style="flex:1.4">
      <div class="card-h">Cryptographic posture<div class="spacer"></div>${NOT_DEPLOYED}</div>
      <div class="card-b">
        <div class="note warn" style="margin-bottom:14px"><b>The post-quantum gateway is not deployed in this
          build.</b> The rows below are the designed architecture, not what is running. In this rig the browser
          reaches the engine over TLS through Caddy and a mock gateway. The mock gateway holds the shared
          secret, as the real gateway would, but it performs none of the cryptography below.</div>
        <table>
        <tr><th style="width:38%">LAYER</th><th style="width:34%">ALGORITHM</th><th>STATE IN THIS BUILD</th></tr>
        <tr><td class="k">Key encapsulation</td><td class="mono">ML-KEM-768, FIPS 203</td><td>Not deployed</td></tr>
        <tr><td class="k">Manifest signature</td><td class="mono">ML-DSA-44, FIPS 204</td><td>Not deployed</td></tr>
        <tr><td class="k">Payload encryption</td><td class="mono">AES-256-GCM</td><td>Not deployed</td></tr>
        <tr><td class="k">Transport</td><td class="mono">TLS 1.3</td><td>Active, terminated at the edge</td></tr>
        <tr><td class="k">Engine authentication</td><td class="mono">Shared secret, X-Engine-Key</td>
          <td>Active, injected by the edge</td></tr>
        <tr><td class="k">Template storage</td><td class="mono">Process memory</td><td>Active, not persisted</td></tr>
      </table></div>
    </div>
    <div class="card" style="flex:1">
      <div class="card-h">Engine build<div class="spacer"></div>
        <span class="mono" style="font-size:11px;color:var(--ink3)">GET /v1/info</span></div>
      <div class="card-b col" style="gap:13px">
        ${S.info?`
        <div><div class="lbl">ENGINE VERSION</div><div class="val mono">${esc(S.info.engineVersion||'?')}</div></div>
        <div style="height:1px;background:var(--line2)"></div>
        <div><div class="lbl">MODEL VERSION</div><div class="val mono">${esc(S.info.modelVersion||'?')}</div></div>
        <div style="height:1px;background:var(--line2)"></div>
        <div><div class="lbl">FACESCAN VERSIONS</div>
          <div class="val mono">${esc((Array.isArray(S.info.faceScanVersions)?S.info.faceScanVersions:[]).join(', ')||'?')}</div></div>
        <div style="height:1px;background:var(--line2)"></div>
        <div><div class="lbl">THRESHOLDS</div>
          <div class="val mono">match ${th.match===null||th.match===undefined?'unset':esc(th.match)}, liveness ${
            th.liveness===null||th.liveness===undefined?'unset':esc(th.liveness)}</div>
          <div style="font-size:11.5px;color:var(--ink3);margin-top:3px">
            Reported as unset by the engine until the eval harness fixes them.</div></div>`
        :`<div class="note warn">/v1/info did not answer. Nothing on this screen is cached, so it is left blank
          rather than shown stale.</div>`}
      </div>
    </div>
    <div class="card" style="flex:1">
      <div class="card-h">Recent activity</div>
      <div class="card-b">
        ${S.log.length?S.log.slice(0,5).map(e=>`<div class="step"><div class="dot ${e.ok?'done':'fail'}"></div>
          <div class="txt">${esc(e.op)} ${esc(e.sub)}${e.ok?'':', rejected'}<div class="t2">${e.t}</div></div></div>`).join('')
          :`<div style="padding:24px 0;color:var(--ink3);font-size:13px">Nothing run yet in this session.</div>`}
        <div style="margin-top:14px"><button class="btn sm ghost" data-go="log">Open activity log</button></div>
      </div>
    </div>
  </div>`;
}

function captureCard(){
  const C=S.capture, total=C.total||12;
  const action=S.session&&S.session.action;
  let inner, cap, badge='';
  if(S.cameraOff){
    inner=`<div class="oval bad">Camera skipped</div>`;
    cap='The camera toggle is off, so no capture can run. Turn it back on in the demo controls.';
  } else if(C.state==='idle'){
    inner=`<div class="oval">Position your face inside the outline</div>`;
    cap='A challenge is requested from the engine immediately before the burst, then 12 frames are captured. '
      +'Hold still while the prompt says Hold steady, then perform the action when it changes: the engine '
      +'checks both halves separately (contract 2.5).';
  } else if(C.state==='running'){

    const G=C.guide, holding=!G||G.phase==='hold';
    inner=`<div class="oval live">${esc(holding?'Hold steady':(action?actionText(action):'Keep going'))}</div>`;
    const pr=S.session&&S.session.params;
    cap=`Capturing frame ${C.frame} of ${total}. `
      +(holding?'Hold still until the prompt changes.'
        :'Keep moving until the capture ends. This console has no face detector, so it cannot tell you when you have moved far enough.')
      +(action?' Challenge action: '+esc(action)+'.':'')
      +(pr?` Hold ${esc(num(pr.settle_ms,0))} ms, then a rise of ${esc(num(pr.target,3))} log-scale.`:'');
    badge=`<div class="badge">${holding?'HOLD':'MOVE'}</div>`;
  } else {
    inner=`<div class="oval live">Capture complete</div>`;
    cap=S.lastScan?`${S.lastScan.frameCount} frames, ${(S.lastScan.size/1024).toFixed(0)} KB msgpack, JPEG quality ${
      esc(S.lastScan.quality)}.`:`${total} frames captured.`;
    badge=`<div class="badge">CAPTURED</div>`;
  }
  const lit=Math.round((C.frame/Math.max(1,total))*32);
  const strip=Array.from({length:32},(_,i)=>{
    const on=C.state!=='idle'&&i<lit;
    return `<i class="${on?(S.spoof&&C.state==='done'?'bad':'on'):''}"></i>`;
  }).join('');
  const busy=C.state==='running'||S.run.state==='running';
  return `
  <div class="card">
    <div class="card-h">Guided capture<div class="spacer"></div>
      <span class="mono" style="font-size:11px;color:var(--ink3)">${total} frames, live camera</span></div>
    <div class="card-b">
      <div class="view">${badge}${inner}<div class="cap">${cap}</div></div>
      <div style="margin-top:14px"><div class="lbl">FRAMES BUNDLED</div><div class="strip">${strip}</div></div>
      <div class="row" style="margin-top:16px;align-items:center;gap:12px">
        <button class="btn pri" id="doCapture" ${busy?'disabled':''}>
          ${C.state==='running'?'Capturing':S.mode==='enrol'?'Capture and enrol':'Capture and verify'}</button>
        ${C.state!=='idle'&&!busy?'<button class="btn ghost" id="doReset">Reset</button>':''}
        <div class="spacer" style="flex:1"></div>
        <span class="mono" style="font-size:11px;color:var(--ink3)">${
          S.session?('Nonce '+esc(S.session.nonce.slice(0,8))):'No nonce yet'}</span>
      </div>
    </div>
  </div>`;
}
function actionText(a){
  return {MOVE_CLOSER:'Slowly move closer to the camera',
    LOOK_LEFT:'Turn your head to the left',LOOK_RIGHT:'Turn your head to the right'}[a]||a;
}

function pipelineCard(){
  const R=S.run;
  const rows=PIPE.map(p=>{
    if(!p.live){
      return `<div class="step"><div class="dot"></div>
        <div class="txt" style="opacity:.55">${p.n}
          <div class="t2">${p.s}</div></div>
        <div class="ms" style="font-size:10.5px">NOT DEPLOYED</div></div>`;
    }

    const st=R.results[p.k];
    const settled=R.state==='failed'||R.state==='done';
    const cls = st==='passed'?'done' : st==='failed'?'fail' : (settled?'skip':'');
    const notReached = settled && (!st || st==='not-reached');
    const note = notReached ? 'Not reached, or the engine did not say. Nothing is claimed for this stage.'
      : st==='advisory' ? 'Measured but NOT enforced on this engine (LIVENESS_ENFORCE is off).' : p.s;
    const tag = st==='passed'?'PASSED' : st==='failed'?'FAILED' : st==='advisory'?'ADVISORY'
      : notReached?'NOT REACHED':'';
    return `<div class="step"><div class="dot ${cls}"></div>
      <div class="txt" ${notReached||st==='advisory'?'style="opacity:.55"':''}>${p.n}
        <div class="t2">${note}</div></div>
      <div class="ms">${R.ms[p.k]!==undefined?R.ms[p.k]+' ms':''}${tag?' '+tag:''}</div></div>`;
  }).join('');
  const liveCount=PIPE.filter(p=>p.live).length;
  return `
  <div class="card ${R.state==='failed'?'warn':''}">
    <div class="card-h">Verification pipeline<div class="spacer"></div>
      <span class="pill ${R.state==='failed'?'warn':''}">${
        R.state==='idle'?'Idle':R.state==='running'?'Running':R.state==='failed'?'Rejected':'Accepted'}</span></div>
    <div class="card-b">
      ${rows}
      ${R.state==='failed'
        ? `<div class="note warn" style="margin-top:16px"><b>Rejected by the engine.</b>
           ${esc(S.result?S.result.msg:'')} There is no soft-fail path, so the later stages were never
           executed.</div>`
        : R.state==='done'
          ? (PIPE.some(p=>p.live&&R.results[p.k]!=='passed')
            ? `<div class="note warn" style="margin-top:16px">Accepted in ${S.result?esc(S.result.ms):'?'}, but not
               every deployed stage was enforced: see the ADVISORY rows. The three gateway stages were not
               executed because the gateway is not deployed.</div>`
            : `<div class="note" style="margin-top:16px">All ${liveCount} deployed stages passed, in ${
             S.result?esc(S.result.ms):'?'}. The three gateway stages were not executed because the gateway is
             not deployed.</div>`)
          : R.state==='running'
            ? `<div class="note" style="margin-top:16px">Running against the engine now.</div>`
            : `<div class="note" style="margin-top:16px">Run a capture to execute the pipeline against the
               engine.</div>`}
    </div>
  </div>`;
}

function livenessLine(l){
  if(!l) return '';
  const failed=Array.isArray(l.failed_signals)?l.failed_signals:[];
  const word = l.enforced===true ? (l.live?'Passed':'Failed')
    : 'NOT ENFORCED (engine verdict: '+(l.live?'live':'not live')+')';
  return `<tr><td class="k">Liveness</td><td class="mono">${word}, score ${esc(num(l.score))}${
    failed.length?(', failed: '+esc(failed.join(', '))):''}</td></tr>`;
}

function resultCard(){
  const r=S.result;
  if(!r) return `<div class="card"><div class="card-h">Result</div>
    <div class="card-b" style="padding:44px;text-align:center;color:var(--ink3);font-size:13px">
    No capture has been run in this session.</div></div>`;
  if(!r.ok) return `<div class="card warn"><div class="card-h">Result<div class="spacer"></div>
      <span class="pill warn">Rejected</span></div>
    <div class="card-b">
      <div class="lbl">OUTCOME</div><div class="val big" style="color:var(--warn)">Rejected</div>
      <div class="note warn" style="margin-top:14px">${esc(r.msg)}</div>
      ${r.requestId?`<div class="mono" style="margin-top:10px;font-size:11.5px;color:var(--ink3)">Request id ${
        esc(r.requestId)}</div>`:''}
      ${typeof r.score==='number'?`<div style="margin-top:14px">
        <div class="lbl">SIMILARITY SCORE, FROM THE ENGINE</div>
        <div class="bar warn"><span style="width:${Math.round(Math.max(0,Math.min(1,r.score))*100)}%"></span>
          <b>${num(r.score)}</b></div>
        <div style="font-size:11.5px;color:var(--ink3);margin-top:5px">Threshold ${
          num(r.threshold)}. Below threshold, so no match.</div>
      </div>`:''}
      ${r.liveness?`<table style="margin-top:16px">${livenessLine(r.liveness)}</table>`:''}
    </div></div>`;
  if(r.mode==='enrol') return `<div class="card"><div class="card-h">Result<div class="spacer"></div>
      <span class="pill solid">Enrolled</span></div>
    <div class="card-b">
      <div class="lbl">OUTCOME</div><div class="val big">Template stored</div>
      <table style="margin-top:16px">
        <tr><td class="k" style="width:52%">Subject</td><td class="mono">${esc(r.subject)}</td></tr>
        <tr><td class="k">Template id</td><td class="mono">${esc(String(r.template_id))}</td></tr>
        <tr><td class="k">Quality score</td><td class="mono">${num(r.quality)}</td></tr>
        <tr><td class="k">Frames embedded</td><td class="mono">${num(r.frames_embedded,0)}</td></tr>
        <tr><td class="k">Capture to verdict</td><td class="mono">${esc(r.ms)}</td></tr>
        ${livenessLine(r.liveness)}
      </table>
      <div class="note" style="margin-top:14px">The template lives in the engine process memory only. Restarting
        the engine forgets it, and Revoke on the Subjects screen deletes it for real.</div>
    </div></div>`;
  return `<div class="card${r.verified?'':' warn'}"><div class="card-h">Result<div class="spacer"></div>
      ${r.verified?'<span class="pill solid">Verified</span>':'<span class="pill warn">Not verified</span>'}</div>
    <div class="card-b">
      <div class="lbl">OUTCOME</div>${r.verified?'<div class="val big">Match, liveness passed</div>'
        :'<div class="val big" style="color:var(--warn)">Face match only</div>'}
      <div style="margin-top:14px"><div class="lbl">SIMILARITY SCORE, FROM THE ENGINE</div>
        <div class="bar ok"><span style="width:${Math.round(Math.max(0,Math.min(1,r.score))*100)}%"></span>
          <b>${num(r.score)}</b></div>
        <div style="font-size:11.5px;color:var(--ink3);margin-top:5px">Threshold ${
          num(r.threshold)}. At or above threshold, so the face matches.</div></div>
      ${r.verified?'':`<div class="note warn" style="margin-top:14px">Liveness was not confirmed by an
        enforced pass, so this is not a verified identity.</div>`}
      <div style="height:1px;background:var(--line2);margin:16px 0"></div>
      <table>
        <tr><td class="k" style="width:52%">Subject</td><td class="mono">${esc(r.subject)}</td></tr>
        <tr><td class="k">Capture to verdict</td><td class="mono">${esc(r.ms)}</td></tr>
        ${livenessLine(r.liveness)}
      </table>
    </div></div>`;
}

function scrEnrol(){
  S.mode='enrol';
  const n=S.subjects.length+1;
  return `
  <div class="h">Enrol a subject</div>
  <div class="sh">Captures a reference template for later 1:1 verification. Consent must be recorded first.</div>
  <div class="row">
    <div style="flex:1.2">${captureCard()}</div>
    <div class="col" style="flex:1">
      <div class="card"><div class="card-h">Subject details</div>
        <div class="card-b col" style="gap:11px">
          <div><div class="lbl">SUBJECT ID, SENT AS user_id</div>
            <input class="field" id="eId" value="${esc(S.pendingId||('s-'+String(n).padStart(2,'0')))}">
            <div style="font-size:11.5px;color:var(--ink3);margin-top:4px">Case-insensitive, stored lowercase:
              1-64 of a-z, 0-9, dot, dash, underscore (contract 2.1).</div></div>
          <div><div class="lbl">DISPLAY NAME, KEPT IN THIS BROWSER ONLY</div>
            <input class="field" id="eName" value="Test subject ${String(n).padStart(2,'0')}"></div>
          <div class="note">Consent for biometric capture must be recorded before enrolment. The engine stores
            one embedding per subject in memory; it never stores the frames.</div>
        </div></div>
      ${pipelineCard()}
      ${resultCard()}
    </div>
  </div>`;
}

function scrVerify(){
  S.mode='verify';
  const live=S.subjects.filter(s=>!s.revoked);
  return `
  <div class="h">Verify</div>
  <div class="sh">Captures a fresh burst and matches it 1:1 against an enrolled reference template.</div>
  <div class="row">
    <div class="col" style="flex:1.2">
      ${captureCard()}
      <div class="card"><div class="card-h">Match against</div>
        <div class="card-b">
          ${live.length?`<select class="field" id="vTarget">
            ${live.map(s=>`<option value="${esc(s.id)}" ${s.id===S.target?'selected':''}>${esc(s.id)}, ${
              esc(s.name)}, template ${esc(s.template)}</option>`).join('')}
          </select>`:`<div class="note warn">No subject is enrolled yet. Enrol one first; the engine stores
            templates in memory and a restart empties it.</div>`}
          <div style="font-size:11.5px;color:var(--ink3);margin-top:8px">
            1:1 verification only. 1:N search is out of scope for this build.</div>
        </div></div>
    </div>
    <div class="col" style="flex:1">${pipelineCard()}${resultCard()}</div>
  </div>`;
}

function scrSubjects(){
  const live=S.subjects.filter(s=>!s.revoked);
  const q=live.map(s=>s.quality).filter(v=>typeof v==='number').sort((a,b)=>a-b);
  const med=q.length?q[Math.floor(q.length/2)].toFixed(2):'no data';
  return `
  <div class="h">Subjects and templates</div>
  <div class="sh">Every row is a subject this console enrolled against the engine. Revoking calls
    DELETE /v1/templates and the engine forgets the embedding.</div>
  <div class="row" style="margin-bottom:16px">
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">ACTIVE TEMPLATES</div>
      <div class="val big">${live.length}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">REVOKED</div>
      <div class="val big">${S.subjects.filter(s=>s.revoked).length}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">MEDIAN QUALITY</div>
      <div class="val big">${med}</div>
      <div class="val" style="font-size:12px;color:var(--ink2);margin-top:6px">Reported by the engine at enrolment</div></div></div>
    <div class="card warn" style="flex:1"><div class="card-b"><div class="lbl">STORAGE</div>
      <div class="val big">In memory</div>
      <div class="val" style="font-size:12px;color:var(--ink2);margin-top:6px">
        Not persisted, not encrypted at rest, lost on restart</div></div></div>
  </div>
  <div class="card">
    <div class="card-h">Enrolled subjects<div class="spacer"></div>
      <span class="mono" style="font-size:11px;color:var(--ink3)">Roster kept in this browser, templates in the engine</span></div>
    <div class="card-b" style="padding-top:6px">${S.subjects.length?`<table>
      <tr><th style="width:10%">ID</th><th style="width:24%">NAME</th><th style="width:14%">ENROLLED</th>
      <th style="width:12%">QUALITY</th><th style="width:17%">TEMPLATE</th><th style="width:11%">STATE</th><th></th></tr>
      ${S.subjects.map(s=>`<tr class="${s.revoked?'bad':''}">
        <td class="k mono">${esc(s.id)}</td><td class="k">${esc(s.name)}</td><td class="mono">${esc(s.enrolled)}</td>
        <td class="mono">${typeof s.quality==='number'?s.quality.toFixed(2):'?'}</td>
        <td class="mono">${esc(s.template||'?')}</td>
        <td>${s.revoked?'Revoked':'Active'}</td>
        <td class="act">${s.revoked
          ? `<button class="btn sm ghost" data-reissue="${esc(s.id)}">Enrol again</button>`
          : `<button class="btn sm ghost" data-revoke="${esc(s.id)}">Revoke template</button>`}</td></tr>`).join('')}
    </table>`:`<div style="padding:50px;text-align:center;color:var(--ink3);font-size:13px">
      No subject has been enrolled from this browser yet.</div>`}</div>
  </div>`;
}

function scrBundle(){
  const b=S.lastScan;
  if(!b) return `
    <div class="h">Capture bundle</div>
    <div class="sh">What the browser assembled and sent, exactly as the engine received it.</div>
    <div class="card"><div class="card-b" style="padding:50px;text-align:center;color:var(--ink3);font-size:13px">
      No bundle yet. Run a capture on the Enrol or Verify screen and it appears here.</div></div>`;
  const t=S.bundleTab;
  const frames=b.frames;
  let body;
  if(t==='manifest'){
    const manifest={
      version:b.version,
      device:b.device,
      challenge:{id:b.challengeId,nonce:b.nonce,action:b.action,params:b.params,results:[]},
      frames:'['+b.frameCount+' frames, jpeg_bytes and ts_ms each]',
      msgpack_bytes:b.size
    };
    body=`<div class="blob" style="height:330px">${esc(JSON.stringify(manifest,null,2))}</div>
    <div style="margin-top:12px;font-size:12px;color:var(--ink3)">
      This is the FaceScan v1 map this browser encoded with msgpack and sent as base64 on the JSON transport
      (contract 2.1). In production the crypto gateway would sign a manifest over it with ML-DSA-44 and
      encrypt the payload; neither happens in this build.</div>`;
  } else if(t==='frames'){
    body=frames?`<table>
      <tr><th style="width:9%">FRAME</th><th style="width:14%">OFFSET</th><th style="width:12%">LENGTH</th>
      <th style="width:46%">SHA-256 OF THE JPEG SENT</th><th>ts_ms</th></tr>
      ${frames.map(f=>`<tr><td class="k mono">${f.n}</td><td class="mono">${f.off}</td>
        <td class="mono">${f.len}</td><td class="mono" style="font-size:10.5px">${esc(f.h)}</td>
        <td class="mono">${f.ts}</td></tr>`).join('')}
    </table>
    <div style="margin-top:12px;font-size:12px;color:var(--ink3)">
      All ${frames.length} frames. Hashes computed in the browser with SubtleCrypto over the exact bytes that
      went into the msgpack payload, so they can be recomputed from a captured request.</div>`
    :`<div style="padding:40px;text-align:center;color:var(--ink3);font-size:13px">Hashing the frames…</div>`;
  } else {
    body=`<div class="note warn">Nothing to show. The payload is not encrypted in this build: there is no
      gateway, so no ML-KEM-768 encapsulation and no AES-256-GCM envelope exist. The bytes leave the browser as
      msgpack inside TLS. This panel stays empty rather than showing a fabricated ciphertext.</div>`;
  }
  return `
  <div class="h">Capture bundle</div>
  <div class="sh">What the browser assembled and sent, exactly as the engine received it.</div>
  <div class="row" style="margin-bottom:16px">
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">BUNDLE SIZE</div>
      <div class="val big">${(b.size/1024).toFixed(0)} KB</div>
      <div class="val" style="font-size:12px;color:var(--ink2);margin-top:6px">Ceiling 350 KB, contract 1.1</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">FRAMES</div>
      <div class="val big">${b.frameCount}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">JPEG QUALITY</div>
      <div class="val big">${b.quality}</div>
      <div class="val" style="font-size:12px;color:var(--ink2);margin-top:6px">Long edge ${b.longEdge} px</div></div></div>
    <div class="card warn" style="flex:1"><div class="card-b"><div class="lbl">SIGNATURE</div>
      <div class="val big">None</div>
      <div class="val" style="font-size:12px;color:var(--ink2);margin-top:6px">ML-DSA-44 lives in the gateway</div></div></div>
  </div>
  <div class="card">
    <div class="card-h">Bundle contents<div class="spacer"></div>
      <div class="seg">
        <button class="${t==='manifest'?'on':''}" data-btab="manifest">FaceScan map</button>
        <button class="${t==='frames'?'on':''}" data-btab="frames">Frame table</button>
        <button class="${t==='cipher'?'on':''}" data-btab="cipher">Encrypted bytes</button></div></div>
    <div class="card-b">${body}</div>
  </div>`;
}

function scrSession(){
  const n=S.session;
  const now=Date.now();
  const left=n&&n.expires_ms?Math.max(0,Math.round((n.expires_ms-now)/1000)):null;
  const state=!n?'none':n.state==='replayed'?'Replayed, already consumed':n.state==='consumed'?'Consumed':'Unused';
  const bad=n&&n.state==='replayed';
  return `
  <div class="h">Session and crypto</div>
  <div class="sh">The challenge nonce lifecycle, and the cryptographic envelope this build does not have.</div>
  <div class="row">
    <div class="col" style="flex:1">
      <div class="card ${bad?'warn':''}">
        <div class="card-h">Challenge nonce<div class="spacer"></div>
          <span class="pill ${bad?'warn':''}">${esc(state)}</span></div>
        <div class="card-b">
          ${n?`
          <div class="lbl">VALUE, FROM GET /v1/challenge</div>
          <div class="blob" style="white-space:pre-wrap;word-break:break-all">${esc(n.nonce)}</div>
          <table style="margin-top:14px">
            <tr><td class="k" style="width:46%">Action demanded</td><td class="mono">${esc(n.action||'?')}</td></tr>
            <tr><td class="k">Issued at</td><td class="mono">${typeof n.issued_ms==='number'?hhmmss(n.issued_ms):'?'}</td></tr>
            <tr><td class="k">Expires at</td><td class="mono">${typeof n.expires_ms==='number'?hhmmss(n.expires_ms):'?'}${
              left!==null&&isFinite(left)?(', '+left+' s left'):''}</td></tr>
            <tr><td class="k">Use policy</td><td class="mono">Single use, consumed by the engine</td></tr>
            <tr><td class="k">Bound to a session</td><td class="mono">No. There is no session: no gateway.</td></tr>
          </table>
          ${bad
            ? `<div class="note warn" style="margin-top:14px"><b>This nonce was already spent.</b>
               The next capture will send it again and the engine will answer CHALLENGE_FAIL. The replay is
               real; the rejection comes from the engine, not from this page.</div>`
            : `<div class="note" style="margin-top:14px">The engine mints the nonce, names an action the capture
               must perform, and spends it on the first scan good enough to judge. A scan rejected for NO_FACE
               keeps its nonce; one that reaches the challenge check does not.</div>`}`
          :`<div class="note">No challenge has been requested yet. One is fetched immediately before each
            capture so as little of the 30 second window as possible is spent before the frames.</div>`}
          <div style="margin-top:14px"><button class="btn ghost sm" id="newNonce">Request a new nonce</button></div>
        </div>
      </div>
    </div>
    <div class="col" style="flex:1">
      <div class="card warn"><div class="card-h">Cryptographic envelope<div class="spacer"></div>${NOT_DEPLOYED}</div>
        <div class="card-b">
          <div class="note warn" style="margin-bottom:14px">The crypto gateway image does not exist yet, so none
            of the rows below is running. They describe the production design.</div>
          <table>
          <tr><td class="k" style="width:48%">Key encapsulation</td><td class="mono">ML-KEM-768, not deployed</td></tr>
          <tr><td class="k">Payload encryption</td><td class="mono">AES-256-GCM, not deployed</td></tr>
          <tr><td class="k">Manifest signature</td><td class="mono">ML-DSA-44, not deployed</td></tr>
          <tr><td class="k">Client signing key</td><td class="mono">Not generated in this build</td></tr>
          <tr><td class="k">Transport actually used</td><td class="mono">TLS 1.3 to the edge</td></tr>
          <tr><td class="k">Engine authentication</td><td class="mono">X-Engine-Key, injected at the edge</td></tr>
        </table>
        <div class="note warn" style="margin-top:14px"><b>In this rig the edge holds the shared secret.</b>
          In production it belongs to the gateway process and the edge never sees it.</div>
        </div></div>
      <div class="card"><div class="card-h">What the engine is handed</div>
        <div class="card-b">
          <div class="step"><div class="dot done"></div><div class="txt">FaceScan v1, msgpack
            <div class="t2">Base64 on the JSON transport, or the raw body with X-User-Id (contract 2.1).</div></div></div>
          <div class="step"><div class="dot done"></div><div class="txt">Challenge map
            <div class="t2">The nonce and the action, inside the scan, spent atomically by the engine.</div></div></div>
          <div class="step"><div class="dot"></div><div class="txt" style="opacity:.55">Signature and nonce verdict
            <div class="t2">Gateway not deployed, so the engine receives no crypto verdict to trust.</div></div></div>
        </div></div>
    </div>
  </div>`;
}

function livenessCaptureView(){
  const C=S.capture, G=C.guide, action=S.session&&S.session.action;
  const running=C.state==='running', holding=!G||G.phase==='hold';
  const say=!running?'Preview appears during the capture'
    :holding?'Hold steady':(action?actionText(action):'Keep going');
  return `<div class="view">${running?`<div class="badge">${holding?'HOLD':'MOVE'}</div>`:''}
    <div class="oval ${running?'live':''}">${esc(say)}</div>
    <div class="cap">${running
      ?`Frame ${C.frame} of ${C.total||12}. `+(holding?'Hold still until the prompt changes.'
        :'Keep moving until the capture ends.')
      :'Run a liveness capture to record a burst against a fresh challenge.'}</div></div>`;
}

function signalCell(s){
  const v = !s ? 'unknown' : (s.advisory===true||s.ok===null) ? 'advisory'
    : s.ok===true ? 'passed' : s.ok===false ? 'failed' : 'unknown';
  return {v:v, label:{passed:'Passed',failed:'Failed',advisory:'Advisory, no vote',unknown:'Unknown'}[v]};
}
function scrLiveness(){
  const l=S.lastLiveness;
  const sig=l&&l.signals||{};
  const names=Object.keys(sig);
  const verdict = !l ? 'No run' : l.enforced===true ? (l.live?'Live':'Not live')
    : (l.live?'Live, not enforced':'Not live, not enforced');
  return `
  <div class="h">Liveness analysis</div>
  <div class="sh">Five signals over the burst: duplicate frames, motion, landmark movement, frame timing and the
    challenge action. The verdict is the weakest signal (contract 2.4). This is anti-replay, not certified PAD.</div>
  <div class="row" style="margin-bottom:16px">
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">VERDICT</div>
      <div class="val big" ${l&&!(l.live&&l.enforced===true)?'style="color:var(--warn)"':''}>${verdict}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">SCORE</div>
      <div class="val big">${l?num(l.score):'—'}</div>
      <div class="val" style="font-size:12px;color:var(--ink2);margin-top:6px">Threshold ${
        l?num(l.threshold):'?'}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">SIGNALS</div>
      <div class="val big">${names.length||'—'}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">FRAMES ANALYSED</div>
      <div class="val big">${S.lastScan?S.lastScan.frameCount:'—'}</div></div></div>
  </div>
  <div class="row">
    <div class="card" style="flex:1.3">
      <div class="card-h">Per-signal evidence<div class="spacer"></div>
        <span class="mono" style="font-size:11px;color:var(--ink3)">POST /v1/liveness</span>
        <button class="btn sm pri" id="doLiveness" ${S.run.state==='running'?'disabled':''}
          style="margin-left:10px">Run a liveness capture</button></div>
      <div class="card-b">
        ${names.length?`<table>
          <tr><th style="width:22%">SIGNAL</th><th style="width:12%">SCORE</th><th style="width:12%">RESULT</th>
          <th>EVIDENCE FROM THE ENGINE</th></tr>
          ${names.map(k=>{
            const s=sig[k]||{};
            const c=signalCell(s);
            const detail=Object.keys(s).filter(x=>x!=='score'&&x!=='ok')
              .map(x=>x+': '+JSON.stringify(s[x])).join(', ');
            return `<tr class="${c.v==='failed'?'bad':''}"><td class="k mono">${esc(k)}</td>
              <td class="mono">${s.score===null?'none':num(s.score)}</td>
              <td>${c.label}</td>
              <td class="mono" style="font-size:11px">${esc(detail)}</td></tr>`;
          }).join('')}
        </table>
        <div style="margin-top:12px;font-size:12px;color:var(--ink3)">
          The overall score is the weakest of the voting signals, so one failed signal fails the scan.
          Advisory signals are measured and reported but do not vote (contract 2.4).
          ${l&&l.enforced!==true?'<b>This engine is not enforcing liveness</b>: the gated routes would not block on this verdict.':''}</div>`
        :`<div style="padding:44px;text-align:center;color:var(--ink3);font-size:13px">
          No liveness run yet. Use the button above, or turn on the repeated-frame toggle first to see the
          duplicate-frame check reject it.</div>`}
      </div>
    </div>
    <div class="card" style="flex:1">
      <div class="card-h">Scope and limits</div>
      <div class="card-b col" style="gap:12px">
        ${livenessCaptureView()}
        <div class="note">These signals look for a replayed or synthetic capture: the same frame repeated, a
          burst with impossible timing, or a capture that ignored the challenge action. The repeated-frame toggle
          sends one JPEG for every frame; its rejection shows the duplicate-frame check works, nothing more.</div>
        <div class="note warn">They are <b>not</b> presentation attack detection. A camera pointed at a printed
          photo, a screen or a playing video records changing frames, and this build does not reliably reject
          it. A 3D mask, an injected virtual-camera stream or a deepfake that still moves and turns is not
          covered either. iBeta PAD certification is out of scope for this build.</div>
      </div></div>
  </div>`;
}

function scrResults(){
  const runs=S.log.filter(e=>e.op==='Verify'||e.op==='Enrol');
  const durations=runs.map(e=>parseFloat(e.ms)).filter(v=>!isNaN(v)).sort((a,b)=>a-b);
  const median=durations.length?durations[Math.floor(durations.length/2)].toFixed(1)+' s':'not measured';
  const live=S.subjects.filter(s=>!s.revoked).length;
  const th=S.info&&S.info.thresholds||{};
  return `
  <div class="h">Test results</div>
  <div class="sh">What this deployment has actually measured. Accuracy figures are deliberately absent: no
    evaluation has been run against this build.</div>
  <div class="card warn" style="margin-bottom:16px">
    <div class="card-h">Acceptance targets<div class="spacer"></div>
      <button class="btn sm ghost" id="doExport">Export this session as JSON</button></div>
    <div class="card-b">
      <div class="note warn" style="margin-bottom:14px"><b>No accuracy evaluation has been run.</b>
        False accept and false reject rates require the eval harness (engine/eval) over a labelled set, and no
        such run exists for this deployment. The rows below are left as not measured rather than filled with
        numbers this console cannot support.</div>
      <table>
      <tr><th style="width:34%">MEASURE</th><th style="width:18%">TARGET</th><th style="width:20%">MEASURED HERE</th>
      <th style="width:14%">RESULT</th><th>BASIS</th></tr>
      <tr><td class="k">False Accept Rate</td><td class="mono">1.00% or lower</td><td class="mono">not measured</td>
        <td>Unknown</td><td>Needs a labelled impostor set</td></tr>
      <tr><td class="k">False Reject Rate</td><td class="mono">5.00% or lower</td><td class="mono">not measured</td>
        <td>Unknown</td><td>Needs a labelled genuine set</td></tr>
      <tr><td class="k">Capture to verdict</td><td class="mono">under 3.0 s</td><td class="mono">${median}</td>
        <td>${durations.length?(parseFloat(median)<3?'Met in this session':'Above target'):'Unknown'}</td>
        <td>Median of ${durations.length} run${durations.length===1?'':'s'} in this browser session</td></tr>
      <tr><td class="k">Enrolled subjects</td><td class="mono">10 minimum</td><td class="mono">${live}</td>
        <td>${live>=10?'Met':'Below target'}</td><td>Enrolled from this browser, held in engine memory</td></tr>
      <tr><td class="k">Physical attack categories tested</td><td class="mono">3 minimum</td><td class="mono">0</td>
        <td>Below target</td><td>Only a synthetic repeated-frame replay runs here, not a photo, screen or video</td></tr>
      <tr><td class="k">KEM encapsulation overhead</td><td class="mono">1,088 B ceiling</td>
        <td class="mono">not deployed</td><td>Unknown</td><td>Gateway absent in this build</td></tr>
    </table></div>
  </div>
  <div class="row">
    <div class="card" style="flex:1">
      <div class="card-h">Operating threshold</div>
      <div class="card-b">
        <div class="lbl">MATCH THRESHOLD REPORTED BY /v1/info</div>
        <div class="val big">${th.match===null||th.match===undefined?'unset':esc(th.match)}</div>
        <div class="note warn" style="margin-top:14px">The engine reports its thresholds as unset placeholders
          until the matching evaluation fixes them. The value the engine actually compares against is returned on
          every /v1/verify response and is shown on the Result card.</div>
      </div>
    </div>
    <div class="card" style="flex:1">
      <div class="card-h">Method and caveats</div>
      <div class="card-b col" style="gap:12px">
        <div><div class="lbl">WHAT THIS SESSION EXERCISED</div>
          <div style="font-size:12.5px;color:var(--ink2);line-height:1.6">
            ${runs.length?(runs.length+' capture run'+(runs.length===1?'':'s')+' from this browser against the live engine.')
            :'Nothing yet. Run a capture to populate this.'}</div></div>
        <div style="height:1px;background:var(--line2)"></div>
        <div class="note">A handful of runs by one person on one device is a smoke test, not an evaluation. Do
          not quote anything on this screen as an accuracy claim.</div>
      </div>
    </div>
  </div>`;
}

function scrLog(){
  const f=S.logFilter;
  const rows=S.log.filter(e=> f==='all'?true : f==='rej'?!e.ok : f==='acc'?e.ok : true);
  return `
  <div class="h">Activity log</div>
  <div class="sh">Every attempt made from this browser in this session, accepted and rejected, with the engine
    error code that rejected it. Nothing is persisted: a reload starts an empty log.</div>
  <div class="row" style="margin-bottom:16px">
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">ATTEMPTS LOGGED</div>
      <div class="val big">${S.log.length}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">REJECTED</div>
      <div class="val big">${S.log.filter(e=>!e.ok).length}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">LIVENESS FAILURES</div>
      <div class="val big">${S.log.filter(e=>!e.ok&&/LIVENESS_FAIL|[Nn]ot live/.test(e.d)).length}</div></div></div>
    <div class="card" style="flex:1"><div class="card-b"><div class="lbl">CHALLENGE FAILURES</div>
      <div class="val big">${S.log.filter(e=>!e.ok&&/CHALLENGE_FAIL/.test(e.d)).length}</div></div></div>
  </div>
  <div class="card">
    <div class="card-h">Events<div class="spacer"></div>
      <div class="seg">
        <button class="${f==='all'?'on':''}" data-filter="all">All</button>
        <button class="${f==='rej'?'on':''}" data-filter="rej">Rejected only</button>
        <button class="${f==='acc'?'on':''}" data-filter="acc">Accepted only</button></div>
      <button class="btn sm ghost" id="doExportLog">Export</button></div>
    <div class="card-b" style="padding-top:6px">
      ${rows.length?`<table>
        <tr><th style="width:11%">TIME</th><th style="width:11%">OPERATION</th><th style="width:13%">SUBJECT</th>
        <th style="width:11%">RESULT</th><th style="width:10%">DURATION</th><th>DETAIL FROM THE ENGINE</th></tr>
        ${rows.map(e=>`<tr class="${e.ok?'':'bad'}">
          <td class="mono">${e.t}</td><td class="k">${esc(e.op)}</td><td class="mono">${esc(e.sub)}</td>
          <td class="${e.ok?'':'k'}">${e.ok?'Accepted':'Rejected'}</td><td class="mono">${esc(e.ms)}</td>
          <td>${esc(e.d)}</td></tr>`).join('')}
      </table>`:`<div style="padding:50px;text-align:center;color:var(--ink3);font-size:13px">
        No events match this filter.</div>`}
      <div style="margin-top:14px;font-size:12px;color:var(--ink3)">Showing ${rows.length} of ${S.log.length}</div>
    </div>
  </div>`;
}

function scrGone(){ return `<div class="gone">
  <div class="box"></div><h2>Engine unreachable</h2>
  <p>The console could not reach the engine. Nothing cached is shown, because every number on this console is
     read live from the engine.</p>
  <p class="mono" style="font-size:11.5px;color:var(--ink3)">
     Same-origin API, GET /health failed${S.connected?'':' (backend toggle is off)'}</p>
  <div style="margin-top:8px"><button class="btn pri" id="doReconnect">Retry</button></div></div>`; }

const VIEWS={overview:scrOverview,enrol:scrEnrol,verify:scrVerify,subjects:scrSubjects,
  bundle:scrBundle,session:scrSession,liveness:scrLiveness,results:scrResults,log:scrLog};

function render(){
  renderPills(); renderNav(); renderDemo();
  const dead=!S.connected||(!S.health&&S.booted);
  $('#main').innerHTML = dead ? scrGone() : VIEWS[S.screen]();
  if(SDK&&(S.screen==='enrol'||S.screen==='verify'||S.screen==='liveness')) SDK.mountVideo();
}

function revokeModal(id){
  const s=sub(id);
  $('#modal').innerHTML=`<div class="scrim" id="scrim"><div class="modal" role="dialog" aria-modal="true">
    <div class="modal-h">Revoke the template for ${esc(s.id)}?</div>
    <div class="modal-b">
      <p style="margin-bottom:12px">This calls <span class="mono">DELETE /v1/templates/${esc(s.id)}</span> on the
      engine. The stored embedding is discarded immediately and cannot be matched again.</p>
      <p style="margin-bottom:12px">There is no soft delete and nothing is retained. Re-enrolling the subject
      needs a fresh capture, which is what the consent screen promises the subject.</p>
      <p>This is the property that makes a compromised biometric recoverable rather than permanent.</p>
    </div>
    <div class="modal-f"><button class="btn ghost" id="mCancel">Cancel</button>
      <button class="btn pri" id="mGo" data-id="${esc(id)}">Revoke template</button></div></div></div>`;
  $('#mGo').focus();
}
function closeModal(){ $('#modal').innerHTML=''; }

function download(name,obj){
  const blob=new Blob([JSON.stringify(obj,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download=name;
  document.body.appendChild(a); a.click(); a.remove();
}

document.addEventListener('click',async e=>{
  const t=e.target.closest('button'); if(!t) return;

  if(t.dataset.go){

    if(RUNS&&RUNS.current()) RUNS.cancel('navigated');
    S.screen=t.dataset.go; render(); return;
  }
  if(t.dataset.btab){ S.bundleTab=t.dataset.btab; render(); return; }
  if(t.dataset.filter){ S.logFilter=t.dataset.filter; render(); return; }
  if(t.dataset.revoke){ revokeModal(t.dataset.revoke); return; }
  if(t.dataset.reissue){
    const s=sub(t.dataset.reissue);
    S.pendingId=s.id; S.screen='enrol';
    toast('Capture again to enrol '+s.id+'. A template cannot be reissued without a fresh capture.');
    render(); return;
  }
  if(t.dataset.tog){
    const k=t.dataset.tog; S[k]=!S[k];
    if(k==='connected'&&!S.connected) toast('Backend toggled off. Requests now go to a dead port.',true);
    if(k==='cameraOff'&&S.cameraOff) toast('Camera skipped. Captures cannot run.',true);
    if(k==='replayNonce'&&S.replayNonce&&!S.lastNonce)
      toast('No nonce has been issued yet. Run one capture first, then replay it.',true);
    if(k==='connected'&&S.connected) await boot();
    render(); return;
  }

  switch(t.id){
    case 'demoMin':
      $('#demo').classList.toggle('min');
      document.body.classList.toggle('demohid');
      t.textContent=$('#demo').classList.contains('min')?'Show':'Hide'; break;
    case 'doReconnect': S.connected=true; await boot(); toast('Retried /health.'); break;
    case 'doCapture': startCapture(); break;
    case 'doLiveness': runLivenessOnly(); break;
    case 'doReset': resetRun(); render(); break;
    case 'newNonce': {
      const got=await getChallenge();
      const ch=got.challenge;
      S.replayNonce=false;
      toast(ch?('New nonce issued, action '+ch.action+'.')
        :withId('The engine did not issue a nonce ('+errCode(got.reply)+').',got.reply),!ch);
      render(); break;
    }
    case 'mCancel': closeModal(); break;
    case 'mGo': {
      const id=t.dataset.id; closeModal();
      const r=await deleteTemplates(id);
      const s=sub(id);
      if(r.ok){
        s.revoked=true; saveRoster();
        if(S.target===id) S.target=(S.subjects.filter(x=>!x.revoked)[0]||{}).id||null;
        addLog({op:'Revoke',sub:id,ok:true,ms:secs(r.ms),
          d:'Engine deleted '+(r.data&&r.data.deleted!==undefined?r.data.deleted:'the')+' template(s)'});
        toast('Template revoked. The engine has forgotten it.');
      } else {
        addLog({op:'Revoke',sub:id,ok:false,ms:secs(r.ms),d:withId(errCode(r)+': '+errMsg(r),r)});
        toast(withId(errCode(r)+': '+errMsg(r),r),true);
      }
      render(); break;
    }
    case 'doExport':
      download('facetech-console-session-'+Date.now()+'.json',
        {engine:S.info,health:S.health,subjects:S.subjects,log:S.log,lastResult:S.result,
         lastLiveness:S.lastLiveness,note:'Captured from the demo rig. No gateway deployed.'});
      toast('Session exported.'); break;
    case 'doExportLog':
      download('facetech-console-log-'+Date.now()+'.json',S.log);
      toast('Activity log exported.'); break;
  }
});
document.addEventListener('change',e=>{
  if(e.target.id==='vTarget'){ S.target=e.target.value; }
  if(e.target.id==='eId'){ S.pendingId=e.target.value.trim()||null; }
});
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeModal(); });
document.addEventListener('click',e=>{ if(e.target.id==='scrim') closeModal(); });

async function boot(){
  await getHealth();
  if(S.health) await getInfo();
  S.booted=true;
  render();
}
render();
boot();
