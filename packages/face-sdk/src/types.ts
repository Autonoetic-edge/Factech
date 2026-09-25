export type Operation = 'enroll' | 'verify';

export const CHALLENGE_ACTIONS = ['MOVE_CLOSER', 'LOOK_LEFT', 'LOOK_RIGHT', 'BLINK_TWICE', 'HEAD_SEQUENCE'] as const;
export type ChallengeAction = (typeof CHALLENGE_ACTIONS)[number];

export interface Challenge {
  readonly nonce: string;
  readonly action: ChallengeAction;
  readonly params: Readonly<Record<string, number>> | null;

  readonly lifeMs: number;
}

export interface DeviceInfo {
  readonly user_agent: string;
  readonly screen: { readonly w: number; readonly h: number };
  readonly tz_offset: number;
}

export interface EncodedFrame { readonly bytes: Uint8Array; readonly ts: number }

export type FailureCode =
  | 'INVALID_USER_ID'
  | 'CAMERA_DENIED'
  | 'CAMERA_UNAVAILABLE'
  | 'CAMERA_ENDED'
  | 'CANCELLED'
  | 'FACE_LOST'
  | 'CHALLENGE_UNAVAILABLE'
  | 'CHALLENGE_EXPIRED'
  | 'SCAN_TOO_LARGE'
  | 'NETWORK'
  | 'TIMEOUT'
  | 'BAD_RESPONSE'
  | 'ENGINE_REJECTED';

export interface Failure {
  readonly ok: false;
  readonly op: Operation;
  readonly code: FailureCode;

  readonly message: string;

  readonly retryable: boolean;

  readonly countsAsAttempt: boolean;

  readonly engineCode: string | null;
  readonly httpStatus: number | null;

  readonly retryAfterMs: number | null;

  readonly requestId: string | null;

  readonly reason: string | null;
}

export type LivenessVerdict = 'passed' | 'failed' | 'not-enforced' | 'unknown';

interface SuccessBase {
  readonly ok: true;
  readonly userId: string;
  readonly liveness: LivenessVerdict;
  readonly requestId: string | null;

  readonly elapsedMs: number;
}
export interface EnrollSuccess extends SuccessBase {
  readonly op: 'enroll';
  readonly templateId: string;
  readonly quality: number | null;
}
export interface VerifySuccess extends SuccessBase {
  readonly op: 'verify';

  readonly match: boolean;
  readonly score: number;
  readonly threshold: number;
}
export type EnrollResult = EnrollSuccess | Failure;
export type VerifyResult = VerifySuccess | Failure;

export interface Instruction {
  readonly action: ChallengeAction;

  readonly settleMs: number;

  readonly target: number | null;

  readonly oval: { readonly from: number; readonly to: number };
}

export type CapturePhase = 'hold' | 'move' | 'landing';

export type SessionEvent =
  | { readonly type: 'action'; readonly text: string }
  | { readonly type: 'camera'; readonly state: 'opening' | 'ready' | 'stopped' }
  | { readonly type: 'instruction'; readonly instruction: Instruction }
  | { readonly type: 'phase'; readonly phase: CapturePhase }
  | { readonly type: 'frame'; readonly index: number; readonly total: number }

  | { readonly type: 'guide'; readonly phase: 'hold' | 'move' | 'done'; readonly progress: number | null }
  | { readonly type: 'uploading'; readonly bytes: number }
  | { readonly type: 'result'; readonly result: EnrollResult | VerifyResult };

export interface FaceProbe {

  readonly height: number | null;

  readonly lost: boolean;
}

export interface FaceSessionOptions {

  readonly gatewayUrl?: string;

  readonly videoElement: HTMLVideoElement;
  readonly onEvent?: (event: SessionEvent) => void;

  readonly onInstruction?: (instruction: Instruction) => void | Promise<void>;

  readonly faceProbe?: () => FaceProbe | null;

  readonly cancelOnHide?: boolean;

  readonly env?: Partial<Environment>;
}

export interface ScanOptions {

  readonly captureMeta?: Readonly<Record<string, string | number | boolean>> | null;
}

export interface FaceSession {
  enroll(userId: string, options?: ScanOptions): Promise<EnrollResult>;
  verify(userId: string, options?: ScanOptions): Promise<VerifyResult>;

  cancel(reason?: string): void;

  dispose(): void;
  readonly busy: boolean;
}

export interface Environment {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
  fetch: typeof fetch;
  getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>;
  createCanvas(): HTMLCanvasElement;
  deviceInfo(): DeviceInfo;
  randomId(): string;
  document: Pick<Document, 'addEventListener' | 'removeEventListener' | 'hidden'> | null;
  window: Pick<Window, 'addEventListener' | 'removeEventListener'> | null;
}

export interface CodedError extends Error {
  readonly sdkCode: FailureCode;
}
export function codedError(code: FailureCode, message: string): CodedError {
  const e = new Error(message) as Error & { sdkCode: FailureCode };
  e.name = 'FaceSdkError';
  e.sdkCode = code;
  return e;
}
export function isCodedError(e: unknown): e is CodedError {
  return e instanceof Error && typeof (e as { sdkCode?: unknown }).sdkCode === 'string';
}
