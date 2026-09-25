const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { makeClock } = require('./harness.js');
const code = fs.readFileSync(path.join(__dirname, '../integration-demo/evaluation-form.js'), 'utf8');
const ready = {enabled:true,evaluation_only:true,retention_days:7,consent_version:'storage-consent-v1'};
function response(value, status=200, type='application/json') {
  return {ok:status===200,status,headers:{get:()=>type},json:async()=>value};
}
async function setup(value=ready, origin='https://facetech.31.97.186.120.sslip.io', storage=new Map()) {
  const elements = Object.fromEntries(['evaluationHome','probe','verify','evaluation','storageNotice','captureConsent','testSubject','testCase','testLighting','testGlasses','log','buildStatus','makeTester','enroll','userId','retryRecording','recordingDiagnostic','consentState'].map(id=>[id,{hidden:false,checked:false,value:'',textContent:'',listeners:{},addEventListener(t,fn){this.listeners[t]=fn},dispatchEvent(e){this.listeners[e.type]?.()}}]));
  elements.evaluationHome.href='https://facetech.31.97.186.120.sslip.io/?v=ui-v6';
  const location={origin,replace(url){this.redirected=url}};
  elements.testSubject.value='T01';elements.testCase.value='self';elements.testLighting.value='room';
  const clock = makeClock(), calls=[], window={listeners:{},addEventListener(t,fn){this.listeners[t]=fn}};
  const localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
  const state={reply:()=>response(value)};
  const fetch=async(url,init)=>{
    calls.push({url,init});
    if(url==='/health') return response({buildId:'test-build'});
    if(!init.signal) return state.reply();
    return new Promise((resolve,reject)=>{
      init.signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true});
      Promise.resolve().then(()=>state.reply()).then(resolve,reject);
    });
  };
  vm.runInNewContext(code,{window,localStorage,location,URL,Event,crypto,AbortController,document:{getElementById:id=>elements[id]},fetch,setTimeout:clock.setTimeout,clearTimeout:clock.clear});
  await clock.drain();
  return {window,e:elements,clock,calls,state,location,storage};
}
for (const origin of ['https://192.168.1.5:8443','http://localhost:8080','http://facetech.31.97.186.120.sslip.io']) {
  test('wrong backend origin redirects before checking storage or enabling capture: '+origin,async()=>{
    const s=await setup({enabled:false},origin);
    assert.equal(s.location.redirected,s.e.evaluationHome.href);
    assert.equal(s.calls.length,0);
    assert.equal(s.window.facetechCaptureOptions,undefined);
    for(const id of ['enroll','verify','probe']) assert.equal(s.e[id].disabled,true);
  });
}
test('canonical origin stays on the page and diagnostics identify the actual origin',async()=>{
  const s=await setup();assert.equal(s.location.redirected,undefined);
  assert.equal(JSON.parse(s.e.recordingDiagnostic.textContent).origin,s.location.origin);
});
test('only consented recording is offered and checkbox remains unchecked initially',async()=>{
  const s=await setup();
  assert.equal(s.e.captureConsent.checked,false);
  await assert.rejects(s.window.facetechCaptureOptions(),/Select.*Save attempts for this tester/);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname,'../integration-demo/index.html'),'utf8'),/captureNoSave|Process without saving/);
});
test('selected consent supplies metadata and a fresh credentialed no-store status request',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;s.e.testSubject.value='t01';s.e.testCase.value='different_person';
  s.e.captureConsent.dispatchEvent(new Event('change'));
  assert.match(s.e.consentState.textContent,/Consent saved/);
  const meta=(await s.window.facetechCaptureOptions()).captureMeta;
  assert.equal(meta.subject,'T01');assert.equal(meta.label,'bona_fide');assert.equal(meta.consent,'storage-consent-v1');
  const calls=s.calls.filter(c=>c.url.startsWith('/capture-status'));
  assert.equal(calls.length,2);assert.notEqual(calls[0].url,calls[1].url);
  assert.equal(calls[1].init.cache,'no-store');assert.equal(calls[1].init.credentials,'same-origin');assert.equal(calls[1].init.redirect,'error');
});
test('subject is validated before recording',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;s.e.testSubject.value='';s.e.testSubject.dispatchEvent(new Event('input'));
  await assert.rejects(s.window.facetechCaptureOptions(),/Select|person code/);
});
for(const [name,value] of [['disabled',{enabled:false}],['missing evaluation flag',{enabled:true}],['string booleans',{...ready,enabled:'true'}],['unknown consent',{...ready,consent_version:'future'}],['missing retention',{...ready,retention_days:undefined}],['null',null],['array',[]]]) {
  test('fails closed for '+name,async()=>{
    const s=await setup(value);s.e.captureConsent.checked=true;
    await assert.rejects(s.window.facetechCaptureOptions(),/Capture is blocked/);
    assert.equal(s.e.captureConsent.checked,true);
    assert.match(s.e.recordingDiagnostic.textContent,/failure/);
  });
}
test('disabled initial response recovers on retry without reselecting consent',async()=>{
  const s=await setup({enabled:false});s.e.captureConsent.checked=true;
  s.state.reply=()=>response(ready);
  assert.equal(await s.e.retryRecording.onclick(),true);
  assert.equal((await s.window.facetechCaptureOptions()).captureMeta.consent,'storage-consent-v1');
});
test('fresh start catches storage disabled after initial ready response',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;s.state.reply=()=>response({enabled:false});
  await assert.rejects(s.window.facetechCaptureOptions(),/did not confirm/);
});
for(const [name,reply,pattern] of [
  ['auth',()=>response({},401),/Sign-in is required/],
  ['HTML',()=>response({},200,'text/html'),/unexpected response type/],
  ['network',()=>{throw Error('network failed')},/network failed/],
  ['bad JSON',()=>({...response({}),json:async()=>{throw Error('invalid JSON')}}),/invalid JSON/],
]) test('reports '+name+' failure then recovers',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;s.state.reply=reply;
  await assert.rejects(s.window.facetechCaptureOptions(),pattern);
  s.state.reply=()=>response(ready);
  assert.equal((await s.window.facetechCaptureOptions()).captureMeta.subject,'T01');
});
test('timeout is bounded and a later retry recovers',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;s.state.reply=()=>new Promise(()=>{});
  const pending=assert.rejects(s.window.facetechCaptureOptions(),/timed out/);
  await s.clock.advance(8000);await pending;
  assert.equal(s.e.retryRecording.disabled,false);
  s.state.reply=()=>response(ready);assert.equal(await s.e.retryRecording.onclick(),true);
});
test('pending checks are coalesced and withdrawn consent cannot proceed',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;
  let release;s.state.reply=()=>new Promise(r=>{release=r});
  const a=s.window.facetechCaptureOptions(),b=s.e.retryRecording.onclick();
  const rejected=assert.rejects(a,/Select.*Save attempts for this tester/);
  await s.clock.drain();s.e.captureConsent.checked=false;release(response(ready));
  await rejected;await b;
  assert.equal(s.calls.filter(c=>c.url.startsWith('/capture-status')).length,2);
});
test('phone photo/video labels remain distinct with explicit glasses metadata',async()=>{
  const s=await setup();s.e.captureConsent.checked=true;s.e.testGlasses.checked=true;
  for(const kind of ['screen_photo','screen_video']) {
    s.e.testCase.value=kind;const meta=(await s.window.facetechCaptureOptions()).captureMeta;
    assert.equal(meta.case,kind);assert.equal(meta.label,'screen_phone');assert.equal(meta.accessory,'glasses');
  }
});
test('consent reset preserves tester ID and requires a new selection',async()=>{
  const s=await setup();s.e.makeTester.onclick();const subject=s.e.testSubject.value;
  assert.match(subject,/^T0[A-F0-9]{8}$/);assert.equal(s.e.userId.value,'r4-'+subject.toLowerCase());
  s.e.captureConsent.checked=true;s.window.facetechResetConsent();
  assert.equal(s.e.captureConsent.checked,false);assert.equal(s.e.testSubject.value,subject);
  await assert.rejects(s.window.facetechCaptureOptions(),/Select.*Save attempts for this tester/);
});
test('receipt distinguishes stored diagnostics from failed storage',async()=>{
  const s=await setup();s.state.reply=()=>response({stored:true,id:13,diagnostics:true});
  await s.window.facetechCaptureReceipt('req13');assert.match(s.e.log.textContent,/capture 13; diagnostics saved/);
  s.state.reply=()=>response({stored:false});await s.window.facetechCaptureReceipt('req14');
  assert.match(s.e.log.textContent,/Recording not saved. Stop testing/);
});

