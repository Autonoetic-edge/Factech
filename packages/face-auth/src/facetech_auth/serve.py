"""Deployable entrypoint: `python -m facetech_auth.serve gateway|engine`.

Same construction as tools/hardening_synthetic_service.py, but every value comes
from the process environment (set by the orchestrator; no .env file is read here),
the analyzer is always the frozen pipeline, and the gateway is wrapped by the
browser panel. A missing or malformed value stops startup; nothing has a default
that could silently weaken a deployment.

Environment (names only; values are secrets or deployment facts):
  AMFATEC_ORIGIN, AMFATEC_ENGINE_ORIGIN          https origins, no path
  AMFATEC_OIDC_ISSUER, AMFATEC_OIDC_CLIENT_ID, AMFATEC_OIDC_CLIENT_SECRET, AMFATEC_OIDC_AUDIENCE
  AMFATEC_ENGINE_KEY                              >= 32 chars, shared gateway<->engine
  AMFATEC_ENGINE_KEY_PREVIOUS (optional)          the retiring key; the engine still
                                                  accepts it, the gateway never sends it
  AMFATEC_TOKEN_KEY_RETIRING_ID, AMFATEC_TOKEN_KEY_RETIRING (optional, pair)
                                                  the retiring token key: existing
                                                  sessions stay readable, new ones use
                                                  the active key
  AMFATEC_TRUSTED_PROXY (required, gateway)       comma-separated reverse-proxy peer
                                                  addresses whose X-Forwarded-For last
                                                  hop is the rate-limit source, or the
                                                  literal "direct" for no proxy in front
  AMFATEC_DB_HOST, AMFATEC_DB_PORT, AMFATEC_DB_USER, AMFATEC_DB_PASSWORD
  AMFATEC_DB_NAME, AMFATEC_EVAL_DB_NAME
  AMFATEC_TOKEN_KEY_ID, AMFATEC_TOKEN_KEY         base64, 32 bytes
  AMFATEC_DATA_KEY (engine), AMFATEC_EVALUATION_KEY (gateway)   base64, 32 bytes
  AMFATEC_TEMPLATE_EXPIRES_AT (engine)            unix seconds; templates end with the round
  AMFATEC_ROUND_ID (gateway)
  AMFATEC_TLS_CERT, AMFATEC_TLS_KEY, AMFATEC_CA_FILE   internal TLS; CA verifies engine + issuer
  AMFATEC_ENGINE_ROOT (engine)                    directory holding the frozen `app` package + models
  AMFATEC_PAGE_ROOT, AMFATEC_SDK_BUNDLE (gateway) apps/verify and packages/face-sdk/dist/index.js
  AMFATEC_BIND, AMFATEC_PORT
"""

import base64
import os
import ssl
import sys
import time


def need(name):
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")  # the name, never a value
    return value


def key(name):
    raw = base64.b64decode(need(name), validate=True)
    if len(raw) != 32:
        raise SystemExit(f"{name} must be 32 bytes, base64")
    return raw


def trusted_proxies():
    """The reverse-proxy peers whose last ``X-Forwarded-For`` hop is the
    rate-limit source. Gateway only, and required: behind a proxy every request
    carries the same transport peer, so a missing or wrong value collapses every
    per-source allowance into one bucket and one caller can lock out everyone.
    The literal "direct" declares no proxy in front. There is no default."""
    value = need("AMFATEC_TRUSTED_PROXY")
    if value == "direct":
        return ()
    peers = tuple(p.strip() for p in value.split(",") if p.strip())
    if not peers:
        raise SystemExit('AMFATEC_TRUSTED_PROXY must be addresses or "direct"')
    return peers


def optional_pair(id_name, key_name):
    """A rotation pair is present in full or absent; half a pair stops startup."""
    key_id = os.environ.get(id_name, "")
    if not key_id:
        if os.environ.get(key_name):
            raise SystemExit(f"{id_name} is required with {key_name}")
        return {}
    return {key_id: key(key_name)}


