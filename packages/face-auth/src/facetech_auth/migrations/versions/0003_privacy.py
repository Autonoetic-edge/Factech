"""Consent history, recording reservations and bounded purge outbox.

Existing template ciphertext requires explicit owner-controlled envelope migration;
runtime decryption never guesses a legacy format. Existing expiry is conservatively
seven days from original creation, never seven days from migration.
"""

import sqlalchemy as sa
from alembic import op
from facetech_auth.schema import added, metadata

revision = "0003_privacy"
down_revision = "0002_durable_templates"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    tables, columns = added("0003")
    for column in columns:
        connection.execute(
            sa.text(
                f"ALTER TABLE {column.table.fullname} ADD COLUMN IF NOT EXISTS "
                f"{column.name} {column.type.compile(connection.dialect)}"
            )
        )
    metadata.create_all(connection, tables=tables, checkfirst=True)
    connection.execute(
        sa.text(
            "UPDATE face_auth.templates SET expires_at = created_at + 604800 "
            "WHERE expires_at IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO face_auth.consent_events "
            "SELECT tenant, subject_id, scope, revision, actor_id, 'self', "
            "text_version, granted, at FROM face_auth.consents ON CONFLICT DO NOTHING"
        )
    )


def downgrade():
    raise RuntimeError("Privacy downgrade refused; quarantine a reviewed restore")
