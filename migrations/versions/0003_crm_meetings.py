"""Clients, operator statuses, and meetings.

Revision ID: 0003_crm_meetings
Revises: 0002_profile_media_video_note
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_crm_meetings"
down_revision: str | None = "0002_profile_media_video_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN = "status IN ('discussion', 'tentative', 'address_sent', 'phone_shared', 'confirmed')"


def upgrade() -> None:
    op.add_column("clients", sa.Column("phone_number", sa.String(length=32), nullable=True))
    op.alter_column("messages", "operator_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)

    op.create_table(
        "operator_statuses",
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            name="fk_operator_statuses_operator_id_operators",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("status IN ('free', 'busy', 'paused')", name="ck_operator_statuses_status"),
    )

    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("when_text", sa.String(length=120), nullable=True),
        sa.Column("client_ready", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_meetings_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["operators.id"],
            name="fk_meetings_operator_id_operators",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'discussion', 'tentative', 'address_sent', 'phone_shared', "
            "'confirmed', 'completed', 'cancelled'"
            ")",
            name="ck_meetings_status",
        ),
    )
    op.create_index("ix_meetings_client_id", "meetings", ["client_id"])
    op.create_index("ix_meetings_operator_id", "meetings", ["operator_id"])
    op.create_index(
        "uq_meetings_one_open_client",
        "meetings",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text(_OPEN),
    )
    op.create_index(
        "uq_meetings_one_open_operator",
        "meetings",
        ["operator_id"],
        unique=True,
        postgresql_where=sa.text(f"operator_id IS NOT NULL AND {_OPEN}"),
    )


def downgrade() -> None:
    op.drop_index("uq_meetings_one_open_operator", table_name="meetings")
    op.drop_index("uq_meetings_one_open_client", table_name="meetings")
    op.drop_index("ix_meetings_operator_id", table_name="meetings")
    op.drop_index("ix_meetings_client_id", table_name="meetings")
    op.drop_table("meetings")
    op.drop_table("operator_statuses")
    op.alter_column("messages", "operator_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_column("clients", "phone_number")
