(() => {
  const get = id => document.getElementById(id);
  let status = null;
  let checking = null;
  const consentState = () => {
    get('consentState').textContent = get('captureConsent').checked
      ? 'Consent selected for this attempt.' : 'Select consent before enrolling or verifying. Consent resets after each attempt.';
  };
  // Never restore recording permission from browser form history.
  get('captureConsent').checked = false;
  get('captureConsent').addEventListener('change', consentState);
  consentState();
  get('makeTester').onclick = () => {
    if (get('enroll').disabled) return;
    const code = 'T0' + crypto.randomUUID().replaceAll('-', '').slice(0, 8).toUpperCase();
    get('testSubject').value = code;
    get('userId').value = 'r4-' + code.toLowerCase();
  };
  window.facetechResetConsent = () => {
    get('captureConsent').checked = false;
    consentState();
  };
  fetch('/health', {cache: 'no-store'}).then(r => {
    if (!r.ok) throw Error('Service not ready');
    return r.json();
  }).then(value => {
    get('buildStatus').textContent = 'Server build: ' + value.buildId;
  }).catch(() => { get('buildStatus').textContent = 'Server build unavailable. Reload before testing.'; });
  function refreshStatus() {
    if (checking) return checking;
    status = null;
    get('retryRecording').disabled = true;
    get('storageNotice').textContent = 'Checking recording availability...';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    const diagnostic = {interface: 'ui-v4', checked_at: new Date().toISOString()};
    checking = (async () => {
      try {
        const r = await fetch('/capture-status?check=' + crypto.randomUUID(), {
          cache: 'no-store', credentials: 'same-origin', redirect: 'error',
          headers: {Accept: 'application/json'}, signal: controller.signal,
        });
        diagnostic.http = r.status;
        diagnostic.content_type = r.headers.get('content-type');
        if (!r.ok) throw Error(r.status === 401 ? 'Sign-in is required. Reopen this page and sign in, then retry.' : `Recording check returned HTTP ${r.status}.`);
        if (!diagnostic.content_type?.includes('application/json')) throw Error('Recording check returned an unexpected response type.');
        const value = await r.json();
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Recording check returned invalid status data.');
        for (const key of ['enabled', 'evaluation_only', 'retention_days', 'consent_version']) {
          const v = value[key];
          diagnostic[key] = typeof v === 'string' ? v.slice(0, 80)
            : ['boolean', 'number'].includes(typeof v) ? v : 'missing or invalid';
        }
        if (value.enabled !== true || value.evaluation_only !== true) throw Error('Server response did not confirm evaluation recording is enabled.');
        if (value.consent_version !== 'storage-consent-v1' || !Number.isInteger(value.retention_days) || value.retention_days < 1) throw Error('Recording consent or retention policy is not supported by this page.');
        status = value;
        get('storageNotice').textContent = `Recording is ready. Saved frames and scores expire after ${value.retention_days} days. Both accepted and rejected submitted attempts are recorded with consent. Camera cancellations before sending have no saved frames.`;
        return true;
      } catch (error) {
        diagnostic.failure = controller.signal.aborted ? 'Recording check timed out.' : error.message;
        get('storageNotice').textContent = diagnostic.failure + ' Capture is blocked. Tap Check recording again.';
        return false;
      } finally {
        clearTimeout(timer);
        get('recordingDiagnostic').textContent = JSON.stringify(diagnostic, null, 2);
        get('retryRecording').disabled = false;
      }
    })().finally(() => { checking = null; });
    return checking;
  }
  get('retryRecording').onclick = refreshStatus;
  refreshStatus();

  function captureMeta() {
    if (!get('captureConsent').checked) throw Error('Select “Save this attempt” to consent to recording before enrolling or verifying.');
    const subject = get('testSubject').value.trim().toUpperCase();
    if (!/^(?=[A-Z0-9-]*[0-9])[A-Z0-9][A-Z0-9-]{1,15}$/.test(subject)) throw Error('Enter a person code such as T01 before recording.');
    const testCase = get('testCase').value;
    const label = {self:'bona_fide',different_person:'bona_fide',print:'print',screen_photo:'screen_phone',screen_video:'screen_phone'}[testCase];
    const accessory = get('testGlasses')?.checked ? {accessory:'glasses'} : {};
    if (!label) throw Error('Choose a valid test case before recording.');
    return {subject, label, case: testCase, lighting: get('testLighting').value, ...accessory};
  }
  window.facetechCaptureOptions = async () => {
    captureMeta();
    if (!await refreshStatus()) throw Error(get('storageNotice').textContent);
    // Consent may be withdrawn while the network check is pending.
    return {captureMeta: {consent: status.consent_version, ...captureMeta()}};
  };
  window.facetechCaptureReceipt = async requestId => {
    if (!requestId) return;
    try {
      const r = await fetch('/review/api/receipt/' + encodeURIComponent(requestId), {cache:'no-store'});
      if (!r.ok) throw Error('receipt unavailable');
      const receipt = await r.json();
      get('log').textContent += receipt.stored
        ? `Recording saved: capture ${receipt.id}; diagnostics ${receipt.diagnostics ? 'saved' : 'unavailable'}; review at /review\n`
        : 'Recording not saved. Stop testing and report this request ID for a storage check.\n';
    } catch { get('log').textContent += 'Recording status could not be confirmed. Stop testing and report this request ID.\n'; }
  };
})();
