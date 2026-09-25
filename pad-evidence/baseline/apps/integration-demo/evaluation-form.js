(() => {
  const get = id => document.getElementById(id);
  let status = null;
  let unavailable = false;
  fetch('/capture-status', {cache: 'no-store'}).then(r => {
    if (!r.ok) throw Error('Recording status unavailable');
    return r.json();
  }).then(value => {
    status = value;
    if (!value.enabled || !value.evaluation_only) return;
    get('evaluation').hidden = false;
    get('storageNotice').textContent = `Saved frames and scores expire after ${value.retention_days} days. Both accepted and rejected submitted attempts are recorded when you consent. Camera cancellations before sending have no saved frames.`;
  }).catch(() => { unavailable = true; });

  window.facetechCaptureOptions = () => {
    if (!status) throw Error(unavailable ? 'Cannot check recording status. Reload before testing.' : 'Recording status is loading. Please try again in a moment.');
    if (!status.enabled || !status.evaluation_only || !get('captureConsent').checked) return null;
    const subject = get('testSubject').value.trim().toUpperCase();
    if (!/^(?=[A-Z0-9-]*[0-9])[A-Z0-9][A-Z0-9-]{1,15}$/.test(subject)) throw Error('Enter a person code such as T01 before recording.');
    const testCase = get('testCase').value;
    const label = {self:'bona_fide',different_person:'bona_fide',print:'print',screen_photo:'screen_phone',screen_video:'screen_phone'}[testCase];
    return {captureMeta: {consent: status.consent_version, subject, label, case: testCase, lighting: get('testLighting').value}};
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
