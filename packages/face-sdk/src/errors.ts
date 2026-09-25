// One error-code map for the SDK, the page and the contract tests (FIX_PLAN 2A.3).
//
// origin: who emits the code. 'engine' codes are contract §3.1 rows (engine/app/errors.py);
//   face-auth passes them through and re-emits PAYLOAD_TOO_LARGE, USER_NOT_FOUND and BUSY
//   itself with the same HTTP status. 'gateway' is the legacy §3.1 origin (no in-repo emitter
//   now). 'face-auth' codes come from packages/face-auth. 'sdk' codes are the SDK's own
//   FailureCode values, never sent by a server.
// http:   the status the emitter sends (null for sdk codes).
// retry:  the SDK's Failure.retryable for this code.
// attempt: the SDK's Failure.countsAsAttempt for this code.
// stage:  a coarse, UI-neutral category a page keys its own outcome and wording by.
// README.md §3.3 records the fields for the codes whose meaning used to disagree between
// copies. tests-contract/test_sdk_error_codes.py checks this map against the engine and
// face-auth emitters, contract §3.1 and the SDK's FailureCode union.

import type { FailureCode } from './types.ts';

export type ErrorOrigin = 'engine' | 'gateway' | 'face-auth' | 'sdk';

export type ErrorStage =
  | 'session' | 'reauth' | 'consent' | 'request' | 'conflict' | 'rate-limit' | 'registration'
  | 'capture' | 'liveness' | 'attempt' | 'enrolment' | 'enrolled' | 'capacity' | 'pending'
  | 'round' | 'service' | 'camera' | 'transport' | 'cancelled';

export interface ErrorCodeInfo {
  readonly origin: ErrorOrigin;
  readonly http: number | null;
  readonly retry: boolean;
  readonly attempt: boolean;
  readonly stage: ErrorStage;
}

const code = (
  origin: ErrorOrigin, http: number | null, stage: ErrorStage,
  { retry = false, attempt = false }: { retry?: boolean; attempt?: boolean } = {},
): ErrorCodeInfo => Object.freeze({ origin, http, retry, attempt, stage });

