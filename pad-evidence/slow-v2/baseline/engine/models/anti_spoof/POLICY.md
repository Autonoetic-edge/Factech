# Frozen evaluation policy: pad-sequence-v1

This is an initial RGB PAD candidate, not validated production protection.
The recognition threshold remains **0.55** and heuristic liveness remains **0.50**.
Depth and moire remain advisory. There is no heuristic-only authentication mode:
`LIVENESS_ENFORCE=0` does not disable any secure endpoint's required gates.

## Provenance and reproduction

Official repository: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
Pinned commit: `b6d5f04ad78778917853b25c778acef6d5626d15`.
`manifest.json` records exact checkpoint names, SHA-256 hashes, converted model
hashes and conversion dependency versions. The files are ordinary Git blobs
from the official repository, not third-party replacements. These are locally
computed hashes of the pinned Git content, not an upstream signed checksum list.
The repository supplies Apache-2.0; no separate checkpoint license was found.
Its full license is bundled in `LICENSE`. Crop arithmetic in `app/anti_spoof.py`
is adapted from upstream `src/generate_patches.py`, with explicit input validation.

Acquire without the Windows-incompatible saved_logs filename:

```
git clone --no-checkout https://github.com/minivision-ai/Silent-Face-Anti-Spoofing vendor/Silent-Face-Anti-Spoofing
git -C vendor/Silent-Face-Anti-Spoofing reset --mixed b6d5f04ad78778917853b25c778acef6d5626d15
git -C vendor/Silent-Face-Anti-Spoofing restore --source=b6d5f04ad78778917853b25c778acef6d5626d15 -- LICENSE README.md README_EN.md test.py requirements.txt src resources images
```

For an existing clone use `git restore --source=<pinned-commit> --` with the same
paths, and verify HEAD separately. Create a separate Python 3.12 virtual environment;
install `engine/requirements.txt`, `torch==2.6.0` from PyTorch's CPU wheel index,
`onnx==1.17.0` and Pillow. Run `python engine/scripts/convert_pad.py` from the project
root. The converter checks pinned HEAD and checkpoint hashes, exports static
1x3x80x80 opset-17 models, validates ONNX, and compares to the actual upstream
`AntiSpoofPredict.predict` on identical crops. Its only compatibility shim is
`collections.Iterable = collections.abc.Iterable` for the old transform library.
Runtime requires neither PyTorch, upstream sources, network access nor downloads.

Upstream evidence: `src/anti_spoof_predict.py`, `src/data_io/functional.py`
(`to_tensor`), `src/generate_patches.py`, `src/utility.py`, and `test.py` at the
pinned revision. Both models use **BGR float32 values 0..255**, with no division
by 255, mean subtraction, grayscale conversion or recognition alignment.
Convert SCRFD xyxy to integer xywh with inclusive width/height (+1), then apply
upstream scale limiting, edge shifting, inclusive crop and OpenCV bilinear resize.
The V2 checkpoint uses 2.7x context; V1SE uses 4.0x, both resized to 80x80.
Keep the existing InsightFace detector/recognizer. Its boxes differ from the
upstream demo's Caffe detector; this is an integration/domain difference to test.
The demo enforces portrait 3:4 source images; browser aspect ratios can differ.
No stretching of the full camera frame is added to force that demo constraint.

Class **1 = live**, classes **0 and 2 = attack**. The repository does not establish
their attack subtypes, so they are not called print/video. Upstream sums softmax
outputs and divides by two, then uses argmax. We reproduce this arithmetic mean.
Scores are not calibrated probabilities. There is no asserted universal safe cutoff.

## Local temporal policy (provisional, not an upstream default)

Exactly 12 frames, 700 ms intended cadence (~7.7 s), existing 350 KiB payload cap.
Existing predecode bounds: 1280px long edge, 1,000,000 pixels, aspect ratio <=3.
Decoder also validates dimensions before allocation. One inference scan runs at
a time; at most four additional scans queue. Each PAD model uses one CPU thread,
static input and a 2 s ORT run cancellation timer. PAD plus continuity has a 15 s
deadline checked before/after work; it never accepts a late result. Existing fixed
shape recognition calls are not force-killed mid-native-call. Container CPU/memory
limits provide another bound; an API/client timeout cannot cause authentication.

