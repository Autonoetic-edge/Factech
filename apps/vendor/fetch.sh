#!/usr/bin/env bash

set -euo pipefail

VERSION="0.10.14"
JSDELIVR="https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VERSION}"
MODELS="https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1"

DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mediapipe"

FILES=(
  "vision_bundle.mjs|${JSDELIVR}/vision_bundle.mjs|136870|e77f281f9619150d937023c355bae170e9120e3b9e43f1e23a2a7bee07197669"
  "wasm/vision_wasm_internal.js|${JSDELIVR}/wasm/vision_wasm_internal.js|209826|9440cf0cc0cea21800e31581ec32aeedcc5fbf9df4509796bbc7d3f99e52ab9c"
  "wasm/vision_wasm_internal.wasm|${JSDELIVR}/wasm/vision_wasm_internal.wasm|9423986|f82a8e6c05e08a44cc9f9e7ec5f845935bcbb1b1500ebe8c2f4812fb4e2917dc"
  "blaze_face_short_range.tflite|${MODELS}/blaze_face_short_range.tflite|229746|b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f"
)

MODE="fetch"
case "${1:-}" in
  --verify)  MODE="verify" ;;
  --dry-run) MODE="dry-run" ;;
  "")        ;;
  *) echo "usage: $0 [--verify|--dry-run]" >&2; exit 2 ;;
esac

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

fail=0
for row in "${FILES[@]}"; do
  IFS='|' read -r rel url size sha <<<"$row"
  out="$DEST/$rel"

  if [ "$MODE" = "dry-run" ]; then
    printf '%-34s %10s bytes  %s\n' "$rel" "$size" "$url"
    continue
  fi

  if [ ! -f "$out" ]; then
    if [ "$MODE" = "verify" ]; then
      echo "MISSING  $rel — run without --verify to download" >&2
      fail=1; continue
    fi
    echo "fetching $rel ($size bytes)"
    mkdir -p "$(dirname "$out")"
    curl -fsSL --max-time 300 -o "$out.part" "$url"
    mv "$out.part" "$out"
  fi

  got_size=$(wc -c <"$out" | tr -d '[:space:]')
  got_sha=$(sha_of "$out")

  if [ "$sha" = "PIN_AFTER_FIRST_FETCH" ]; then
    echo "UNPINNED $rel  size=$got_size  sha256=$got_sha"
    echo "         ^ paste that sha256 into FILES[] above, then re-run --verify" >&2
    fail=1; continue
  fi
  if [ "$got_size" != "$size" ] || [ "$got_sha" != "$sha" ]; then
    echo "MISMATCH $rel" >&2
    echo "  expected  $size bytes  $sha" >&2
    echo "  got       $got_size bytes  $got_sha" >&2
    echo "  refusing to use it; delete the file and re-run to fetch again" >&2
    fail=1; continue
  fi
  echo "OK       $rel  ($got_size bytes)"
done

[ "$MODE" = "dry-run" ] && exit 0
if [ "$fail" -ne 0 ]; then
  echo "" >&2
  echo "vendor check FAILED — the guided page will run with no face guide" >&2
  exit 1
fi
echo ""
echo "vendor OK — MediaPipe tasks-vision ${VERSION} (Apache-2.0) in $DEST"
