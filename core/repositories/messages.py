import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.db.models import Message


async def insert_message(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    client_id: uuid.UUID,
    operator_id: uuid.UUID | None,
    direction: str,
    ciphertext: bytes,
    media_file_id: str | None,
    telegram_message_id: int | None,
) -> tuple[Message, bool]:
    message = Message(
        id=message_id,
        client_id=client_id,
        operator_id=operator_id,
        direction=direction,
        ciphertext=ciphertext,
        media_file_id=media_file_id,
        telegram_message_id=telegram_message_id,
        created_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(message)
            await session.flush()
    except IntegrityError:
        existing = await _find_source(session, client_id, direction, telegram_message_id)
        if existing is None:
            raise
        return existing, False
    return message, True


async def list_recent_dialog(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
    limit: int,
) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.client_id == client_id, Message.operator_id == operator_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(await session.scalars(statement))
    rows.reverse()
    return rows


async def list_recent_visible(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
    limit: int,
) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.client_id == client_id, _visible(operator_id))
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(await session.scalars(statement))
    rows.reverse()
    return rows


async def list_visible_dialog(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
    limit: int,
) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.client_id == client_id, _visible(operator_id))
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_recent_desk(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    limit: int,
) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.client_id == client_id, Message.operator_id.is_(None))
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(await session.scalars(statement))
    rows.reverse()
    return rows


def _visible(operator_id: uuid.UUID) -> ColumnElement[bool]:
    return or_(Message.operator_id == operator_id, Message.operator_id.is_(None))


async def list_dialog(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
    limit: int,
) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.client_id == client_id, Message.operator_id == operator_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def _find_source(
    session: AsyncSession,
    client_id: uuid.UUID,
    direction: str,
    telegram_message_id: int | None,
) -> Message | None:
    if telegram_message_id is None:
        return None
    statement = select(Message).where(
        Message.client_id == client_id,
        Message.direction == direction,
        Message.telegram_message_id == telegram_message_id,
    )
    return await session.scalar(statement)
