import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Operator, ProfileMedia
from core.schemas.operator import OperatorPublicSummary


async def get_by_id(session: AsyncSession, operator_id: uuid.UUID) -> Operator | None:
    return await session.get(Operator, operator_id)


async def get_by_telegram_user_id(session: AsyncSession, telegram_user_id: int) -> Operator | None:
    statement = select(Operator).where(Operator.telegram_user_id == telegram_user_id)
    return await session.scalar(statement)


async def list_active_ids(session: AsyncSession) -> list[uuid.UUID]:
    statement = select(Operator.id).where(Operator.is_active.is_(True))
    return list(await session.scalars(statement))


async def list_telegram_user_ids(session: AsyncSession) -> set[int]:
    statement = select(Operator.telegram_user_id).where(Operator.is_active.is_(True))
    return set(await session.scalars(statement))


async def list_public_summaries(session: AsyncSession) -> list[OperatorPublicSummary]:
    statement = select(
        Operator.id,
        Operator.telegram_user_id,
        Operator.username,
        Operator.display_name,
        Operator.is_superadmin,
        Operator.is_active,
    ).order_by(Operator.display_name)
    rows = (await session.execute(statement)).all()
    return [
        OperatorPublicSummary(
            id=row.id,
            telegram_user_id=row.telegram_user_id,
            username=row.username,
            display_name=row.display_name,
            is_superadmin=row.is_superadmin,
            is_active=row.is_active,
        )
        for row in rows
    ]


async def create_operator(
    session: AsyncSession,
    *,
    operator_id: uuid.UUID,
    telegram_user_id: int,
    display_name: str,
    profile_ciphertext: bytes,
    is_superadmin: bool = False,
    username: str | None = None,
) -> Operator:
    operator = Operator(
        id=operator_id,
        telegram_user_id=telegram_user_id,
        username=username,
        display_name=display_name,
        is_superadmin=is_superadmin,
        is_active=True,
        profile_ciphertext=profile_ciphertext,
    )
    session.add(operator)
    await session.flush()
    return operator


async def set_active(session: AsyncSession, operator_id: uuid.UUID, is_active: bool) -> None:
    statement = update(Operator).where(Operator.id == operator_id).values(is_active=is_active)
    await session.execute(statement)


async def save_profile_ciphertext(
    session: AsyncSession,
    operator_id: uuid.UUID,
    profile_ciphertext: bytes,
) -> None:
    statement = (
        update(Operator)
        .where(Operator.id == operator_id)
        .values(profile_ciphertext=profile_ciphertext)
    )
    await session.execute(statement)


async def list_media(session: AsyncSession, operator_id: uuid.UUID) -> list[ProfileMedia]:
    statement = (
        select(ProfileMedia)
        .where(ProfileMedia.operator_id == operator_id)
        .order_by(ProfileMedia.position)
    )
    return list(await session.scalars(statement))


async def count_media(session: AsyncSession, operator_id: uuid.UUID) -> int:
    rows = await list_media(session, operator_id)
    return len(rows)


async def next_media_position(session: AsyncSession, operator_id: uuid.UUID) -> int:
    statement = select(ProfileMedia.position).where(ProfileMedia.operator_id == operator_id)
    positions = list(await session.scalars(statement))
    if not positions:
        return 0
    return max(positions) + 1


async def add_media(
    session: AsyncSession,
    *,
    media_id: uuid.UUID,
    operator_id: uuid.UUID,
    kind: str,
    file_id: str,
    caption_ciphertext: bytes | None,
    position: int,
) -> ProfileMedia:
    media = ProfileMedia(
        id=media_id,
        operator_id=operator_id,
        kind=kind,
        file_id=file_id,
        caption_ciphertext=caption_ciphertext,
        position=position,
    )
    session.add(media)
    await session.flush()
    return media


async def delete_owned_media(
    session: AsyncSession,
    media_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> bool:
    statement = select(ProfileMedia).where(
        ProfileMedia.id == media_id,
        ProfileMedia.operator_id == operator_id,
    )
    media = await session.scalar(statement)
    if media is None:
        return False
    await session.delete(media)
    await session.flush()
    return True
