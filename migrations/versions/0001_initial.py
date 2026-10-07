"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=True),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("is_superadmin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("profile_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("telegram_user_id", name="uq_operators_telegram_user_id"),
    )
    op.create_index("ix_operators_telegram_user_id", "operators", ["telegram_user_id"])

    op.create_table(
        "profile_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("file_id", sa.String(length=512), nullable=False),
        sa.Column("caption_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            name="fk_profile_media_operator_id_operators",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("kind IN ('photo', 'video')", name="ck_profile_media_kind"),
    )
    op.create_index("ix_profile_media_operator_id", "profile_media", ["operator_id"])

    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=True),
        sa.Column("assigned_operator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_operator_id"],
            ["operators.id"],
            name="fk_clients_assigned_operator_id_operators",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("telegram_user_id", name="uq_clients_telegram_user_id"),
        sa.CheckConstraint(
            "state IN ('new', 'routed', 'in_dialog', 'closed')",
            name="ck_clients_state",
        ),
    )
    op.create_index("ix_clients_telegram_user_id", "clients", ["telegram_user_id"])
    op.create_index("ix_clients_assigned_operator_id", "clients", ["assigned_operator_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("media_file_id", sa.String(length=512), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_messages_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            name="fk_messages_operator_id_operators",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "client_id",
            "direction",
            "telegram_message_id",
            name="uq_messages_source",
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_messages_direction"),
    )
    op.create_index("ix_messages_client_id", "messages", ["client_id"])
    op.create_index("ix_messages_operator_id", "messages", ["operator_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("clients")
    op.drop_table("profile_media")
    op.drop_table("operators")