Every frame with exactly one usable face is evaluated, without ranking by PAD
score. Any multiple-face frame fails. Crop-invalid/missing faces are unavailable,
not live. Require >=9 usable frames, >=2 in each index third (0..3/4..7/8..11),
first usable index <=1, last >=10, and no >1600 ms gap between usable frames.
All usable frames must classify as live by ensemble argmax. Aggregate mean and
minimum live score are diagnostics; neither hides a failing frame.

Use the same detection for the PAD context crop and each recognition-aligned
embedding. Every usable frame is embedded; all pairwise cosine similarities must
be >=0.55 (a provisional continuity reuse of the existing identity threshold).
The normalized mean of these exact embeddings is the enrollment/verification
embedding. Missing, nonfinite or invalid embeddings fail. No best-three selection.

Outcomes: `live`, `spoof`, `insufficient_evidence`, `inference_error`. Only `live`
can continue, and a successful ordered challenge AND heuristic liveness are also
mandatory before template creation or identity matching. NaN/invalid softmax,
missing/corrupt models, exceptions and timeouts fail closed. Startup loads and
warms all required models; failure prevents readiness. Public health omits scores.

## Ordered head rotation

The server randomly selects left-then-right or right-then-left, with a random
switch cue at 4200/4300/4400 ms. Initial still/frontal phase lasts 1400 ms. The
browser gives timed instructions and records frames, but its guide verdict is
never trusted. The backend projects eye/nose/mouth landmarks onto the eye axis,
measuring two relative anatomical yaw proxies invariant to translation, uniform
scaling and image-plane roll. This supports head rotation evidence, not metric
3D yaw/depth. Both ratios must move >=0.20 in the requested direction relative
to the frontal baseline for two successive sampled frames in each action window.
The opposite action during a window fails. Allow 700 ms reaction after cues.
Require >=2 observations per phase, initial absolute proxies <=0.25, initial
spread <=0.15, scan span 7400..11000 ms and gaps 400..1100 ms.

These numbers are an unvalidated local challenge policy. Left means the subject's
left with an unmirrored camera capture. The visible preview may be mirrored;
frames must not be. Physical testing must verify cue interpretation.

Nonce: cryptographically random, expires after 30 s, bounded in-memory registry,
one use including rejected submissions, and bound to canonical target identity
plus enroll/verify/liveness operation. Identity/operation travel in headers at
issue time to avoid user IDs in query/access logs. Client timestamps cannot prove
capture origin; additionally reject spans longer than elapsed server issue time
(250 ms tolerance). Each issued nonce identifies one capture attempt/session.
Unknown-user verification returns 404 before inference, as before.

Head turning alone does not prevent video replay. Six possible order/timing
combinations are a weak challenge space; this is supplementary to learned PAD.
Perspective screen tilting, crafted video, browser injection and virtual cameras
remain possible. RGB/browser capture does not provide trusted sensor attestation.

## Data and evaluation

The engine has no database but is **not stateless**: it holds bounded templates
and nonces in memory. Restart loses enrollments. No persistent engine biometric
storage was added. Consented raw recording and diagnostics remain in the gateway
with existing seven-day retention and shared Basic Auth at the evaluation edge.
Review is protected at the edge, not by a new per-reviewer identity system.
No image transfer/backups from the VPS, relabeling, exporter enabling or capture
deletion is part of this change. Existing enrollment attack labels remain uncertain.

Screen photo/video stay separate in `test_context.case`; `label=screen_phone`
continues to identify the presentation device. Glasses use optional accessory
metadata. Use separate attack-enrollment IDs; the UI requires suffixes -photo and
-video when these consented test cases are selected. This UI guard is for test
hygiene, not authentication security. Freeze this policy for round 1. Any tuning
requires a new policy version and fresh, independent attempts.
