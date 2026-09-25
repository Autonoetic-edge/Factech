const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../integration-demo/evaluation-form.js'), 'utf8');
async function setup(status) {
  const elements = Object.fromEntries(['evaluation','storageNotice','captureConsent','testSubject','testCase','testLighting','testGlasses','log'].map(id=>[id,{hidden:true,checked:false,value:'',textContent:''}]));
  const window = {};
  vm.runInNewContext(code,{window,document:{getElementById:id=>elements[id]},fetch:async()=>({ok:true,json:async()=>status})});
  await new Promise(resolve=>setImmediate(resolve));
  return {window,elements};
}
test('unchecked consent leaves storage metadata absent',async()=>{
  const {window}=await setup({enabled:true,evaluation_only:true,retention_days:7});
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
  assert.equal(window.facetechCaptureOptions(),null);
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
