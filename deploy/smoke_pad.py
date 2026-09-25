"""Run inside gateway. Synthetic no-consent scan; no persistence or image output."""
import base64
import json
import os
import time
import urllib.request

import msgpack

BASE = "http://127.0.0.1:8080"


def request(path, method="GET", body=None, headers=None, base=BASE):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base+path, data=data, method=method,
                                 headers={"Content-Type":"application/json", **(headers or {})})
    try:
        response = urllib.request.urlopen(req, timeout=40)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response), dict(response.headers)


def main():
    health = request("/health")
    assert health[0] == 200
    status_before = request("/capture-status")[1]
    headers = {"X-Facetech-Operation":"liveness"}
    code, challenge, _ = request("/v1/challenge", headers=headers)
    assert code == 200 and challenge["action"] == "HEAD_SEQUENCE"
    # Public upstream F1 JPEG has been copied only for smoke workload, not from captures.
    jpeg = open("/tmp/image_F1.jpg", "rb").read()
    scan = {"version":1,"device":{},"challenge":challenge,
            "frames":[{"jpeg_bytes":jpeg,"ts_ms":i*700} for i in range(12)]}
    packed = base64.b64encode(msgpack.packb(scan, use_bin_type=True)).decode()
    time.sleep(8)
    start = time.perf_counter()
    # Inspect diagnostics over the authenticated internal hop; do not expose keys.
    code, body, response_headers = request("/v1/liveness", "POST", {"facescan":packed},
        {"X-Engine-Key":os.environ["ENGINE_API_KEY"]}, os.environ["FACETECH_ENGINE_URL"])
    pad_status = code
    latency_ms = (time.perf_counter()-start)*1000
    diag = json.loads(next(v for k,v in response_headers.items() if k.lower()=="x-facetech-decision"))
    assert code == 422 and diag["pad"]["outcome"] == "spoof", (code,body,diag)
    assert diag["pad"]["usable"] >= 9
    replay = request("/v1/liveness", "POST", {"facescan":packed})
    assert replay[0] == 422 and "reused_nonce" in replay[1]["error"]["message"]
    assert not any(k.lower()=="x-facetech-decision" for k in replay[2])
    # A bound enrollment nonce cannot be submitted as liveness, and is spent.
    code, wrong, _ = request("/v1/challenge", headers={"X-Facetech-Operation":"enroll","X-User-Id":"smoke-photo"})
    scan["challenge"] = wrong
    packed = base64.b64encode(msgpack.packb(scan, use_bin_type=True)).decode()
    mismatch = request("/v1/liveness", "POST", {"facescan":packed})
    assert mismatch[0] == 422 and "binding_mismatch" in mismatch[1]["error"]["message"]
    status_after = request("/capture-status")[1]
    # No X-Capture-Meta consent was sent. Existing capture IDs are reviewed separately.
    print(json.dumps({"build":health[1]["buildId"],"pad_request_id":diag["request_id"],
                      "status":pad_status,"pad_rejection":body,"pad":diag["pad"],
                      "challenge":diag.get("head_sequence"),"latency_ms":round(latency_ms,2),
                      "replay_rejected":True,"binding_rejected":True,"diagnostics_stripped":True,
                      "capture_status_before":status_before,"capture_status_after":status_after}, indent=2))


if __name__ == "__main__":
    main()
