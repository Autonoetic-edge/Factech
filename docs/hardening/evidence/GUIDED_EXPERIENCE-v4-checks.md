# Design study 04 — ZIP-inspired participant experience

19 September 2026. Local design prototype; not connected to the VPS.

## Source and design changes

Reference: the user-supplied `C:\Users\hp\Downloads\Amfatec-main.zip`.
Reviewed the archive's World.tsx, world.css, scene.ts, endScene.ts, Flow.tsx,
flow.css, globals.css, Console.tsx, and brand assets as design context.

- Warm paper workspace and a single dark verification surface.
- Restrained sans-serif headings with Georgia italic emphasis, ivory actions,
  hairline progress, warm neutral sculpture lighting.
- The original archive logo (`client/app/public/logo-256.png`) replaces the
  earlier screenshot crop. Saved as `assets/amfatec-logo.png`.
- The archive portrait (`client/app/public/cloud-face.png`) is saved unchanged
  as `assets/amfatec-portrait.png` and rendered as decorative WebGL points.
- A short point-gather introduction transitions to the directional 3D guide
  when the simulated camera is enabled. No interactive dragging or extra choices.
- Previous preview preserved at `evidence/guided-experience-v3.html`.

No archive marketing claims, capture counts, timings, consent promises, or
backend behavior were imported. Its application was read, not run. The preview
retains its explicit simulation labels and existing state machine.

## Verification

- Inline JavaScript parses successfully with Node.
- Visually checked the desktop composition at 1440 × 1000 and mobile at
  390 × 844, plus the app's default browser-panel width.
- Clean AmFatec logo and illustrated portrait render; transition to the matte
  guide observed after enabling the simulated camera.
- Mobile capture presents the guide above instructions, with Stop visible.
- Stop produces the cancelled result. Privacy dialog opens with recording off;
  Escape closes it and returns focus to the privacy button.
- Browser automation had one transient click timeout after closing the dialog;
  fresh accessibility state showed the unchanged entry screen, and the next
  accessibility action succeeded.
- The automatic capture sequence reached the successful sample result on mobile;
  recording remained off. No horizontal overflow at 390 px, and both logo
  elements reported their correct 256 px source size.
- Browser warning/error logs were empty during the verified run.
- Return to workspace reaches the simulated panel handoff. The portrait pause
  control changes to Play illustration. Desktop overflow check also passed.
- Final particle sampling uses 16,000 points distributed by image contrast;
  both portrait and logo are embedded, without runtime asset requests.
- All 100 protected source hashes matched the baseline; no VPS, SDK, backend,
  authentication, or capture-policy code changed.

See the handoff document for production requirements. Physical devices,
screen-reader output, operating-system reduced-motion settings, and GPU fallback
were not exercised in this revision. The fallback and reduced-motion code paths
are implemented; this is not evidence of device-level validation.
