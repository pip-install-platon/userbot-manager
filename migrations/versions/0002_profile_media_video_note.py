"""Allow video notes in an operator album.

Revision ID: 0002_profile_media_video_note
Revises: 0001_initial
Create Date: 2026-10-08

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_profile_media_video_note"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_profile_media_kind", "profile_media", type_="check")
    op.create_check_constraint(
        "ck_profile_media_kind",
        "profile_media",
        "kind IN ('photo', 'video', 'video_note')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_profile_media_kind", "profile_media", type_="check")
    op.create_check_constraint(
        "ck_profile_media_kind",
        "profile_media",
        "kind IN ('photo', 'video')",
    )
