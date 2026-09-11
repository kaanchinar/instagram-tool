"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("id", BIGINT_PK, primary_key=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("following_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ok", "failed", name="snapshot_status"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_snapshots_taken_at", "snapshots", ["taken_at"])

    op.create_table(
        "snapshot_entries",
        sa.Column("id", BIGINT_PK, primary_key=True),
        sa.Column(
            "snapshot_id",
            BIGINT_PK,
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
        sa.Column("ig_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("follower", "following", name="direction"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "snapshot_id", "ig_user_id", "direction", name="uq_snapshot_entry"
        ),
    )
    op.create_index(
        "ix_snapshot_entries_snapshot_id", "snapshot_entries", ["snapshot_id"]
    )

    op.create_table(
        "people",
        sa.Column("ig_user_id", BIGINT_PK, primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column(
            "is_follower", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_following", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "whitelisted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "events",
        sa.Column("id", BIGINT_PK, primary_key=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ig_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "new_follower",
                "unfollowed",
                "i_followed",
                "i_unfollowed",
                name="event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            BIGINT_PK,
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
    )
    op.create_index("ix_events_detected_at", "events", ["detected_at"])
    op.create_index("ix_events_ig_user_id", "events", ["ig_user_id"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("people")
    op.drop_table("snapshot_entries")
    op.drop_table("snapshots")
    sa.Enum(name="event_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="direction").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="snapshot_status").drop(op.get_bind(), checkfirst=True)
