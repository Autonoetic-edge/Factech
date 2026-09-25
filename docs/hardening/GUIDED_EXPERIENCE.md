# AmFatec guided participant experience — design study 04

19 September 2026. Interactive local prototype and integration handoff.

Open [guided-experience.html](guided-experience.html) directly in a browser. It is
a single, dependency-free HTML file containing its styles, interaction controller,
and embedded artwork with a procedural 3D movement guide. It does not request the camera, authenticate,
store data, contact services, or perform a face check. Every identity and result
is explicitly simulated. Its CSP blocks connections and media requests.

The request for this revision is a better UI/UX with fewer choices/clicks,
purposeful 3D animation, and a design suitable for integration with the VPS panel.
The previous documents were treated as design and system context, not as a new
request to implement their hardening milestones or deploy services.

## Design decisions

The interface uses the supplied AmFatec logo and name, a quiet workspace shell,
clear typography, a warm light workspace, and one dark verification surface with
an ivory primary action. The logo remains visible in the header on mobile. It avoids dashboard metrics,
decorative cards, gradients across the page, and navigation that has no destination.
The sidebar is workspace context; the progress indicators are informational.

| Previous preview | Revised experience |
| --- | --- |
| Five visible steps, including repeated sign-in/consent | Three stages: Prepare, Face check, Result |
| Separate checkbox and continue for required permission | Explicit “Agree & continue” with the purpose stated immediately above |
| Optional recording checkbox always on screen | Recording off by default; details and choice in a privacy dialog |
| Designer clicks to advance every cue | Automatic demonstration with a matching animated head pose |
| Competing primary/secondary navigation | One primary next action; Stop during capture |
| Preview scenario selectors always visible | Collapsed prototype controls below the participant experience |
| Result Done button restarts the walkthrough | Return to workspace leads to a clearly labeled integration destination |

The initial account is already signed in **for the demo**. This is not an
authentication shortcut in production. Returning checks assume current consent
has been returned by the server; they start at camera preparation.

### Default paths and interaction count

- First setup: **Agree & continue → Enable camera → Start face check**. After these
  three deliberate actions, cues and the sample result appear automatically.
- Returning check: **Enable camera → Start face check**. Two deliberate actions
  when identity and consent are already satisfied.
- After success: **Return to workspace**. The prototype shows a completion
  destination; the production host supplies its real route.

Browser permission prompts and any real authentication actions are additional.
No usability improvement or completion-time reduction has been measured yet.

## Visual and motion specification

| Element | Treatment |
| --- | --- |
| Canvas | Warm paper `#faf8f4` |
| Main surface | Charcoal `#141412`; quiet 1 px border; 14 px radius |
| Text | Workspace ink `#201d19`; verification text `#f4efe6`, supporting copy `#b5aea3` |
| Primary action | Ivory `#f4efe6` on the dark surface; minimum 50 px height; logo retains AmFatec red |
| Illustration | Original camera-framing line diagram before preparation; warm matte 3D head during movement guidance |
| Typography | Local Inter if present, then Segoe UI / Arial; Georgia italic for instruction emphasis; no font download |
| Hierarchy | Page title → current instruction → one supporting cue → next action |
| Content transition | 240 ms, 5 px entry; disabled for reduced motion |
| 3D render | Native WebGL smooth-normal sculpture; approximately 30 fps, DPR capped at 1.75 |

The introduction uses an original SVG positioning diagram with a face outline and framing marks. The portrait and point-cloud renderer from the supplied design have been removed. The static diagram makes camera preparation clear without suggesting a live feed. Enabling the simulated camera reveals the procedural 3D movement guide. Forward, Left, and Right buttons allow practice before starting; during capture they display automatic progress.

Only the supplied logo is embedded as a data URL. The former portrait asset is retained as historical source material, but is not loaded by the preview. No model, library, font, or CDN is fetched. Both illustrations are decorative; text conveys every instruction. The
illustrated left turn points to screen-left for the mirrored guide;
physical-device direction validation is still required.

Pause illustration stops decorative animation. Required pose changes remain
visible as static changes. `prefers-reduced-motion` starts with a static pose and
disables motion. Animation stops when the document is hidden. Demo capture is
cancelled on hide; after submission, hiding leads to uncertain-result recovery.

Demo timing is deliberately isolated in `DEMO_PHASES`: 4 seconds forward, 6 left,
6 right, followed by a 1.9-second simulated processing state. **These are only
walkthrough timings. Do not copy them into production capture policy.**

### Responsive behavior and accessibility

- Wide screens: workspace rail, two-column experience, persistent three-stage
  progress. Below 900 px the rail collapses to a narrow context strip.
- Below 680 px: single column, full-width primary action. Preparation keeps the
  action above the illustration. During capture the illustration moves above the
  instruction so the head and Stop action can be seen together.
- Semantic headings, text labels, native modal dialogs, visible keyboard focus,
  skip link, and `aria-current="step"` describe the flow.
- Capture changes use a polite live region. Focus on Stop survives an automatic
  cue update. A deliberate screen transition focuses its heading.
- No instruction relies on animation or color alone. The canvas is decorative
  to assistive technology; the equivalent instruction is visible text.

## Failure and recovery behavior

Open **Prototype controls** to select a journey and scenario. Changing either
restarts the demo so a scenario cannot change underneath a capture. Controls are
disabled during capture/processing; Restart remains a designer-only reset.

