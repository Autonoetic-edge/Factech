"""Separate-process probe for D01/D03: reads and nonce claims from another PID.

Synthetic loopback cluster only; prints JSON to stdout. Never a service.
"""

import json
import secrets
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / ".hardening-deps"),
    str(ROOT / "packages/face-auth/src"),
    str(ROOT / "engine"),
    str(ROOT / "tests-hardening"),
]


def main():
    import sqlalchemy as sa
    from facetech_auth import schema as s
    from facetech_auth.operations import spend_nonce
    from facetech_auth.postgres import PostgresRepository
    from postgres_fixtures import local_url

    request = json.loads(sys.stdin.read())
    repo = PostgresRepository(local_url())
    if request["command"] == "fingerprint":
        from facetech_auth.inference import FrozenAnalyzer

        print(
            json.dumps(
                {"format_id": FrozenAnalyzer(ROOT / "engine", warm=False).format_id}
            )
        )
    elif request["command"] == "templates":
        with repo.engine.connect() as connection:
            rows = connection.execute(
                sa.select(s.templates.c.id, s.templates.c.model_fingerprint)
                .where(
                    s.templates.c.tenant == request["tenant"],
                    s.templates.c.subject_id == request["subject"],
                    s.templates.c.active.is_(True),
                )
                .order_by(s.templates.c.id)
            ).mappings()
            print(json.dumps({"templates": [dict(v) for v in rows]}))
    elif request["command"] == "claim":
        wins, errors, lock = [], [], threading.Lock()
        barrier = threading.Barrier(request["attempts"])

        def attempt():
            try:
                barrier.wait(10)
                with repo.engine.begin() as connection:
                    claimed = spend_nonce(
                        connection,
                        request["digest"],
                        request["binding"],
                        request["now_ms"],
                        secrets.token_hex(16),
                    )
                with lock:
                    wins.append(claimed is not None)
            except Exception as exc:  # reported, never hidden
                with lock:
                    errors.append(type(exc).__name__)

        threads = [threading.Thread(target=attempt) for _ in range(request["attempts"])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        print(json.dumps({"wins": sum(wins), "attempts": len(wins), "errors": errors}))
    repo.dispose()


if __name__ == "__main__":
    main()
