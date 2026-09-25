export const FRAME_COUNT = 12;

export const CAPTURE_MS = 500;

// HEAD_SEQUENCE captures at the engine's own cadence (engine/app/constants.py
// FRAME_INTERVAL_MS / CAPTURE_SPAN_MS; tests-contract/test_shared_constants.py).
export const HEAD_SEQUENCE_INTERVAL_MS = 1400;
export const HEAD_SEQUENCE_SPAN_MS = (FRAME_COUNT - 1) * HEAD_SEQUENCE_INTERVAL_MS;

export const LONG_EDGE = 480;

export const TARGET_BYTES = 330 * 1024;

export const ENGINE_MAX_BYTES = 350 * 1024;

export const LANDING_MS = 700;

export const JPEG_STEPS: readonly (readonly [quality: number, longEdge: number])[] = [
  [0.62, LONG_EDGE], [0.5, LONG_EDGE], [0.4, LONG_EDGE], [0.32, 360], [0.28, 288],
];

export const DEFAULT_SETTLE_MS = 1600;

export const DEFAULT_CHALLENGE_LIFE_MS = 30_000;

export const EXPIRY_MARGIN_MS = 1500;

export const READY_TIMEOUT_MS = 3000;

export const CHALLENGE_TIMEOUT_MS = 8000;
export const SCAN_TIMEOUT_MS = 20_000;

export const GUIDE_TICK_MS = 100;

export const FACE_LOST_MS = 1500;

export const CAMERA_READY_MS = 3000;
export const CAMERA_READY_POLL_MS = 50;

export const MAX_RETRY_AFTER_MS = 60_000;

export const CAMERA_CONSTRAINTS: MediaStreamConstraints = {
  video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
  audio: false,
};

// The page's pre-start hints (apps/verify/framing.js precheck). PROVISIONAL, set 24 Sep 2026
// and not yet measured on phones: mean luma of the face crop (0-255) and the Laplacian
// variance of the 64x64 face crop.
export const LUMA_MIN = 55;
export const SHARP_MIN = 12;
