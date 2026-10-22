"""Ban clients who send overt abuse.

Revision ID: 0004_client_ban
Revises: 0003_crm_meetings
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_client_ban"
down_revision: str | None = "0003_crm_meetings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("clients", "is_banned")
