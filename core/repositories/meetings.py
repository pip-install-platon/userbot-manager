import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import OPEN_MEETING_STATUSES, MeetingStatus
from core.db.models import Meeting

_OPEN = tuple(status.value for status in OPEN_MEETING_STATUSES)
_OWNED = (
    MeetingStatus.ADDRESS_SENT.value,
    MeetingStatus.PHONE_SHARED.value,
    MeetingStatus.CONFIRMED.value,
)


async def get_by_id(session: AsyncSession, meeting_id: uuid.UUID) -> Meeting | None:
    return await session.get(Meeting, meeting_id)


async def get_open(session: AsyncSession, client_id: uuid.UUID) -> Meeting | None:
    statement = select(Meeting).where(Meeting.client_id == client_id, Meeting.status.in_(_OPEN))
    return await session.scalar(statement)


async def get_open_for_operator(session: AsyncSession, operator_id: uuid.UUID) -> Meeting | None:
    statement = select(Meeting).where(Meeting.operator_id == operator_id, Meeting.status.in_(_OPEN))
    return await session.scalar(statement)


async def latest(session: AsyncSession, client_id: uuid.UUID) -> Meeting | None:
    statement = select(Meeting).where(Meeting.client_id == client_id).order_by(Meeting.updated_at.desc()).limit(1)
    return await session.scalar(statement)


async def should_reopen_desk(session: AsyncSession, client_id: uuid.UUID) -> bool:
    if await get_open(session, client_id) is not None:
        return False
    last = await latest(session, client_id)
    return last is not None and last.status == MeetingStatus.COMPLETED.value


async def ensure_open(session: AsyncSession, client_id: uuid.UUID) -> Meeting:
    existing = await get_open(session, client_id)
    if existing is not None:
        return existing
    meeting = Meeting(
        id=uuid.uuid4(),
        client_id=client_id,
        operator_id=None,
        status=MeetingStatus.DISCUSSION.value,
        city=None,
        when_text=None,
        client_ready=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(meeting)
            await session.flush()
    except IntegrityError:
        raced = await get_open(session, client_id)
        if raced is None:
            raise
        return raced
    return meeting


async def list_unclaimed_tentative(session: AsyncSession, *, limit: int) -> list[Meeting]:
    statement = (
        select(Meeting)
        .where(
            Meeting.status == MeetingStatus.TENTATIVE.value,
            Meeting.operator_id.is_(None),
        )
        .order_by(Meeting.updated_at.asc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_owned_open(session: AsyncSession, operator_id: uuid.UUID) -> list[Meeting]:
    statement = (
        select(Meeting)
        .where(Meeting.operator_id == operator_id, Meeting.status.in_(_OWNED))
        .order_by(Meeting.updated_at.desc())
    )
    return list(await session.scalars(statement))


def touch(meeting: Meeting, status: MeetingStatus | None = None) -> None:
    if status is not None:
        meeting.status = status.value
    meeting.updated_at = datetime.now(UTC)
