import json
from pathlib import Path

import msgpack
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response


def install_review(app, get_store):
    def store():
        value = get_store()
        if value is None or not value.evaluation_only:
            raise HTTPException(404, "Evaluation review is disabled")
        value.purge_expired()
        if value.last_purge_error:
            raise HTTPException(503, "Retention cleanup needs attention")
        return value

    def row(capture_id):
        value = store()
        with value._lock:
            cursor = value._conn.execute(
                "SELECT * FROM captures WHERE id=?", (capture_id,)
            )
            found = cursor.fetchone()
            if found is None:
                raise HTTPException(404, "Capture not found or expired")
            return dict(zip([c[0] for c in cursor.description], found))

    def scan_of(item):
        try:
            scan = msgpack.unpackb(
                item["scan"], raw=False, unicode_errors="surrogateescape"
            )
            if not isinstance(scan, dict) or not isinstance(scan.get("frames"), list):
                raise ValueError("No frame list")
            return scan
        except Exception:
            raise HTTPException(422, "Stored scan cannot be decoded") from None

    def private(data):
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/review")
    def review_page():
        store()
        return FileResponse(
            Path(__file__).parent / "review.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/review/api/captures")
    def captures(before: int = 2147483647):
        value = store()
        with value._lock:
            cursor = value._conn.execute(
                "SELECT id,captured_at,subject_id,endpoint,status_code,label,request_id,"
                "build_id,test_context,decision_json FROM captures WHERE id<? ORDER BY id DESC LIMIT 50",
                (before,),
            )
            items = [
                dict(zip([c[0] for c in cursor.description], r))
                for r in cursor.fetchall()
            ]
        for item in items:
            item["decision"] = json.loads(item.pop("decision_json") or "null")
            item["test_context"] = json.loads(item["test_context"] or "null")
        return private({"items": items, "retention_days": value.retention_days})

    @app.get("/review/api/receipt/{request_id}")
    def receipt(request_id: str):
        value = store()
        with value._lock:
            found = value._conn.execute(
                "SELECT id,decision_json IS NOT NULL FROM captures WHERE request_id=? ORDER BY id DESC LIMIT 1",
                (request_id,),
            ).fetchone()
        return private(
            {
                "stored": found is not None,
                "id": found[0] if found else None,
                "diagnostics": bool(found[1]) if found else False,
            }
        )

    @app.get("/review/api/captures/{capture_id}")
    def detail(capture_id: int):
        item = row(capture_id)
        scan = scan_of(item)
        item.pop("scan")
        item["decision"] = json.loads(item.pop("decision_json") or "null")
        item["test_context"] = json.loads(item["test_context"] or "null")
        item["device"] = scan.get("device")
        challenge = scan.get("challenge", {})
        item["challenge"] = {
            k: challenge[k] for k in ("action", "params") if k in challenge
        }
        item["frames"] = [
            {"index": i, "ts_ms": f.get("ts_ms"), "pose": f.get("pose")}
            for i, f in enumerate(scan["frames"])
            if isinstance(f, dict)
        ]
        return private(item)

    @app.get("/review/api/captures/{capture_id}/frames/{index}")
    def frame(capture_id: int, index: int):
        scan = scan_of(row(capture_id))
        if index < 0 or index >= len(scan["frames"]):
            raise HTTPException(404, "Frame not found")
        jpeg = scan["frames"][index].get("jpeg_bytes")
        if isinstance(jpeg, str):
            jpeg = jpeg.encode("utf-8", "surrogateescape")
        if not isinstance(jpeg, bytes) or not jpeg.startswith(b"\xff\xd8"):
            raise HTTPException(422, "Frame is not a JPEG")
        return Response(
            jpeg,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.delete("/review/api/captures/{capture_id}")
    def delete(capture_id: int, request: Request):
        if request.headers.get("X-Review-Action") != "delete":
            raise HTTPException(403, "Explicit review action required")
        value = store()
        row(capture_id)
        deleted = value._delete("id = ?", (capture_id,))
        return private({"deleted": deleted})