| Scenario | Behavior |
| --- | --- |
| Camera permission denied | Show browser-settings guidance; no capture starts. Change the demo scenario to Successful check to simulate restored permission. |
| Unclear capture | One clearer-view cue and Try again |
| Presence not confirmed | Neutral explanation, stillness/turn guidance, and Try again |
| No match | Explain the saved face did not match; never enroll as a fallback. First-setup mode maps this review scenario to unclear capture. |
| Busy before capture | No capture; remain at Prepare in progress. Production must honor Retry-After. |
| Sign-in expired | Simulated sign-in screen, then re-evaluate consent and camera |
| Stop before submission | Cancel scheduled work; camera off; Start again |
| Disconnect/leave after submission | Check existing result; no automatic new operation |
| Existing result still pending | Repeatable pending message, no invented success |
| Saved result found | Explicit designer control simulates recovery of success |
| Recording failed | Face-check success remains independent of recording failure; this scenario requires recording to be enabled |

The preview's result is never authoritative. “Camera ready” is also labeled as a
simulation and is not proof of framing, lighting, or permission.

## VPS panel integration handoff

This is a design-ready prototype, **not an installed production integration**.
The existing application/SDK and live VPS were not changed. The previous proposed
architecture and acceptance requirements remain independent of this design.

### Mounting and styling

Integrate the `.experience` region and the participant privacy/help dialogs into
the existing authenticated panel route. Reuse the panel's navigation/account shell;
the prototype's sidebar/topbar are replaceable context. Bind branding and color
tokens to the panel's design system. Keep reviewer/admin operational controls
outside this participant surface.

Extract the inline stylesheet/script into the panel's versioned static assets and
scope the CSS to the mounted experience (or a component boundary). The standalone
demo intentionally uses global CSS. Adopt the host's nonce/hash CSP rather than
carrying over this prototype's `unsafe-inline` setting. Remove the mock controller,
scenario controls, sample identity and all simulated outcomes from production.

Use an HTTPS route on the panel's own origin for real camera access and session
cookies. An embedded iframe would additionally require deliberate camera
permissions and a defined host/component message contract; neither is implemented.

### Adapter boundaries

| UI intent or state | Production owner / mapping |
| --- | --- |
| Account display / change account | Real session provider, trusted current actor, real account-switch flow |
| Required agreement | Server-versioned consent with purpose, retention, reviewers, withdrawal and deletion information presented before agreement |
| Recording choice | Separate consent, initially off unless a valid server policy explicitly says otherwise; stored independently of biometric decision |
| Enable camera / ready | Camera lifecycle owned by the integration; permission failure maps to the denied state |
| Start face check | One supported SDK operation for the authenticated subject; fresh challenge; prevent duplicate starts |
| Instruction text | SDK `onEvent` with `type: 'action'` supplies current challenge-based text |
| Head direction | Derived from the same authoritative instruction/phase source; validate mirrored participant-left/right on physical phones |
| Frame/capture state | SDK frame and phase events; never invent success percentages |
| Submission | SDK uploading/operation state; do not call this a completed or accepted result |
| Camera off | Only display after actual tracks have stopped; do not assume uploading means camera stopped |
| Stop / unmount | SDK `cancel(reason)` / `dispose()` plus adapter camera and timer cleanup |
| Successful result | Validated server decision and required acceptance predicates, not just SDK `ok` |
| Recording status | Independent receipt/storage state |
| Unknown result | Durable operation reference and authorized status lookup before allowing another enrollment |
| Return to workspace | Host routing callback, after authoritative result validation |

Source checked for this handoff: `packages/face-sdk/src/types.ts`, `src/index.ts`,
and `src/workflow/session.ts`. The current exports include `createFaceSession`,
`FaceSessionOptions.onEvent`, `onInstruction`, `cancel`, and `dispose`. Events
include camera, action, instruction, phase, frame, guide, uploading and result.

Two integration gaps deserve explicit engineering work:

1. The present SDK interface exposes enroll/verify/cancel/dispose; it does not
   expose a separate public prepare-camera method. Design a single-owner camera
   preparation adapter or supported SDK extension before implementing the
   two-action Enable camera → Start flow. Do not open competing camera streams.
2. HEAD_SEQUENCE directions are currently emitted as action text. There is no
   typed left/right event in the public type union. Add a versioned typed cue or
   use an explicit, tested mapping of the existing supported cues. Do not guess
   direction from a timer or use free-text parsing without a defined contract.

Keep existing `HEAD_SEQUENCE`, settle/switch parameters, server first direction,
12 frames at 1400 ms, challenge TTL, matching/PAD gates, and thresholds unchanged.
The SDK's challenge values, not `DEMO_PHASES`, must control production timing.
Do not add a center-return instruction during a required hold.

Durable outcome recovery and consent/privacy lifecycle need the proposed backend
work before these UI promises can be operational. Deployment and physical-device
validation are separate from this local design handoff.

## Verification and archived version

Revision-one artifacts are preserved as [previous HTML](evidence/guided-experience-v1.html)
and [previous design notes](evidence/GUIDED_EXPERIENCE-v1.md). Their verification
record describes the earlier revision only.

Earlier interaction verification is recorded in
[GUIDED_EXPERIENCE-v2-checks.md](evidence/GUIDED_EXPERIENCE-v2-checks.md).
The current branding references and historical revision-three checks are in
[AMFATEC_BRAND_REFERENCES.md](AMFATEC_BRAND_REFERENCES.md).
No backend milestone or biometric efficacy claim is made by this prototype.

Current visual and interaction checks: [design study 04 evidence](evidence/GUIDED_EXPERIENCE-v4-checks.md).
