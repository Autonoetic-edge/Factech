# AmFatec — brand application and design references

19 September 2026. Local preview only.

## Current application — study 04

The latest user-supplied Amfatec-main.zip provides a clean transparent mark at
`client/app/public/logo-256.png`. The preview now embeds that original asset,
preserved in [assets/amfatec-logo.png](assets/amfatec-logo.png), at 32–42 px with
live AmFatec wordmark text. This supersedes the screenshot crop described below.

The archive's warm paper palette, dark participant flow, and typography informed the design. The borrowed portrait treatment has been removed at the user's request. The workspace uses `#faf8f4`; the check
surface uses `#141412`, with ivory `#f4efe6` text and primary action. Brand red is
carried by the logo. Georgia italic provides restrained heading emphasis without
fetching fonts. An original SVG camera-framing diagram introduces preparation; the procedural guide demonstrates head turns. See [revision-four evidence](evidence/GUIDED_EXPERIENCE-v4-checks.md).

The remaining sections record the preceding study's source and decisions.

## Supplied identity

The product name is **AmFatec**, with that exact capitalization. The mark is the
red geometric circular logo supplied by the user. The original 394 × 220 PNG is
preserved byte-for-byte in [assets/amfatec-brand-reference.png](assets/amfatec-brand-reference.png).

The interface embeds that image and presents the mark through a 138 × 138 CSS
window starting at source coordinate (128, 18). The source pixels are not redrawn
or altered. The wordmark text is live HTML for legibility at small screen sizes.
This is a supplied raster reference, not a newly created vector brand asset.

Desktop uses a horizontal logo/name lockup in the workspace rail. Narrow layouts
move the lockup into the top header so the brand does not disappear with the rail.
The title and participant footer also use AmFatec. Internal repository/SDK names
are unchanged.

## References reviewed

These sources informed brand consistency and guidance patterns. No vendor UI,
artwork, policy, marketing metrics, or software was copied into the prototype.

| Reference | Relevant observation | Application in this design |
| --- | --- | --- |
| [Persona — inquiry template configuration](https://help.withpersona.com/articles/ETA0GIS8K60DSoiFRpA9z/) | Flow themes cover colors, typography, buttons, illustrations, and header assets. | Treat the logo, primary action, progress, and responsive header as one coherent brand application. |
| [Transmit Security — customize identity verification](https://developer.transmitsecurity.com/guides/verify/customize_experience) | Verification screens can apply a business logo and brand styling; recovery is part of the experience. | Preserve the supplied mark and existing recovery states while refining the visual theme. |
| [Veriff — identity verification](https://www.veriff.com/product/identity-verification) | The product emphasizes user guidance during verification. | Retain one current instruction and a matching head pose; do not add decisions during capture. |
| [Persona — integration methods](https://docs.withpersona.com/choosing-an-integration-method) | Embedded flows keep users inside the host app, with a completion callback. | Keep the participant card suitable for a host panel and preserve the explicit return-to-workspace boundary. |

The application decisions in the last column are design interpretations, not
claims that these companies use this layout or endorse AmFatec.

## Revised palette

- Brand/action red: `#c92035`; hover: `#aa182b`; soft tint: `#fbedef`.
- Canvas: `#f7f7f5`; surface: white; text: `#252529`.
- Illustration surface: `#f2f2f0`; the 3D head uses a neutral slate material.
- Success remains muted green. The illustration-status dot is gray, so it does
  not resemble an active recording indicator.
- Large instruction headings stay ink-colored. Red is reserved for selected
  progress, small identity accents, and the primary action.

## Revision-three verification

- JavaScript syntax check passed; branding changes did not alter the state machine.
- The supplied PNG and saved reference are byte-identical.
- No remaining “Facetech” text in the current preview HTML.
- Logo loaded correctly and was visually checked at the default browser-panel
  width, 390 × 844 mobile, and 1440 × 1000 desktop.
- No horizontal overflow at mobile or desktop check sizes.
- All 100 protected baseline file hashes still match.
- Existing revision-two interaction evidence remains separate. No production
  connection, deployment, or physical-device validation is implied.
