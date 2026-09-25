"use strict";
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const ENCODING = path.resolve(__dirname, "..", "..", "packages", "face-sdk", "src", "encoding");
const load = (name) => import(pathToFileURL(path.join(ENCODING, name)).href);

(async () => {
  const [{ buildFaceScan }, { mpEncode, bytesToB64, b64ToBytes }] =
    await Promise.all([load("scan.ts"), load("msgpack.ts")]);
  const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const frames = input.frames.map(function (f) {
    return { bytes: b64ToBytes(f.jpeg_b64), ts: f.ts_ms };
  });
  const scan = buildFaceScan(frames, input.device, input.challenge_id);
  process.stdout.write(bytesToB64(mpEncode(scan)));
})().catch((e) => {
  console.error("SDK encoder failed: " + (e && e.stack || e));
  process.exit(2);
});
