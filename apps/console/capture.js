function createConsoleCapture(sdk, shared, S) {
  let camStream = null;
  const videoEl = document.createElement('video');
  videoEl.autoplay = true; videoEl.muted = true; videoEl.playsInline = true;

  async function openCamera() {
    if (S.cameraOff) throw new Error('camera-blocked');
    if (camStream && camStream.active) return camStream;
    const s = await navigator.mediaDevices.getUserMedia(
      { video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }, audio: false });

    if (camStream && camStream.active) { s.getTracks().forEach((t) => t.stop()); return camStream; }
    camStream = s;
    videoEl.srcObject = camStream;
    await videoEl.play();
    return camStream;
  }
  function mountVideo() {
    const holder = document.querySelector('.view');
    if (!holder || !camStream) return;
    if (videoEl.parentNode !== holder) holder.insertBefore(videoEl, holder.firstChild);
    if (videoEl.srcObject !== camStream) videoEl.srcObject = camStream;
    videoEl.play().catch(() => {});
  }
  function stopCamera() {
    if (camStream) { camStream.getTracks().forEach((t) => t.stop()); camStream = null; videoEl.srcObject = null; }
  }

  function jpegOf(cv, quality, longEdge) {
    if (Math.max(cv.width, cv.height) > longEdge) {
      const s = longEdge / Math.max(cv.width, cv.height);
      const c2 = document.createElement('canvas');
      c2.width = Math.max(2, Math.round(cv.width * s)); c2.height = Math.max(2, Math.round(cv.height * s));
      c2.getContext('2d').drawImage(cv, 0, 0, c2.width, c2.height);
      cv = c2;
    }
    return sdk.b64ToBytes(cv.toDataURL('image/jpeg', quality).split(',')[1]);
  }

  async function captureFrames(onFrame, shouldAbort) {
    const frames = [], tmp = document.createElement('canvas'), ctx = tmp.getContext('2d');
    const [q0, edge0] = sdk.JPEG_STEPS[0];
    for (let i = 0; i < sdk.FRAME_COUNT; i++) {
      if (shouldAbort && shouldAbort()) throw new Error('face-lost');
      const t0 = performance.now();
      const vw = videoEl.videoWidth || 640, vh = videoEl.videoHeight || 480;
      const scale = Math.min(1, sdk.LONG_EDGE / Math.max(vw, vh));
      tmp.width = Math.max(2, Math.round(vw * scale)); tmp.height = Math.max(2, Math.round(vh * scale));
      ctx.drawImage(videoEl, 0, 0, tmp.width, tmp.height);
      const cv = document.createElement('canvas');
      cv.width = tmp.width; cv.height = tmp.height;
      cv.getContext('2d').drawImage(tmp, 0, 0);
      const f = { canvas: cv, ts: performance.now() };
      frames.push(f);
      if (onFrame) onFrame(i + 1);

      try { f.jpeg0 = jpegOf(cv, q0, edge0); } catch (e) {                        }
      if (i === sdk.FRAME_COUNT - 1) break;
      const wait = sdk.CAPTURE_MS - (performance.now() - t0);
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    }
    return frames;
  }
  function deviceInfo() {
    return {
      user_agent: navigator.userAgent,
      screen: { w: Math.round(window.innerWidth), h: Math.round(window.innerHeight) },
      tz_offset: new Date().getTimezoneOffset(),
    };
  }

  function encodeFrames(frames, quality, longEdge, usePre) {
    return frames.map((f) => ((usePre && f.jpeg0) ? { bytes: f.jpeg0, ts: f.ts }
      : { bytes: jpegOf(f.canvas, quality, longEdge), ts: f.ts }));
  }

  function encodeScan(frames, challenge) {
    let enc = null;
    for (let i = 0; i < sdk.JPEG_STEPS.length; i++) {
      const [q, edge] = sdk.JPEG_STEPS[i];
      const scan = sdk.buildFaceScan(encodeFrames(frames, q, edge, i === 0), deviceInfo(),
        (crypto.randomUUID ? crypto.randomUUID() : 'ch-' + Date.now()), challenge);
      const bytes = sdk.mpEncode(scan);
      enc = { scan, bytes, b64: sdk.bytesToB64(bytes), size: bytes.length, quality: q, longEdge: edge,
        action: challenge && challenge.action };
      if (enc.size <= sdk.TARGET_BYTES) break;
    }
    return enc;
  }

  function releaseFrames(frames) {
    for (const f of frames || []) {
      if (f.canvas) { f.canvas.width = 0; f.canvas.height = 0; }
      f.canvas = null; f.jpeg0 = null;
    }
  }

  return {
    openCamera, stopCamera, mountVideo, captureFrames, encodeScan, releaseFrames,
    rawRequest: sdk.rawRequest, CHALLENGE_TIMEOUT_MS: sdk.CHALLENGE_TIMEOUT_MS, SCAN_TIMEOUT_MS: sdk.SCAN_TIMEOUT_MS,
    buildFaceScan: sdk.buildFaceScan, mpEncode: sdk.mpEncode, bytesToB64: sdk.bytesToB64,
    FRAME_COUNT: sdk.FRAME_COUNT, TARGET_BYTES: sdk.TARGET_BYTES, CAPTURE_MS: sdk.CAPTURE_MS,
    createRunController: sdk.createRunController, assertLive: sdk.assertLive,
    bindLifecycle: sdk.bindLifecycle, createGuide: sdk.createGuide, guideStep: sdk.guideStep,
    canonicalUserId: sdk.canonicalUserId, validUserId: sdk.isValidUserId,
    livenessVerdict: sdk.livenessVerdict, signalVerdict: shared.signalVerdict,
  };
}
