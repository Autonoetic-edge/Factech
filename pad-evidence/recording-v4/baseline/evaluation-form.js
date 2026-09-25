(() => {
  const get = id => document.getElementById(id);
  let status = null;
  let unavailable = false;
  // Never restore recording permission from browser form history.
  get('captureConsent').checked = false;
  get('captureNoSave').checked = false;
  get('captureConsent').addEventListener('change', () => {
    if (get('captureConsent').checked) get('captureNoSave').checked = false;
  });
  get('captureNoSave').addEventListener('change', () => {
    if (get('captureNoSave').checked && get('captureConsent').checked) {
      get('captureConsent').checked = false;
      get('captureConsent').dispatchEvent(new Event('change'));
    }
  });
  get('makeTester').onclick = () => {
    if (get('enroll').disabled) return;
    const code = 'T0' + crypto.randomUUID().replaceAll('-', '').slice(0, 8).toUpperCase();
    get('testSubject').value = code;
    get('userId').value = 'r3-' + code.toLowerCase();
  };
  window.facetechResetConsent = () => {
    get('captureConsent').checked = false;
    get('captureNoSave').checked = false;
  };
  fetch('/health', {cache: 'no-store'}).then(r => {
    if (!r.ok) throw Error('Service not ready');
    return r.json();
  }).then(value => {
    get('buildStatus').textContent = 'Server build: ' + value.buildId;
  }).catch(() => { get('buildStatus').textContent = 'Server build unavailable. Reload before testing.'; });
  fetch('/capture-status', {cache: 'no-store'}).then(r => {
    if (!r.ok) throw Error('Recording status unavailable');
    return r.json();
  }).then(value => {
    status = value;
    if (!value.enabled || !value.evaluation_only) {
      get('storageNotice').textContent = 'Recording is unavailable. You can explicitly choose processing without saving.';
      return;
    }
    get('evaluation').hidden = false;
    get('storageNotice').textContent = `Saved frames and scores expire after ${value.retention_days} days. Both accepted and rejected submitted attempts are recorded when you consent. Camera cancellations before sending have no saved frames.`;
  }).catch(() => { unavailable = true; get('storageNotice').textContent = 'Recording status unavailable. Reload before testing.'; });

  window.facetechCaptureOptions = () => {
    if (!status) throw Error(unavailable ? 'Cannot check recording status. Reload before testing.' : 'Recording status is loading. Please try again in a moment.');
    const save = get('captureConsent').checked;
    const noSave = get('captureNoSave').checked;
    if (save === noSave) throw Error('Choose Save this attempt with consent, or Process without saving frames.');
    if (noSave) return null;
    if (!status.enabled || !status.evaluation_only) throw Error('Recording is unavailable. Choose processing without saving or try again later.');
    const subject = get('testSubject').value.trim().toUpperCase();
    if (!/^(?=[A-Z0-9-]*[0-9])[A-Z0-9][A-Z0-9-]{1,15}$/.test(subject)) throw Error('Enter a person code such as T01 before recording.');
    const testCase = get('testCase').value;
    const label = {self:'bona_fide',different_person:'bona_fide',print:'print',screen_photo:'screen_phone',screen_video:'screen_phone'}[testCase];
    const accessory = get('testGlasses')?.checked ? {accessory:'glasses'} : {};
    return {captureMeta: {consent: status.consent_version, subject, label, case: testCase, lighting: get('testLighting').value, ...accessory}};
  };
  window.facetechCaptureReceipt = async requestId => {
    if (!requestId || !status?.evaluation_only) return;
    try {
      const r = await fetch('/review/api/receipt/' + encodeURIComponent(requestId), {cache:'no-store'});
      if (!r.ok) throw Error('receipt unavailable');
      const receipt = await r.json();
      get('log').textContent += receipt.stored
        ? `Recording saved: capture ${receipt.id}; diagnostics ${receipt.diagnostics ? 'saved' : 'unavailable'}; review at /review\n`
        : 'Recording not saved (no consent, storage limit, or storage error).\n';
    } catch { get('log').textContent += 'Recording status could not be confirmed.\n'; }
  };
})();
