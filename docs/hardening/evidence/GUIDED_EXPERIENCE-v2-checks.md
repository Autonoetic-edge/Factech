# Guided experience revision 2 — local verification

Date: 19 September 2026.

Scope: `docs/hardening/guided-experience.html` and the accompanying design handoff.
The supplied wireframe screenshot prompted a second visual pass within revision 2:
the final guide uses a smooth matte-blue WebGL sculpture on a pale background.

## Source checks

- Node JavaScript syntax compilation passed.
- Final HTML at the checkpoint: 43,886 bytes; no external asset URLs.
- 25 unique static HTML IDs; no duplicate static IDs.
- No fetch, XMLHttpRequest, WebSocket, getUserMedia, localStorage or sessionStorage
  calls in the prototype script. CSP denies connect/media access.
- All 100 file hashes in `baseline/source-sha256.json` matched.
- No production app, SDK, gateway, model, policy, or VPS file was changed.
- No backend test suite was rerun or represented as new evidence.

## Browser checks

Used the Codex in-app browser with a dedicated loopback preview at
`http://127.0.0.1:8766/guided-experience.html`. The preview server serves only this
HTML document and binds to 127.0.0.1. It does not expose the repository directory.

Observed at desktop 1440 × 1000, mobile 390 × 844, and the default narrow browser
panel. Desktop and mobile DOM overflow checks found no horizontal overflow.

Verified through real preview interactions:

- First-time agreement → camera preparation → explicit start.
- Automatic progression from capture to sample success, without Next-cue clicks.
- Completion → Return to workspace reaches the prototype destination.
- Privacy dialog defaults recording off; Escape closes it and restores focus.
- Returning-user selection skips the first-time agreement screen.
- Camera-denied scenario displays its recovery instruction before capture.
- Mobile capture puts the illustration above its instruction and shows Stop.
- Stop before submission shows a stopped result with Start again.
- The revised solid head renders successfully with no shader/browser errors.
- Pause illustration changes to Play illustration.
- Revised-art capture advances through the final turn and processing.
- Uncertain result → Check existing result stays pending without a new scan.
- Simulate saved result found reaches the successful returning-check result.
- Scenario changes restart the selected journey; selectors are disabled during
  capture/processing.
- Browser error/warning log was empty at the final artwork checkpoint.

A locator wait aimed at the short-lived left-turn heading timed out; the next
inspection showed the third cue and then the processing state. This was not used
as evidence of physical direction correctness. Rendering and static sign mapping
were checked; physical-device mirroring remains unverified.

## Not claimed

Reduced-motion handling, WebGL-unavailable fallback, tab-background cleanup, and
the remaining error variants were source-inspected, not exhaustively exercised
across browsers. No physical phone, assistive-technology, camera permission,
capture timing, GPU performance, recognition, PAD, or live-backend validation was
performed. No conversion/abandonment improvement has been measured.

The design handoff identifies the existing SDK's missing separate prepare-camera
API and missing typed direction event. The demo timer, result selection, and
sample identity must be replaced by a real panel adapter before integration.
