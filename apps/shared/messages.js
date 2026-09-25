export const CUE_TEXT = Object.freeze({
  none: 'We cannot see a face. Look straight at the screen.',
  notface: 'That does not look like a face. Show your whole face to the camera.',
  multi: 'Only one face, please. Make sure no one else is in view.',
  hidden: 'Someone else is in the camera view, just outside the picture. Make sure only you are in view.',
  far: 'Come a little closer.',
  close: 'Move back a little.',
  offcentre: 'Put your face inside the oval.',
  hold: 'Hold still.',
  shaky: 'Too shaky. Hold your phone with both hands, or rest it on something.',
  dark: 'More light, please. Turn toward a lamp or daylight.',
  bright: 'Too much light on your face. Move out of direct light.',
  good: 'Ready. Hold still.',
});

export const PHASE_TEXT = Object.freeze({
  hold: 'Hold still until the oval grows.',
  move: 'Now move closer slowly as the oval grows.',
  reached: 'Close enough. Hold there.',
  landing: 'Done. Checking...',
});

export const STOPPED_TEXT = Object.freeze({
  cancelled: 'Stopped.',
  hidden: 'Stopped because the page went to the background. Press enroll or verify to try again.',
});

export const MOVED_EARLY = 'moved-early';
export const MOVED_EARLY_TEXT = 'You moved too soon. Hold still until the oval grows.';

export const ENGINE_TEXT = Object.freeze({
  UNAUTHORIZED: 'The service is not set up correctly. Please tell the person running the test.',
  PAYLOAD_TOO_LARGE: 'The scan was too big to send. Please try again in better light.',
  MALFORMED_SCAN: 'The scan was damaged on the way. Please try again.',
  NO_FACE: 'We could not find your face. Face the camera in good light and try again.',
  MULTI_FACE: 'More than one face was seen. Make sure only you are in view and try again.',
  USER_NOT_FOUND: 'No one is enrolled under that name. Enroll first, or check the name.',
  MODEL_UNAVAILABLE: 'The service is not ready yet. Please try again later.',
  BUSY: 'The service is busy. Wait a few seconds and try again.',
  LOW_QUALITY: 'We did not get a clear enough view. Hold still at the start, in good light, and try again.',
  LIVENESS_FAIL: 'The check did not pass. Hold still until the oval grows, then move closer slowly.',
  CHALLENGE_FAIL: 'That attempt timed out or was already used. Please try again.',
  ENGINE_UNREACHABLE: 'The service cannot be reached at the moment. Please try again later.',
});

export const SDK_TEXT = Object.freeze({
  INVALID_USER_ID: 'Use only small letters, numbers, dots, dashes or underscores in the name.',
  CAMERA_DENIED: 'Camera access is blocked. Allow the camera in your browser settings and try again.',
  CAMERA_UNAVAILABLE: 'No camera could be opened. Close other apps that use it and try again.',
  CAMERA_ENDED: 'The camera stopped. Please try again.',
  CANCELLED: 'Stopped.',
  FACE_LOST: 'Your face went out of the oval. Keep it inside the oval until the scan ends.',
  CHALLENGE_UNAVAILABLE: 'Could not start the check. Check your connection and try again.',
  CHALLENGE_EXPIRED: 'That took too long. Please try again.',
  SCAN_TOO_LARGE: 'The scan was too big to send. Please try again in better light.',
  NETWORK: 'Connection problem. Check your internet and try again.',
  TIMEOUT: 'The service took too long to answer. Please try again.',
  BAD_RESPONSE: 'Something went wrong on our side. Please try again.',
  ENGINE_REJECTED: 'Something went wrong. Please try again.',
});

export const LIVENESS_SIGNAL_TEXT = Object.freeze({
  challenge: 'Hold still at the start, then come closer until your face fills the oval.',
  landmarks: 'Your face was hard to track. Keep your whole face in the oval and try again.',
  motion: 'The picture did not change the way a live face does. Try again in steady light.',
  duplicates: 'Some pictures came out the same. Keep the camera on your face and try again.',
  timing: 'The camera was too slow. Close other apps and try again.',
});

export const RESULT_TEXT = Object.freeze({
  verified: 'It is you. Verified.',
  noMatch: 'That face did not match.',
  matchNotLive: 'Your face matched, but the live-person check was not confirmed. You are not verified.',
  enrolled: 'You are enrolled.',
  enrolledNotLive: 'Your face was saved, but the live-person check was not confirmed.',
});

export function isAuthenticated(result) {
  return !!result && result.ok === true && result.op === 'verify'
    && result.match === true && result.liveness === 'passed';
}

export function resultTone(result) {
  if (!result || result.ok !== true) return 'alert';
  if (result.op === 'enroll') return result.liveness === 'passed' ? 'ok' : 'alert';
  return isAuthenticated(result) ? 'ok' : 'alert';
}

export function failedSignals(message) {
  const m = /(?:liveness check failed|not enough usable frames to check liveness): ([a-z_]+(?:, [a-z_]+)*)/
    .exec(String(message || ''));
  return m ? m[1].split(', ') : [];
}

export function messageFor(result) {
  if (!result) return SDK_TEXT.BAD_RESPONSE;
  if (result.ok) {
    if (result.op === 'enroll') {
      return result.liveness === 'passed' ? RESULT_TEXT.enrolled : RESULT_TEXT.enrolledNotLive;
    }
    if (result.op !== 'verify') return SDK_TEXT.BAD_RESPONSE;
    if (result.match !== true) return RESULT_TEXT.noMatch;
    return isAuthenticated(result) ? RESULT_TEXT.verified : RESULT_TEXT.matchNotLive;
  }
  if (result.code === 'CANCELLED' && result.reason === MOVED_EARLY) return MOVED_EARLY_TEXT;
  if (result.engineCode === 'LIVENESS_FAIL') {
    const worst = failedSignals(result.message).find((s) => Object.hasOwn(LIVENESS_SIGNAL_TEXT, s));
    if (worst) return LIVENESS_SIGNAL_TEXT[worst];
  }
  if (result.engineCode && Object.hasOwn(ENGINE_TEXT, result.engineCode)) {
    return ENGINE_TEXT[result.engineCode];
  }
  return Object.hasOwn(SDK_TEXT, result.code) ? SDK_TEXT[result.code] : SDK_TEXT.ENGINE_REJECTED;
}
