"""Loopback-only M1 integration fixture; never a deployable inference service."""

import argparse
import base64
import json
import ssl
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / ".hardening-deps"),
    str(ROOT / "packages/face-auth/src"),
    str(ROOT / "tests-hardening"),
]


def application(side, analyzer="frozen"):
    import sqlalchemy as sa
    from facetech_auth.config import Config
    from facetech_auth.evaluation_store import EvaluationStore
    from facetech_auth.http import create_engine, create_gateway
    from facetech_auth.oidc import Provider
    from facetech_auth.operations import DataCipher, PostgresOperations
    from facetech_auth.postgres import PostgresRepository
    from facetech_auth.privacy import Recording
    from facetech_auth.sessions import Sessions, TokenVault
    from postgres_fixtures import SyntheticAnalyzer

    state = ROOT / ".hardening-runtime/state"
    values = json.loads((state / f"{side}-secrets.json").read_text("utf-8"))
    if values["marker"] != "facetech-m1-synthetic-only":
        raise ValueError("Synthetic fixture marker required")
    config = Config(
        "https://127.0.0.1:18443/realms/facetech-hardening",
        "facetech-bff",
        values["client_secret"],
        "facetech-engine",
        "https://127.0.0.1:18444",
        "https://127.0.0.1:18444/auth/callback",
        "https://127.0.0.1:18445",
        values["engine_key"],
    )
    url = sa.URL.create(
        "postgresql+psycopg",
        username="facetech_test",
        password=values["pg_password"],
        host="127.0.0.1",
        port=15432,
        database="facetech_m1",
    )
    repository = PostgresRepository(url)
    tls = ssl.create_default_context(cafile=str(state / "tls.crt"))
    sessions = Sessions(
        repository,
        Provider(config, tls_context=tls),
        TokenVault(
            {"synthetic-v1": base64.b64decode(values["token_key"])}, "synthetic-v1"
        ),
    )
    if side == "engine":
        cipher = DataCipher(base64.b64decode(values["data_key"]))
        if analyzer == "frozen":
            from facetech_auth.inference import FrozenAnalyzer

            chosen = FrozenAnalyzer(ROOT / "engine")
        else:
            chosen = SyntheticAnalyzer()
        return create_engine(
            sessions,
            operations=PostgresOperations(
                repository,
                cipher,
                analyzer=chosen,
                template_expires_at=int(time.time()) + 3600,
            ),
        )
    evaluation = EvaluationStore(
        PostgresRepository(url.set(database="facetech_eval")),
        DataCipher(base64.b64decode(values["evaluation_key"]), purpose="evaluation"),
    )
    return create_gateway(
        sessions,
        operations=PostgresOperations(
            repository,
            evaluation.cipher,
            evaluation=evaluation,
            recording=Recording(repository, evaluation, round_id="m3-synthetic-round"),
        ),
        engine_tls_context=tls,
    )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("side", choices=("gateway", "engine"))
    parser.add_argument("--synthetic-only", required=True, action="store_true")
    parser.add_argument("--analyzer", choices=("frozen", "synthetic"), default="frozen")
    args = parser.parse_args()
    state = ROOT / ".hardening-runtime/state"
    uvicorn.run(
        application(args.side, args.analyzer),
        host="127.0.0.1",
        port=18444 if args.side == "gateway" else 18445,
        ssl_certfile=str(state / "tls.crt"),
        ssl_keyfile=str(state / "tls.key"),
        access_log=False,
        log_level="warning",
        proxy_headers=False,
        limit_concurrency=20,
    )
