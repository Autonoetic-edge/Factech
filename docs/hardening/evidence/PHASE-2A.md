# Phase 2A evidence: shared constants, codes and flags

Branch `claude/fix-phase-2a`, 25 Sep 2026. FIX_PLAN.md items 2A.1-2A.4, under the
"Phase 2A" rows of HARDENING_PLAN.md's protected-file table (all approved by the owner,
25 Sep 2026).

## Manifest

`baseline/source-sha256.v6.json` is v5's 57-path list with fresh hashes for the 10
protected files this phase changed. Every other path, including the 8 gitignored
build/fetch artifacts, is carried forward from v5 unchanged. `source-sha256.v5.json` is
not modified and stays the ACTIVE manifest: making v6 active needs its own approved
"new manifest" row, as v5 had. No path was added or dropped.

The manifest was written by a script that copies v5 and rehashes only the tracked, changed
paths. `tools/hardening_verify_baseline.py --rebaseline` was not used, for two reasons.
It hashes every path, so in this checkout it would fail on the absent MediaPipe files and
`POLICY.md`. It would also record locally rebuilt PAD model hashes in place of v5's.

| Path | v5 (sha256) | v6 (sha256) | Row |
|---|---|---|---|
| `engine/app/anti_spoof.py` | `733d4fbcc4869062…` | `30763ef5e6e98ef6…` | anti_spoof.py (2A.1) |
| `engine/app/challenge.py` | `36b68eae772a842d…` | `159a9d15c7f53c62…` | challenge.py (2A.1), challenge.py (2A.4) |
| `engine/app/facescan.py` | `39fe3d7b238342e0…` | `dd9c3e1195424c06…` | facescan.py (2A.1) |
| `engine/app/head_sequence.py` | `39e39ef4288c6dc1…` | `a041894acc3ba315…` | head_sequence.py (2A.4) |
| `engine/app/liveness.py` | `19980524f5be4327…` | `fccd6b7de60e5970…` | liveness.py (2A.4) |
| `engine/app/main.py` | `4ee6485e29426ed8…` | `f87b4cde5935f3ce…` | main.py (2A.1), main.py (2A.4) |
| `packages/face-sdk/src/camera/camera.ts` | `9a189c2300fe79be…` | `a5ce892704adb21c…` | camera.ts (2A.2, conditional) |
| `packages/face-sdk/src/constants.ts` | `1d9fd4fc9177ab8a…` | `b7ed0b86da32f7ee…` | constants.ts (2A.2) |
| `packages/face-sdk/src/index.ts` | `d39ffa28190e1ddd…` | `9521e5ea896ef7f4…` | index.ts (2A.2, conditional), index.ts (2A.3) |
| `packages/face-sdk/src/workflow/session.ts` | `26f14b21f2e99661…` | `d3d45502acd9725d…` | session.ts (2A.2), session.ts (2A.3) |

Full hashes are in the two manifest files.

## Verification

`tools/hardening_verify_baseline.py` (ACTIVE = v5): 13 mismatched, 5 missing. 10 of the
mismatches are the files above. The other 3 are `engine/models/anti_spoof/{2.7_80x80_MiniFASNetV2.onnx,
4_0_0_80x80_MiniFASNetV1SE.onnx,manifest.json}`, gitignored and rebuilt locally by
`engine/scripts/convert_pad.py` (parity passed, max abs error 1.37e-6).

`--manifest …/source-sha256.v6.json`: 3 mismatched (the same 3 local model files),
5 missing: `apps/vendor/mediapipe/*` (4, fetch blocked by this sandbox's network
policy) and `engine/models/anti_spoof/POLICY.md` (gitignored, not produced by the
conversion). 0 tracked files mismatch.

## Rows that could not be applied

- `apps/shared/face-guide.js (2A.2)`: both allowed options (wire `SHARP_MIN`/`LUMA_MIN`/
  `LUMA_MAX` to the page values, or delete them) break the protected
  `apps/tests/face-guide.test.js` (lines 282 and 511 pin all three to `null`). Wiring
  would also change the guide's cues. Not changed.
- `apps/shared/messages.js (2A.3)`: the protected `apps/tests/face-guide.test.js` (line
  601) requires the file to import nothing and pins its `ENGINE_TEXT`/`SDK_TEXT` keys, so
  lookups into the SDK's `ERROR_CODES` cannot be made from it. Not changed. The contract
  test `tests-contract/test_sdk_error_codes.py` holds its text equal to the SDK's
  `ERROR_TEXT`.

Both need a row for `apps/tests/face-guide.test.js` before they can proceed.
