"""Initial isolated identity/session/audit and authorization state.

No legacy identity, template or capture import. Destructive downgrade is refused.
"""

from alembic import op
from facetech_auth.schema import install

revision = "0001_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    install(op.get_bind())


def downgrade():
    raise RuntimeError(
        "Destructive identity downgrade refused; restore an isolated reviewed backup"
    )
