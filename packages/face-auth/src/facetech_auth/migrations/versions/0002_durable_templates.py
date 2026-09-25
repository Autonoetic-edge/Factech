"""Durable model-bound templates, idempotent operations and deletion ledger.

Compatible expansion: adds nullable columns, backfills from existing synthetic
rows, then tightens them. Re-runnable on a database that already has them.
Destructive downgrade is refused.
"""

import sqlalchemy as sa
from alembic import op
from facetech_auth.schema import added, metadata

revision = "0002_durable_templates"
down_revision = "0001_identity"
branch_labels = None
depends_on = None

BACKFILL = (
    """UPDATE face_auth.templates t SET owner_actor_id = r.owner_actor_id
       FROM face_auth.resources r
       WHERE r.tenant = t.tenant AND r.kind = 'subject' AND r.id = t.subject_id
         AND t.owner_actor_id IS NULL""",
    "UPDATE face_auth.templates SET model_fingerprint = format"
    " WHERE model_fingerprint IS NULL",
    "UPDATE face_auth.templates SET key_version = 'v1' WHERE key_version IS NULL",
    "UPDATE face_auth.templates SET revoked_at = created_at"
    " WHERE active = false AND revoked_at IS NULL",
    """INSERT INTO face_auth.deletion_ledger (tenant, kind, id, actor_id, at)
       SELECT tenant, 'capture', capture_id, actor_id, at
       FROM face_auth.deletion_requests ON CONFLICT DO NOTHING""",
)


def upgrade():
    connection = op.get_bind()
    tables, columns = added("0002")
    for column in columns:
        connection.execute(
            sa.text(
                f"ALTER TABLE {column.table.fullname} ADD COLUMN IF NOT EXISTS "
                f"{column.name} {column.type.compile(connection.dialect)}"
            )
        )
    metadata.create_all(connection, tables=tables, checkfirst=True)
    for statement in BACKFILL:
        connection.execute(sa.text(statement))
    for column in columns:
        if not column.nullable:
            connection.execute(
                sa.text(
                    f"ALTER TABLE {column.table.fullname} ALTER COLUMN "
                    f"{column.name} SET NOT NULL"
                )
            )


def downgrade():
    raise RuntimeError(
        "Destructive template/operation downgrade refused; restore a reviewed backup"
    )