def application(side):
    import sqlalchemy as sa

    from .config import Config
    from .http import create_engine, create_gateway
    from .oidc import Provider
    from .operations import DataCipher, PostgresOperations
    from .postgres import PostgresRepository
    from .sessions import Sessions, TokenVault

    proxies = trusted_proxies() if side == "gateway" else ()
    origin = need("AMFATEC_ORIGIN")
    config = Config(
        need("AMFATEC_OIDC_ISSUER"),
        need("AMFATEC_OIDC_CLIENT_ID"),
        need("AMFATEC_OIDC_CLIENT_SECRET"),
        need("AMFATEC_OIDC_AUDIENCE"),
        origin,
        origin + "/auth/callback",
        need("AMFATEC_ENGINE_ORIGIN"),
        need("AMFATEC_ENGINE_KEY"),
        os.environ.get("AMFATEC_ENGINE_KEY_PREVIOUS", ""),
    )
    url = sa.URL.create(
        "postgresql+psycopg",
        username=need("AMFATEC_DB_USER"),
        password=need("AMFATEC_DB_PASSWORD"),
        host=need("AMFATEC_DB_HOST"),
        port=int(need("AMFATEC_DB_PORT")),
        database=need("AMFATEC_DB_NAME"),
    )
    repository = PostgresRepository(url)
    tls = ssl.create_default_context(cafile=need("AMFATEC_CA_FILE"))
    registration = None
    if side == "gateway" and os.environ.get("AMFATEC_SELF_REGISTRATION") == "true":
        from .registration import ParticipantRegistration

        registration = ParticipantRegistration(repository, tenant="amfatec")
    sessions = Sessions(
        repository,
        Provider(config, tls_context=tls),
        TokenVault(
            {
                **optional_pair(
                    "AMFATEC_TOKEN_KEY_RETIRING_ID", "AMFATEC_TOKEN_KEY_RETIRING"
                ),
                need("AMFATEC_TOKEN_KEY_ID"): key("AMFATEC_TOKEN_KEY"),
            },
            need("AMFATEC_TOKEN_KEY_ID"),
        ),
    )
    sessions.registration = registration
    if side == "engine":
        from .inference import FrozenAnalyzer

        expires = int(need("AMFATEC_TEMPLATE_EXPIRES_AT"))
        if expires <= time.time():
            raise SystemExit("AMFATEC_TEMPLATE_EXPIRES_AT is in the past")
        root = need("AMFATEC_ENGINE_ROOT")
        sys.path.insert(0, root)  # the frozen `app` package, imported unchanged
        return create_engine(
            sessions,
            operations=PostgresOperations(
                repository,
                DataCipher(key("AMFATEC_DATA_KEY")),
                analyzer=FrozenAnalyzer(root),
                template_expires_at=expires,
            ),
        )

    from .evaluation_store import EvaluationStore
    from .panel import Panel
    from .privacy import Recording

    evaluation = EvaluationStore(
        PostgresRepository(url.set(database=need("AMFATEC_EVAL_DB_NAME"))),
        DataCipher(key("AMFATEC_EVALUATION_KEY"), purpose="evaluation"),
    )
    gateway = create_gateway(
        sessions,
        operations=PostgresOperations(
            repository,
            evaluation.cipher,
            evaluation=evaluation,
            recording=Recording(
                repository, evaluation, round_id=need("AMFATEC_ROUND_ID")
            ),
        ),
        engine_tls_context=tls,
    )
    return Panel(
        gateway,
        repository=repository,
        origin=origin,
        page_root=need("AMFATEC_PAGE_ROOT"),
        sdk_bundle=need("AMFATEC_SDK_BUNDLE"),
        trusted_proxies=proxies,
    )


def main():
    import uvicorn

    if len(sys.argv) != 2 or sys.argv[1] not in {"gateway", "engine"}:
        raise SystemExit("usage: python -m facetech_auth.serve gateway|engine")
    uvicorn.run(
        application(sys.argv[1]),
        host=need("AMFATEC_BIND"),
        port=int(need("AMFATEC_PORT")),
        ssl_certfile=need("AMFATEC_TLS_CERT"),
        ssl_keyfile=need("AMFATEC_TLS_KEY"),
        access_log=False,
        log_level="warning",
        proxy_headers=False,
        server_header=False,
        limit_concurrency=20,
    )


if __name__ == "__main__":
    main()
