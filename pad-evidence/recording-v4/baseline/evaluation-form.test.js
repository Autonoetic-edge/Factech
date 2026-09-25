const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../integration-demo/evaluation-form.js'), 'utf8');
async function setup(status) {
  const elements = Object.fromEntries(['evaluation','storageNotice','captureConsent','testSubject','testCase','testLighting','testGlasses','log','captureNoSave','buildStatus','makeTester','enroll','userId'].map(id=>[id,{hidden:true,checked:false,value:'',textContent:'',listeners:{},addEventListener(t,fn){this.listeners[t]=fn},dispatchEvent(e){this.listeners[e.type]?.()}}]));
  const window = {};
  vm.runInNewContext(code,{window,Event,crypto,document:{getElementById:id=>elements[id]},fetch:async()=>({ok:true,json:async()=>status})});
  await new Promise(resolve=>setImmediate(resolve));
  return {window,elements};
}
test('explicit no-save is required and leaves storage metadata absent',async()=>{
  const {window,elements:e}=await setup({enabled:true,evaluation_only:true,retention_days:7});
  assert.throws(()=>window.facetechCaptureOptions(),/Choose Save/);
  e.captureNoSave.checked=true;
  assert.equal(window.facetechCaptureOptions(),null);
});
test('consent requires subject and carries independent genuine label',async()=>{
  const {window,elements:e}=await setup({enabled:true,evaluation_only:true,retention_days:7,consent_version:'storage-consent-v1'});
  e.captureConsent.checked=true;
  assert.throws(()=>window.facetechCaptureOptions(),/person code/);
  e.testSubject.value='t01';e.testCase.value='different_person';e.testLighting.value='room';
  const meta=window.facetechCaptureOptions().captureMeta;
  assert.equal(meta.subject,'T01');assert.equal(meta.label,'bona_fide');assert.equal(meta.case,'different_person');
});
test('disabled capture cannot be opted into by the checkbox',async()=>{
  const {window,elements}=await setup({enabled:false});elements.captureConsent.checked=true;
  assert.throws(()=>window.facetechCaptureOptions(),/Recording is unavailable/);
});

test('phone photo/video remain distinct and glasses are explicit metadata',async()=>{
  const {window,elements:e}=await setup({enabled:true,evaluation_only:true,retention_days:7,consent_version:'storage-consent-v1'});
  e.captureConsent.checked=true;e.testSubject.value='T01';e.testLighting.value='room';
  for(const value of ['screen_photo','screen_video']) {
    e.testCase.value=value;
    assert.equal(window.facetechCaptureOptions().captureMeta.case,value);
    assert.equal(window.facetechCaptureOptions().captureMeta.label,'screen_phone');
  }
  e.testCase.value='self';e.testGlasses.checked=true;
  assert.equal(window.facetechCaptureOptions().captureMeta.accessory,'glasses');
});


test('tester code creates a separate fresh target and persists across consent reset',async()=>{
  const {window,elements:e}=await setup({enabled:true,evaluation_only:true,retention_days:7});
  e.makeTester.onclick();
  const subject=e.testSubject.value;
  assert.match(subject,/^T0[A-F0-9]{8}$/);
  assert.equal(e.userId.value,'r3-'+subject.toLowerCase());
  e.captureConsent.checked=true;
  window.facetechResetConsent();
  assert.equal(e.captureConsent.checked,false);
  assert.equal(e.captureNoSave.checked,false);
  assert.equal(e.testSubject.value,subject);
  assert.throws(()=>window.facetechCaptureOptions(),/Choose Save/);
});

test('choosing no-save withdraws existing recording permission',async()=>{
  const {elements:e}=await setup({enabled:true,evaluation_only:true});
  e.captureConsent.checked=true;e.captureNoSave.checked=true;
  e.captureNoSave.dispatchEvent(new Event('change'));
  assert.equal(e.captureConsent.checked,false);
});