function consent(s, checked=true) {
  s.e.captureConsent.checked=checked;
  s.e.captureConsent.dispatchEvent(new Event('change'));
}
test('consent and tester target survive repeated attempts and reload',async()=>{
  const s=await setup();s.e.userId.value='enrolled-t01';consent(s);
  await s.window.facetechCaptureOptions();await s.window.facetechCaptureOptions();
  const next=await setup(ready,undefined,s.storage);
  assert.equal(next.e.testSubject.value,'T01');assert.equal(next.e.userId.value,'enrolled-t01');
  assert.equal(next.e.captureConsent.checked,true);
  assert.equal((await next.window.facetechCaptureOptions()).captureMeta.subject,'T01');
});
test('new tester has no inherited grant; returning tester retains their own grant',async()=>{
  const s=await setup();consent(s);s.e.makeTester.onclick();
  assert.equal(s.e.captureConsent.checked,false);
  await assert.rejects(s.window.facetechCaptureOptions(),/Select/);
  s.e.testSubject.value='T01';s.e.testSubject.dispatchEvent(new Event('input'));
  assert.equal(s.e.captureConsent.checked,true);
});
test('withdrawal remains withdrawn after reload',async()=>{
  const s=await setup();consent(s);consent(s,false);
  const next=await setup(ready,undefined,s.storage);
  assert.equal(next.e.captureConsent.checked,false);
  await assert.rejects(next.window.facetechCaptureOptions(),/Select/);
});
test('different consent version is never restored',async()=>{
  const storage=new Map([['facetech-tester-consent-v1',JSON.stringify({grants:{T01:'future'},subject:'T01',target:'demo'})]]);
  const s=await setup(ready,undefined,storage);
  assert.equal(s.e.captureConsent.checked,false);
});
test('unavailable storage retains consent on page with explicit notice',async()=>{
  const storage={get(){throw Error('blocked')},set(){throw Error('blocked')}};
  const s=await setup(ready,undefined,storage);consent(s);
  await s.window.facetechCaptureOptions();await s.window.facetechCaptureOptions();
  assert.match(s.e.consentState.textContent,/page only/);
});
test('changing subject during preflight cannot carry the prior grant',async()=>{
  const s=await setup();consent(s);
  let release;s.state.reply=()=>new Promise(r=>release=r);
  const pending=assert.rejects(s.window.facetechCaptureOptions(),/Select/);
  await s.clock.drain();s.e.testSubject.value='T02';release(response(ready));await pending;
});
test('withdrawal or clearing consent in another tab disables this tab',async()=>{
  const s=await setup();consent(s);
  s.window.listeners.storage({key:'facetech-tester-consent-v1',newValue:JSON.stringify({grants:{}})});
  assert.equal(s.e.captureConsent.checked,false);
  await assert.rejects(s.window.facetechCaptureOptions(),/Select/);
  consent(s);s.window.listeners.storage({key:null,newValue:null});
  assert.equal(s.e.captureConsent.checked,false);
});
