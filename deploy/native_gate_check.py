"""Isolated process, public fixture, native PAD/recognizer; never live templates."""
import json
import time

import cv2

from app import challenge, main, store, trace
from app.detect import align_largest_face, detect_faces
from app.embed import cosine_similarity, embed_aligned

main.warm_models()
image = cv2.imread("/tmp/image_F1.jpg")
h, w = image.shape[:2]
image = cv2.resize(image, (round(w*480/max(h,w)), round(h*480/max(h,w))))
_, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 62])
jpeg = encoded.tobytes()
face = detect_faces(jpeg)[0]
embedding = embed_aligned(align_largest_face(jpeg, face))
store.enroll("synthetic-isolated", embedding)
rows = []
for operation in ("enroll", "verify", "liveness"):
    user = "" if operation == "liveness" else "synthetic-isolated"
    issued = challenge.issue(operation, user)
    # A deterministic server-time fixture; not a physical challenge completion.
    challenge._issued[issued["nonce"]]["issued_ms"] -= 8000
    scan = {"challenge": issued, "frames": [{"jpeg_bytes": jpeg, "ts_ms": i*700} for i in range(12)]}
    t, token = trace.start(operation, "native-test-" + operation)
    started = time.perf_counter()
    try:
        if operation == "enroll":
            response = main._enroll_scan(user, scan)
        elif operation == "verify":
            response = main._verify_scan(store.get_templates(user), scan, user)
        else:
            response = main._liveness_scan(scan)
        assert response.status_code == 422
        assert t.fields["pad"]["outcome"] == "spoof"
        assert "match" not in t.fields
        assert len(store.get_templates("synthetic-isolated")) == 1
        rows.append({"operation":operation,"status":response.status_code,
                     "pad":t.fields["pad"]["outcome"],"usable":t.fields["pad"]["usable"],
                     "potential_similarity_same_fixture":cosine_similarity(embedding, embedding),
                     "latency_ms":round((time.perf_counter()-started)*1000,2)})
    finally:
        trace.reset(token)
print(json.dumps({"isolated_native_gate_checks":rows,"templates_unchanged":True},indent=2))
