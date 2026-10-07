import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Message


async def insert_message(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
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
