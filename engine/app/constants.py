"""Shared engine numbers, stated once (FIX_PLAN 2A.1).

These restate the existing frozen values; changing one here changes it everywhere,
so a threshold change still needs its own approved row in HARDENING_PLAN.md.
packages/face-sdk/src/constants.ts carries the capture numbers the browser shares
(tests-contract/test_shared_constants.py keeps the two equal).
"""

# Capture: 12 frames, 1400 ms apart (HEAD_SEQUENCE), so an 11-interval span.
FRAME_COUNT = 12
FRAME_INTERVAL_MS = 1400
CAPTURE_SPAN_MS = (FRAME_COUNT - 1) * FRAME_INTERVAL_MS

# Wire limits for one FaceScan payload, raw and base64.
MAX_SCAN_BYTES = 350 * 1024
MAX_B64_CHARS = 4 * ((MAX_SCAN_BYTES + 2) // 3)

# PAD evidence gap limits: pad-sequence-v2-slow and pad-single-v1.
MAX_GAP_MS = 3800
SINGLE_MAX_GAP_MS = 1400

# Decision thresholds.
MATCH_THRESHOLD = 0.55
LIVENESS_THRESHOLD = 0.50

# Challenge nonce lifetime.
CHALLENGE_EXPIRY_MS = 30_000
