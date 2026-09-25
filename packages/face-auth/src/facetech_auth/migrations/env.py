"""Migrations require an explicitly supplied connection; never read a DSN/env."""

from alembic import context
from facetech_auth.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Migration requires an explicit isolated owner connection")
context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
