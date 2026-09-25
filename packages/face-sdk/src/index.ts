export { createFaceSession } from './workflow/session.ts';

export type {
  Challenge, ChallengeAction, CapturePhase, DeviceInfo, EnrollResult, EnrollSuccess,
  Environment, Failure, FailureCode, FaceProbe, FaceSession, FaceSessionOptions,
  Instruction, LivenessVerdict, Operation, ScanOptions, SessionEvent, VerifyResult,
  VerifySuccess,
} from './types.ts';
export { CHALLENGE_ACTIONS } from './types.ts';

export { CAPTURE_MS, FRAME_COUNT, LANDING_MS } from './constants.ts';
export { CAMERA_CONSTRAINTS, LUMA_MIN, SHARP_MIN } from './constants.ts';
export { OVAL_START } from './challenge/guide.ts';
