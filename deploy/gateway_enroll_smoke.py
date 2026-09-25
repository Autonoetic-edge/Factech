import base64
import hashlib
import json
import time
import urllib.error
import urllib.request

import msgpack

base = "http://127.0.0.1:8080"
def get(path):
    with urllib.request.urlopen(base+path) as r: return r.read()
before = json.loads(get("/capture-status"))["total"]
request = urllib.request.Request(base+"/v1/challenge", headers={"X-Facetech-Operation":"enroll","X-User-Id":"smoke-photo"})
with urllib.request.urlopen(request) as response: challenge = json.load(response)
jpeg = open("/tmp/image_F1.jpg", "rb").read()
scan = {"version":1,"device":{},"challenge":challenge,"frames":[{"jpeg_bytes":jpeg,"ts_ms":i*700} for i in range(12)]}
body = json.dumps({"user_id":"smoke-photo","facescan":base64.b64encode(msgpack.packb(scan,use_bin_type=True)).decode()}).encode()
time.sleep(8)
request = urllib.request.Request(base+"/v1/enroll",data=body,headers={"Content-Type":"application/json"})
try:
    response = urllib.request.urlopen(request,timeout=40)
except urllib.error.HTTPError as exc:
    response = exc
with response:
    result = json.load(response)
    assert response.status == 422 and "PAD spoof" in result["error"]["message"]
    assert "X-Facetech-Decision" not in response.headers
    request_id = response.headers["X-Request-Id"]
assert json.loads(get("/capture-status"))["total"] == before
page = get("/page.js")
sdk = get("/sdk/index.js")
assert b"HEAD_SEQUENCE" in sdk and b"Keep the phone still" in page
assert b"pad-sequence-v1" in get("/")
print(json.dumps({"request_id":request_id,"gateway_enrollment":result,"status":422,
                  "capture_count_unchanged":before,"sdk_sha256":hashlib.sha256(sdk).hexdigest()},indent=2))
