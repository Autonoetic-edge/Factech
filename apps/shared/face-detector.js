import {
  RECORD_MAX_H, SHARP_SIZE, START_MAX_H, TICK_HZ, createFaceGuide, createHealth, grayOf,
  guideTick, healthStep, laplacianVariance, meanLuma, sharpCrop, visible,
} from './face-guide.js';

export const VENDOR_URL = '/vendor/mediapipe/';

let loading = null;

export function loadDetector(vendorUrl = VENDOR_URL) {
  if (loading) return loading;
  loading = (async () => {
    const mod = await import(vendorUrl + 'vision_bundle.mjs');
    const fileset = await mod.FilesetResolver.forVisionTasks(vendorUrl + 'wasm');

    const opts = {
      baseOptions: { modelAssetPath: vendorUrl + 'blaze_face_short_range.tflite', delegate: 'GPU' },
      runningMode: 'VIDEO', minDetectionConfidence: 0.5,
    };
    try { return await mod.FaceDetector.createFromOptions(fileset, opts); }
    catch (e) {
      opts.baseOptions.delegate = 'CPU';
      return await mod.FaceDetector.createFromOptions(fileset, opts);
    }
  })();
  loading.catch(() => { loading = null; });
  return loading;
}

export function createFaceDetector({ video, view, onTick, onUnavailable, vendorUrl = VENDOR_URL }) {
  const guide = createFaceGuide();
  const health = createHealth();
  let det = null, raf = 0, last = 0, lastTs = 0, maxH = START_MAX_H, gaveUp = false;

  let cv = null, ctx = null;
  function measureCrop(dets, frame) {
    const none = { sharp: null, luma: null };
    try {
      const seen = visible(dets, frame);
      if (seen.length !== 1) return none;
      const c = sharpCrop(seen[0], frame);
      if (!c) return none;
      if (!ctx) {
        cv = document.createElement('canvas');
        cv.width = cv.height = SHARP_SIZE;
        ctx = cv.getContext('2d', { willReadFrequently: true });
      }
      ctx.drawImage(video, c.sx, c.sy, c.sw, c.sh, 0, 0, SHARP_SIZE, SHARP_SIZE);
      const px = ctx.getImageData(0, 0, SHARP_SIZE, SHARP_SIZE).data;
      return {
        sharp: laplacianVariance(grayOf(px, SHARP_SIZE * SHARP_SIZE), SHARP_SIZE, SHARP_SIZE),
        luma: meanLuma(px, SHARP_SIZE, SHARP_SIZE, null),
      };
    } catch (e) {
      return none;
    }
  }

  const giveUp = (reason) => {
    if (gaveUp) return;
    gaveUp = true; health.available = false; stop();
    if (onUnavailable) onUnavailable(reason);
  };

  function step() {
    raf = requestAnimationFrame(step);
    if (!det || !video.videoWidth || video.readyState < 2) return;
    const now = performance.now();
    if (now - last < 1000 / TICK_HZ) return;
    last = now;
    const box = view.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const frame = { videoW: video.videoWidth, videoH: video.videoHeight, viewW: box.width, viewH: box.height, maxH };
    let dets;
    try {

      const ts = Math.max(now, lastTs + 1); lastTs = ts;
      const res = det.detectForVideo(video, ts);
      dets = (res && res.detections) || [];
      healthStep(health, true);
    } catch (e) {
      healthStep(health, false);
      if (!health.available) giveUp('detector failed ' + health.errors + ' times in a row');
      return;
    }
    guideTick(guide, dets, frame, now, measureCrop(dets, frame));
    if (onTick) onTick(guide, dets, frame);
  }

  function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; }

  return {
    guide,
    get available() { return health.available && !gaveUp; },

    async start() {
      if (gaveUp) return false;
      if (!det) {
        try { det = await loadDetector(vendorUrl); }
        catch (e) { giveUp('detector did not load: ' + ((e && e.message) || e)); return false; }
      }
      if (!raf) raf = requestAnimationFrame(step);
      return true;
    },
    stop,

    setMode(mode) { maxH = mode === 'record' ? RECORD_MAX_H : START_MAX_H; },
    reset() { Object.assign(guide, createFaceGuide()); },
  };
}
