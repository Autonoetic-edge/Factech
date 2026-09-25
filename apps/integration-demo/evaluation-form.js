(() => {
  const get = id => document.getElementById(id);
  // The laptop phone server serves these same files with recording disabled
  // and a different engine. Always use the recorded evaluation deployment.
  const evaluationUrl = new URL(get('evaluationHome').href);
  if (location.origin !== evaluationUrl.origin) {
    get('storageNotice').textContent = 'Opening the recorded evaluation site. This address uses a different backend.';
    for (const id of ['enroll', 'verify', 'probe']) get(id).disabled = true;
    location.replace(evaluationUrl.href);
    return;
  }
  let status = null;
  let checking = null;
  const consentVersion = 'storage-consent-v1';
  const storageKey = 'facetech-tester-consent-v1';
  const personCode = () => get('testSubject').value.trim().toUpperCase();
  const validPerson = value => /^(?=[A-Z0-9-]*[0-9])[A-Z0-9][A-Z0-9-]{1,15}$/.test(value);
  let remembered = {grants: {}, subject: '', target: ''};
  let persistent = true;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved && typeof saved.grants === 'object' && saved.grants !== null && !Array.isArray(saved.grants)) {
      remembered.grants = Object.fromEntries(Object.entries(saved.grants)
        .filter(([subject, version]) => validPerson(subject) && version === consentVersion));
      if (validPerson(saved.subject) && typeof saved.target === 'string' && saved.target.length <= 128) {
        remembered.subject = saved.subject;
        remembered.target = saved.target;
        get('testSubject').value = saved.subject;
        get('userId').value = saved.target;
      }
    }
  } catch { persistent = false; }
  const persist = () => {
    remembered.subject = personCode();
    remembered.target = get('userId').value;
    try { localStorage.setItem(storageKey, JSON.stringify(remembered)); }
    catch { persistent = false; }
  };
  let selectedSubject = personCode();
  const consentState = () => {
    get('consentState').textContent = get('captureConsent').checked
      ? `Consent saved for ${selectedSubject}${persistent ? ' in this browser' : ' on this page only (browser storage unavailable)'}. It covers future attempts for this tester. Untick to withdraw and stop capture.`
      : 'Give consent once for this tester. A new tester needs their own consent.';
  };
  const selectSubject = () => {
    selectedSubject = personCode();
    get('captureConsent').checked = remembered.grants[selectedSubject] === consentVersion;
    persist();
    consentState();
  };
  // Restore only an explicit grant bound to this tester and consent version.
  get('captureConsent').checked = remembered.grants[selectedSubject] === consentVersion;
  get('captureConsent').addEventListener('change', () => {
    selectedSubject = personCode();
    if (get('captureConsent').checked && validPerson(selectedSubject)) remembered.grants[selectedSubject] = consentVersion;
    else { delete remembered.grants[selectedSubject]; get('captureConsent').checked = false; }
    persist();
    consentState();
  });
  get('testSubject').addEventListener('input', selectSubject);
  get('testSubject').addEventListener('change', selectSubject);
  get('userId').addEventListener('change', persist);
  window.addEventListener?.('storage', event => {
    if (event.key !== storageKey && event.key !== null) return;
    try {
      const saved = JSON.parse(event.newValue);
      remembered.grants = saved?.grants && typeof saved.grants === 'object' ? saved.grants : {};
    } catch { remembered.grants = {}; }
    const hadConsent = get('captureConsent').checked;
    get('captureConsent').checked = remembered.grants[personCode()] === consentVersion;
    consentState();
    if (hadConsent && !get('captureConsent').checked) get('captureConsent').dispatchEvent(new Event('change'));
  });
  consentState();
  get('makeTester').onclick = () => {
    if (get('enroll').disabled) return;
    const code = 'T0' + crypto.randomUUID().replaceAll('-', '').slice(0, 8).toUpperCase();
    get('testSubject').value = code;
    get('userId').value = 'r4-' + code.toLowerCase();
    selectSubject();
  };
  window.facetechResetConsent = () => {
    delete remembered.grants[personCode()];
    get('captureConsent').checked = false;
    persist();
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
    const diagnostic = {interface: 'ui-v6', origin: location.origin, checked_at: new Date().toISOString()};
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
    if (personCode() !== selectedSubject) selectSubject();
    if (!get('captureConsent').checked) throw Error('Select “Save attempts for this tester” to consent to recording before enrolling or verifying.');
    const subject = get('testSubject').value.trim().toUpperCase();
    if (!/^(?=[A-Z0-9-]*[0-9])[A-Z0-9][A-Z0-9-]{1,15}$/.test(subject)) throw Error('Enter a person code such as T01 before recording.');
    const testCase = get('testCase').value;
    const label = {self:'bona_fide',different_person:'bona_fide',print:'print',screen_photo:'screen_phone',screen_video:'screen_phone'}[testCase];
    const accessory = get('testGlasses')?.checked ? {accessory:'glasses'} : {};
    if (!label) throw Error('Choose a valid test case before recording.');
    persist();
    return {subject, label, case: testCase, lighting: get('testLighting').value, ...accessory};
  }
  window.facetechCaptureOptions = async () => {
    const before = captureMeta();
    if (!await refreshStatus()) throw Error(get('storageNotice').textContent);
    // Consent may be withdrawn while the network check is pending.
    const after = captureMeta();
    if (after.subject !== before.subject) throw Error('Tester changed during the recording check. Start again.');
    return {captureMeta: {consent: status.consent_version, ...after}};
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
