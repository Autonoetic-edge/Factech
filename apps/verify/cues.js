// Typed head-movement cues, derived from the SDK's own 'action' events.
//
// The SDK (packages/face-sdk/src/workflow/session.ts, frozen) emits exactly
// three sentences during a HEAD_SEQUENCE capture. This table maps those exact
// sentences to a pose. It is a lookup, not a parser: any other text maps to
// null and the page shows the text without moving the head guide.
// apps/tests/verify-cues.test.js pins these strings against the SDK source, so
// an SDK wording change fails a test instead of silently guessing a direction.

export const CUE_FORWARD = 'Face forward. Hold still.';
export const cueTurn = (direction) =>
  `Slowly turn your head a little to your ${direction}, then hold. Keep both eyes visible and the phone still.`;

const POSES = new Map([
  [CUE_FORWARD, 'forward'],
  [cueTurn('LEFT'), 'left'],
  [cueTurn('RIGHT'), 'right'],
]);

/** @returns {'forward'|'left'|'right'|null} */
export function poseOf(text) {
  return POSES.get(text) ?? null;
}

// Button order on the page. Left/right are the participant's own left/right;
// the preview is mirrored, so "left" is also screen-left.
export const POSE_ORDER = ['forward', 'left', 'right'];

// Single turn (engine CHALLENGE_POLICY=single-turn-v1): the SDK's 'instruction' event names
// the action, and that alone switches the page to the one-turn screen. HEAD_SEQUENCE keeps
// the sentence table above, so the page and the engine can never disagree.
// Sign convention (docs/SINGLE_TURN_PLAN.md 3.3): assumed LOOK_LEFT = the participant's own
// left. If a phone round shows it inverted, swap the two poses in this one table.
const ACTION_POSE = { LOOK_LEFT: 'left', LOOK_RIGHT: 'right' };

/** @returns {'left'|'right'|null} */
export function poseOfAction(action) {
  return ACTION_POSE[action] ?? null;
}

// Page yaw (framing.js yawOf) grows as the nose moves to image-right, which is the
// participant's left. The turn meter multiplies by this, so "toward" is always positive.
export const TURN_SIGN = { left: 1, right: -1 };
