"""PostgreSQL application schema: identity, durable state and M3 privacy records.

Provider identity storage belongs to Keycloak's separate database. No legacy
capture table or model data is imported by this schema. Columns and tables tagged
``info={"revision": "0002"}`` are absent from a 0001 database; the migration
files create exactly their own shape from this single definition.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData(schema="face_auth")


def text(name, **kwargs):
    return sa.Column(name, sa.Text, **kwargs)


def integer(name, **kwargs):
    return sa.Column(name, sa.BigInteger, **kwargs)


R2 = {"revision": "0002"}
R3 = {"revision": "0003"}


actors = sa.Table(
    "actors",
    metadata,
    text("id", primary_key=True),
    text("issuer", nullable=False),
    text("sub", nullable=False),
    text("tenant", nullable=False),
    sa.Column("roles", JSONB, nullable=False),
    integer("epoch", nullable=False),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.UniqueConstraint("issuer", "sub"),
    sa.CheckConstraint("epoch >= 0"),
)
resources = sa.Table(
    "resources",
    metadata,
    text("tenant", primary_key=True),
    text("kind", primary_key=True),
    text("id", primary_key=True),
    text("owner_actor_id"),
    text("round_id"),
    integer("generation", nullable=False),
    text("data_ref"),
    sa.Column("active", sa.Boolean, nullable=False),
    integer("expires_at"),
    sa.CheckConstraint("kind IN ('subject','round','capture','receipt')"),
    sa.CheckConstraint("generation >= 0"),
)
sa.Index(
    "one_subject_per_owner",
    resources.c.tenant,
    resources.c.owner_actor_id,
    unique=True,
    postgresql_where=resources.c.kind == "subject",
)
sa.Index(
    "resource_listing",
    resources.c.tenant,
    resources.c.kind,
    resources.c.round_id,
    resources.c.id,
)

sessions = sa.Table(
    "sessions",
    metadata,
    text("id", primary_key=True),
    text("cookie_hash", nullable=False, unique=True),
    sa.Column(
        "actor_id", sa.Text, sa.ForeignKey("face_auth.actors.id"), nullable=False
    ),
    integer("epoch", nullable=False),
    text("issuer", nullable=False),
    text("sub", nullable=False),
    text("provider_sid", nullable=False),
    integer("authenticated_at", nullable=False),
    integer("created_at", nullable=False),
    integer("touched_at", nullable=False),
    integer("expires_at", nullable=False),
    text("csrf_hash", nullable=False),
    sa.Column("token_ciphertext", sa.LargeBinary, nullable=False),
    text("token_hash", nullable=False),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.CheckConstraint("expires_at > created_at"),
)
sa.Index(
    "provider_sessions", sessions.c.issuer, sessions.c.sub, sessions.c.provider_sid
)
logins = sa.Table(
    "logins",
    metadata,
    text("state_hash", primary_key=True),
    text("browser_hash", nullable=False),
    text("nonce", nullable=False),
    text("verifier", nullable=False),
    integer("expires_at", nullable=False),
)
grants = sa.Table(
    "grants",
    metadata,
    text("id", primary_key=True),
    sa.Column(
        "actor_id", sa.Text, sa.ForeignKey("face_auth.actors.id"), nullable=False
    ),
    text("tenant_id", nullable=False),
    text("round_id", nullable=False),
    sa.Column("scopes", JSONB, nullable=False),
    integer("expires_at", nullable=False),
    sa.Column("revoked", sa.Boolean, nullable=False),
)
outbox = sa.Table(
    "logout_outbox",
    metadata,
    sa.Column(
        "session_id", sa.Text, sa.ForeignKey("face_auth.sessions.id"), primary_key=True
    ),
    integer("attempts", nullable=False),
    integer("next_attempt", nullable=False),
    integer("lease_until", nullable=False),
)
replays = sa.Table(
    "logout_replays",
    metadata,
    text("issuer", primary_key=True),
    text("jti", primary_key=True),
    integer("expires_at", nullable=False),
)
revocations = sa.Table(
    "provider_revocations",
    metadata,
    text("id", primary_key=True),
    text("issuer", nullable=False),
    text("sub"),
    text("sid"),
    integer("expires_at", nullable=False),
)
audit = sa.Table(
    "audit",
    metadata,
    text("id", primary_key=True),
    text("request_id", nullable=False),
    text("actor_id"),
    text("action", nullable=False),
    text("resource_id"),
    text("outcome", nullable=False),
    integer("at", nullable=False),
)
challenges = sa.Table(
    "challenges",
    metadata,
    text("digest", primary_key=True),
    text("actor_id", nullable=False),
    text("session_id", nullable=False),
    text("tenant", nullable=False),
    text("subject_id", nullable=False),
    integer("generation", nullable=False),
    text("operation", nullable=False),
    sa.Column("parameters", JSONB, nullable=False),
    integer("issued_at", nullable=False),
    integer("expires_at", nullable=False),
    sa.Column("consumed", sa.Boolean, nullable=False),
    integer("consumed_at", info=R2),
    text("operation_id", info=R2),
)
templates = sa.Table(
    "templates",
    metadata,
    text("id", primary_key=True),
    text("tenant", nullable=False),
    text("subject_id", nullable=False),
    integer("created_at", nullable=False),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    text("format", nullable=False),
    # 0002: model-bound, owner-bound, key-versioned, explicit revocation time.
    text("owner_actor_id", nullable=False, info=R2),
    text("model_fingerprint", nullable=False, info=R2),
    text("key_version", nullable=False, info=R2),
    integer("revoked_at", info=R2),
    integer("expires_at", info=R3),
)
sa.Index(
    "active_templates",
    templates.c.tenant,
    templates.c.subject_id,
    postgresql_where=templates.c.active.is_(True),
)
operations = sa.Table(
    "operations",
    metadata,
    text("id", primary_key=True),
    text("tenant", nullable=False),
    text("actor_id", nullable=False),
    text("subject_id", nullable=False),
    text("key", nullable=False),
    text("operation", nullable=False),
    text("request_digest", nullable=False),
    text("state", nullable=False),
    integer("status"),
    sa.Column("result", JSONB),
    integer("created_at", nullable=False),
    integer("expires_at", nullable=False),
    sa.UniqueConstraint("actor_id", "key"),
    sa.CheckConstraint("state IN ('processing','completed','failed')"),
    info=R2,
)
deletion_ledger = sa.Table(
    "deletion_ledger",
    metadata,
    text("tenant", primary_key=True),
    text("kind", primary_key=True),
    text("id", primary_key=True),
    text("actor_id", nullable=False),
    integer("at", nullable=False),
    sa.CheckConstraint("kind IN ('template','capture')"),
    info=R2,
)
consents = sa.Table(
    "consents",
    metadata,
    text("tenant", primary_key=True),
    text("subject_id", primary_key=True),
    text("scope", primary_key=True),
    text("actor_id", nullable=False),
    text("text_version", nullable=False),
    sa.Column("granted", sa.Boolean, nullable=False),
    integer("revision", nullable=False),
    integer("at", nullable=False),
)
deletions = sa.Table(
    "deletion_requests",
    metadata,
    text("tenant", primary_key=True),
    text("capture_id", primary_key=True),
    text("actor_id", nullable=False),
    integer("at", nullable=False),
)


# No raw scans, device payloads or diagnostic bodies in the application database.
recordings = sa.Table(
    "recordings",
    metadata,
    text("tenant", primary_key=True),
    text("id", primary_key=True),
    text("subject_id", nullable=False),
    text("actor_id", nullable=False),
    text("round_id", nullable=False),
    integer("revision", nullable=False),
    text("state", nullable=False),
    integer("created_at", nullable=False),
    integer("expires_at", nullable=False),
    info=R3,
)
purge_jobs = sa.Table(
    "purge_jobs",
    metadata,
    text("tenant", primary_key=True),
    text("kind", primary_key=True),
    text("id", primary_key=True),
    integer("attempts", nullable=False),
    integer("next_attempt", nullable=False),
    integer("lease_until", nullable=False),
    text("state", nullable=False),
    text("last_error"),
    info=R3,
)
consent_events = sa.Table(
    "consent_events",
    metadata,
    text("tenant", primary_key=True),
    text("subject_id", primary_key=True),
    text("scope", primary_key=True),
    integer("revision", primary_key=True),
    text("actor_id", nullable=False),
    text("authority", nullable=False),
    text("text_version", nullable=False),
    sa.Column("granted", sa.Boolean, nullable=False),
    integer("at", nullable=False),
    info=R3,
)


def added(revision):
    """Tables and columns first introduced by a later revision."""
    tables = [t for t in metadata.sorted_tables if t.info.get("revision") == revision]
    columns = [
        c
        for t in metadata.sorted_tables
        if t.info.get("revision") != revision
        for c in t.columns
        if c.info.get("revision") == revision
    ]
    return tables, columns


def install(connection):
    """Only called by Alembic revision 0001 with an owner connection."""
    connection.execute(sa.text("CREATE SCHEMA face_auth"))
    tables2, columns2 = added("0002")
    tables3, columns3 = added("0003")
    tables, columns = tables2 + tables3, columns2 + columns3
    metadata.create_all(
        connection, tables=[t for t in metadata.sorted_tables if t not in tables]
    )
    for column in columns:  # revision 0001 never had these; keep its exact shape
        connection.execute(
            sa.text(f"ALTER TABLE {column.table.fullname} DROP COLUMN {column.name}")
        )
    connection.execute(
        sa.text("""
        CREATE FUNCTION face_auth.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Audit records are append only'; END $$
    """)
    )
    connection.execute(
        sa.text("""
        CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON face_auth.audit
        FOR EACH ROW EXECUTE FUNCTION face_auth.audit_immutable()
    """)
    )
