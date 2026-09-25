"""Local staging for the AmFatec page on the owned loopback stack (synthetic secrets only).

    tools/hardening_local_stack.py postgres ; ... keycloak      (start the owned stack first)
    tools/amfatec_stage.py prepare                              (migrate + provision the realm's users)
    tools/amfatec_stage.py engine                               (frozen pipeline, https://127.0.0.1:18445)
    tools/amfatec_stage.py gateway                              (page + gateway, https://127.0.0.1:18444)

Runs the DEPLOYABLE entrypoint (facetech_auth.serve), not the synthetic service, so
staging exercises the same wiring that ships. Secrets go from the state file into
this process's environment only; nothing is printed or written.
"""

import asyncio
import base64
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / ".hardening-deps"),
    str(ROOT / "packages/face-auth/src"),
    str(ROOT / "tests-hardening"),
]
STATE = ROOT / ".hardening-runtime/state"
ISSUER = "https://127.0.0.1:18443/realms/facetech-hardening"
PARTICIPANTS = ("alice", "bob", "carol")


def values():
    data = json.loads((STATE / "synthetic-secrets.json").read_text("utf-8"))
    if data["marker"] != "facetech-m1-synthetic-only":
        raise SystemExit("Synthetic fixture marker required")
    if "evaluation_key" not in data:
        data["evaluation_key"] = base64.b64encode(secrets.token_bytes(32)).decode()
        (STATE / "synthetic-secrets.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
    return data


def prepare():
    from facetech_auth.policy import Role
    from facetech_auth.provision import Provisioner
    from postgres_fixtures import prepare_databases

    owner, _ = prepare_databases()
    provisioner = Provisioner(owner, administrator_id="amfatec-staging-provisioner")
    users = values()["users"]

    async def accounts():
        for name in PARTICIPANTS:
            if await owner.actor_by_identity(ISSUER, users[name]["id"]) is None:
                await provisioner.create_account(
                    ISSUER, users[name]["id"], "amfatec-staging", {Role.PARTICIPANT}
                )
                print("provisioned", name)
            else:
                print("already provisioned", name)

    asyncio.run(accounts())


def serve(side):
    v = values()
    os.environ.update(
        AMFATEC_ORIGIN="https://127.0.0.1:18444",
        AMFATEC_ENGINE_ORIGIN="https://127.0.0.1:18445",
        AMFATEC_OIDC_ISSUER=ISSUER,
        AMFATEC_OIDC_CLIENT_ID="facetech-bff",
        AMFATEC_OIDC_CLIENT_SECRET=v["client_secret"],
        AMFATEC_OIDC_AUDIENCE="facetech-engine",
        AMFATEC_ENGINE_KEY=v["engine_key"],
        AMFATEC_DB_HOST="127.0.0.1",
        AMFATEC_DB_PORT="15432",
        AMFATEC_DB_USER="facetech_test",
        AMFATEC_DB_PASSWORD=v["pg_password"],
        AMFATEC_DB_NAME="facetech_m1",
        AMFATEC_EVAL_DB_NAME="facetech_eval",
        AMFATEC_TOKEN_KEY_ID="synthetic-v1",
        AMFATEC_TOKEN_KEY=v["token_key"],
        AMFATEC_TLS_CERT=str(STATE / "tls.crt"),
        AMFATEC_TLS_KEY=str(STATE / "tls.key"),
        AMFATEC_CA_FILE=str(STATE / "tls.crt"),
        AMFATEC_BIND="127.0.0.1",
        AMFATEC_PORT="18444" if side == "gateway" else "18445",
    )
    if side == "engine":
        os.environ.update(
            AMFATEC_DATA_KEY=v["data_key"],
            AMFATEC_TEMPLATE_EXPIRES_AT=str(int(time.time()) + 86400),
            AMFATEC_ENGINE_ROOT=str(ROOT / "engine"),
        )
    else:
        os.environ.update(
            AMFATEC_EVALUATION_KEY=v["evaluation_key"],
            AMFATEC_ROUND_ID="amfatec-staging-round",
            AMFATEC_PAGE_ROOT=str(ROOT / "apps/verify"),
            AMFATEC_SDK_BUNDLE=str(ROOT / "packages/face-sdk/dist/index.js"),
            # Required, no default. Staging is reached directly, so "direct";
            # set AMFATEC_TRUSTED_PROXY=127.0.0.1 before this command to stage a
            # simulated proxy hop instead.
            AMFATEC_TRUSTED_PROXY=os.environ.get("AMFATEC_TRUSTED_PROXY", "direct"),
        )
    from facetech_auth import serve as entry

    sys.argv = ["facetech_auth.serve", side]
    entry.main()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action == "prepare":
        prepare()
    elif action in {"gateway", "engine"}:
        serve(action)
    else:
        raise SystemExit(__doc__)
