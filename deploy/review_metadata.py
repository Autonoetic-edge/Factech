"""Read only review metadata and scores inside the gateway; never frame bytes."""
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/review/api/captures") as response:
    data = json.load(response)
items = []
for item in data["items"]:
    decision = item.get("decision") or {}
    items.append({"id": item["id"], "request_id": item["request_id"], "build_id": item["build_id"],
                  "captured_at": item["captured_at"], "subject_id": item["subject_id"],
                  "endpoint": item["endpoint"], "label": item["label"], "case": item["test_context"],
                  "decision": decision})
print(json.dumps({"retention_days": data["retention_days"], "items": items}, indent=2))
