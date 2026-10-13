import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import OperatorAvailability


async def upsert_status(session: AsyncSession, operator_id: uuid.UUID, status: str) -> None:
    row = await session.get(OperatorAvailability, operator_id)
    now = datetime.now(UTC)
    if row is None:
        session.add(OperatorAvailability(operator_id=operator_id, status=status, updated_at=now))
        await session.flush()
        return
    row.status = status
    row.updated_at = now


async def list_all(session: AsyncSession) -> list[OperatorAvailability]:
    statement = select(OperatorAvailability)
    return list(await session.scalars(statement))
