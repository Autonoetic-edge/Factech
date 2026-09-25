"""Operator commands, run by hand inside the gateway image. Never run at service start.

python -m facetech_auth.admin migrate            create/upgrade both schemas
python -m facetech_auth.admin account SUB        bind an identity-provider user to a new participant

Reads the same AMFATEC_* environment as facetech_auth.serve. Prints no secret.
"""

import asyncio
import sys

import sqlalchemy as sa

from .serve import need

TENANT = "amfatec"


def url(database):
    return sa.URL.create(
        "postgresql+psycopg",
        username=need("AMFATEC_DB_USER"),
        password=need("AMFATEC_DB_PASSWORD"),
        host=need("AMFATEC_DB_HOST"),
        port=int(need("AMFATEC_DB_PORT")),
        database=need(database),
    )


def migrate():
    from .evaluation_store import metadata
    from .postgres import PostgresRepository
    from .postgres import migrate as upgrade

    upgrade(PostgresRepository(url("AMFATEC_DB_NAME")).engine)
    with PostgresRepository(url("AMFATEC_EVAL_DB_NAME")).engine.begin() as connection:
        connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS face_evaluation"))
        metadata.create_all(connection)
    print("migrated")


def account(sub):
    from .policy import Role
    from .postgres import PostgresRepository
    from .provision import Provisioner

    owner = PostgresRepository(url("AMFATEC_DB_NAME"))
    issuer = need("AMFATEC_OIDC_ISSUER")

    async def create():
        if await owner.actor_by_identity(issuer, sub) is not None:
            return print("already provisioned")
        await Provisioner(owner, administrator_id="amfatec-operator").create_account(
            issuer, sub, TENANT, {Role.PARTICIPANT}
        )
        print("provisioned")

    asyncio.run(create())


if __name__ == "__main__":
    if sys.argv[1:] == ["migrate"]:
        migrate()
    elif len(sys.argv) == 3 and sys.argv[1] == "account":
        account(sys.argv[2])
    else:
        raise SystemExit(__doc__)