export const ERROR_CODES: Readonly<Record<string, ErrorCodeInfo>> = Object.freeze({
  // Contract §3.1, engine.
  UNAUTHORIZED: code('engine', 401, 'service'),
  PAYLOAD_TOO_LARGE: code('engine', 413, 'request'),
  MALFORMED_SCAN: code('engine', 422, 'request'),
  NO_FACE: code('engine', 422, 'capture', { retry: true, attempt: true }),
  MULTI_FACE: code('engine', 422, 'capture', { retry: true, attempt: true }),
  USER_NOT_FOUND: code('engine', 404, 'enrolment'),
  MODEL_UNAVAILABLE: code('engine', 503, 'service'),
  BUSY: code('engine', 503, 'capacity', { retry: true }),
  LOW_QUALITY: code('engine', 422, 'capture', { retry: true }),
  LIVENESS_FAIL: code('engine', 422, 'liveness', { retry: true, attempt: true }),
  CHALLENGE_FAIL: code('engine', 422, 'liveness', { retry: true, attempt: true }),
  // Contract §3.1, gateway (legacy origin).
  ENGINE_UNREACHABLE: code('gateway', 502, 'service', { retry: true }),

  // packages/face-auth.
  AUTHENTICATION_REQUIRED: code('face-auth', 401, 'session'),
  CSRF_REQUIRED: code('face-auth', 403, 'session'),
  RECENT_LOGIN_REQUIRED: code('face-auth', 403, 'reauth'),
  CONSENT_REQUIRED: code('face-auth', 403, 'consent'),
  CONSENT_CHANGED: code('face-auth', 409, 'consent'),
  CONSENT_VERSION_REQUIRED: code('face-auth', 400, 'consent'),
  NOT_FOUND: code('face-auth', 404, 'request'),
  MALFORMED_REQUEST: code('face-auth', 400, 'request'),
  UNSUPPORTED_MEDIA_TYPE: code('face-auth', 415, 'request'),
  UNTRUSTED_CONTEXT: code('face-auth', 400, 'request'),
  UNTRUSTED_ORIGIN: code('face-auth', 400, 'request'),
  IDEMPOTENCY_KEY_REQUIRED: code('face-auth', 400, 'request'),
  SELF_ESCALATION_FORBIDDEN: code('face-auth', 403, 'request'),
  RECORDING_DELETED: code('face-auth', 409, 'request'),
  IDEMPOTENCY_CONFLICT: code('face-auth', 409, 'conflict'),
  RESOURCE_CHANGED: code('face-auth', 409, 'conflict'),
  SUBJECT_REVOKED: code('face-auth', 409, 'conflict'),
  RATE_LIMITED: code('face-auth', 429, 'rate-limit'),
  REGISTRATION_CLOSED: code('face-auth', 403, 'registration'),
  CHALLENGE_INVALID: code('face-auth', 409, 'attempt'),
  OPERATION_EXPIRED: code('face-auth', 409, 'attempt'),
  OPERATION_INTERRUPTED: code('face-auth', 409, 'attempt'),
  OPERATION_IN_PROGRESS: code('face-auth', 409, 'pending'),
  TEMPLATE_EXPIRED: code('face-auth', 409, 'enrolment'),
  CAPACITY_EXCEEDED: code('face-auth', 409, 'enrolled'),
  ROUND_CLOSED: code('face-auth', 409, 'round'),
  ROUND_UNCONFIGURED: code('face-auth', 503, 'round'),
  DEPENDENCY_UNAVAILABLE: code('face-auth', 503, 'capacity'),
  REQUEST_TIMEOUT: code('face-auth', 408, 'transport'),
  MODEL_VERSION_MISMATCH: code('face-auth', 409, 'service'),
  RECOVERED: code('face-auth', 409, 'service'),

  // The SDK's own FailureCode values.
  INVALID_USER_ID: code('sdk', null, 'request'),
  CAMERA_DENIED: code('sdk', null, 'camera'),
  CAMERA_UNAVAILABLE: code('sdk', null, 'camera'),
  CAMERA_ENDED: code('sdk', null, 'camera', { retry: true }),
  CANCELLED: code('sdk', null, 'cancelled'),
  FACE_LOST: code('sdk', null, 'capture', { retry: true }),
  CHALLENGE_UNAVAILABLE: code('sdk', null, 'transport', { retry: true }),
  CHALLENGE_EXPIRED: code('sdk', null, 'attempt', { retry: true }),
  SCAN_TOO_LARGE: code('sdk', null, 'capture', { retry: true }),
  NETWORK: code('sdk', null, 'transport', { retry: true }),
  TIMEOUT: code('sdk', null, 'transport', { retry: true }),
  BAD_RESPONSE: code('sdk', null, 'service'),
  ENGINE_REJECTED: code('sdk', null, 'service'),
} satisfies Record<FailureCode, ErrorCodeInfo> & Record<string, ErrorCodeInfo>);

/** The code's entry when it is one a server sends (not an SDK FailureCode), else null. */
export function serverCode(c: string | null | undefined): ErrorCodeInfo | null {
  if (typeof c !== 'string' || !Object.hasOwn(ERROR_CODES, c)) return null;
  const info = ERROR_CODES[c]!;
  return info.origin === 'sdk' ? null : info;
}

/** The entry of one of the SDK's own FailureCode values. */
export function sdkCode(c: FailureCode): ErrorCodeInfo {
  return ERROR_CODES[c]!;
}

// Default user text: one plain sentence per engine and SDK code. apps/shared/messages.js
// still carries its own copy (ENGINE_TEXT / SDK_TEXT); the contract test keeps the two equal.
export const ERROR_TEXT: Readonly<Record<string, string>> = Object.freeze({
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
