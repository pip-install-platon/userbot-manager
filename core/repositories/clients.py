import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import ClientState
from core.db.models import Client


async def get_by_id(session: AsyncSession, client_id: uuid.UUID) -> Client | None:
    return await session.get(Client, client_id)


async def get_by_telegram_user_id(session: AsyncSession, telegram_user_id: int) -> Client | None:
    statement = select(Client).where(Client.telegram_user_id == telegram_user_id)
    return await session.scalar(statement)


async def get_owned(
    session: AsyncSession,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> Client | None:
    statement = select(Client).where(
        Client.id == client_id,
        Client.assigned_operator_id == operator_id,
    )
    return await session.scalar(statement)


async def get_or_create(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    username: str | None,
) -> Client:
    existing = await get_by_telegram_user_id(session, telegram_user_id)
    if existing is not None:
        if username and existing.username != username:
            existing.username = username
        return existing
    client = Client(
        id=uuid.uuid4(),
        telegram_user_id=telegram_user_id,
        username=username,
        state=ClientState.NEW.value,
    )
    try:
        async with session.begin_nested():
            session.add(client)
            await session.flush()
    except IntegrityError:
        raced = await get_by_telegram_user_id(session, telegram_user_id)
        if raced is None:
            raise
        return raced
    return client


async def list_for_operator(
    session: AsyncSession,
    operator_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> list[Client]:
    statement = (
        select(Client)
        .where(Client.assigned_operator_id == operator_id)
        .order_by(Client.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))


def belongs_to_operator(client: Client, operator_id: uuid.UUID) -> bool:
    return client.assigned_operator_id == operator_id
