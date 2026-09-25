export const FRAME_COUNT = 12;

export const CAPTURE_MS = 500;

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
