"""Durable quarantine shared by all repository instances of a restored database."""

import sqlalchemy as sa
from alembic import op

revision = "0004_recovery_gate"
down_revision = "0003_privacy"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS face_auth.recovery_state (
            id text PRIMARY KEY CHECK (id = 'reads'),
            enabled boolean NOT NULL,
            checkpoint bigint NOT NULL
        )
    """)
    )
    connection.execute(
        sa.text("""
        INSERT INTO face_auth.recovery_state VALUES ('reads', true, 0)
        ON CONFLICT DO NOTHING
    """)
    )


def downgrade():
    raise RuntimeError("Recovery-gate downgrade refused")
