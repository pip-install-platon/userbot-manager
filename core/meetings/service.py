import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import ClientState, MeetingStatus, OperatorStatus
from core.crypto.service import CryptoService
from core.db.models import Client, Meeting
from core.meetings.facts import MeetingFacts
from core.meetings.phrases import (
    explain_address,
    explain_address_missing,
    explain_cancel,
    explain_phone,
    explain_phone_missing,
    explain_reschedule,
)
from core.redis import status as operator_status
from core.repositories import clients as client_repository
from core.repositories import meetings as meeting_repository
from core.repositories import operator_status as status_repository
from core.repositories import operators as operator_repository
from core.schemas.events import MeetingOfferEvent, OperatorAlertEvent

_NEGOTIATING = {MeetingStatus.DISCUSSION, MeetingStatus.TENTATIVE}
_OWNED = {MeetingStatus.ADDRESS_SENT, MeetingStatus.PHONE_SHARED, MeetingStatus.CONFIRMED}


class ClaimCode(StrEnum):
    CLAIMED = "claimed"
    LOST = "lost"
    NOT_FREE = "not_free"
    HAS_MEETING = "has_meeting"
    MISSING = "missing"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class ClaimResult:
    code: ClaimCode
    notice: str
    reply: str | None = None
    client_id: uuid.UUID | None = None
    client_telegram_id: int | None = None
    meeting_id: uuid.UUID | None = None


@dataclass(frozen=True)
class MeetingAction:
    ok: bool
    notice: str
    reply: str | None = None
    client_id: uuid.UUID | None = None
    client_telegram_id: int | None = None
    meeting_id: uuid.UUID | None = None
    operator_status: OperatorStatus | None = None


@dataclass(frozen=True)
class ClientAdvance:
    status: MeetingStatus
    city: str | None
    when_text: str | None
    forced_reply: str | None
    offer: MeetingOfferEvent | None
    alert: OperatorAlertEvent | None


async def free_operator_ids(session: AsyncSession, redis: Redis) -> list[uuid.UUID]:
    active = await operator_repository.list_active_ids(session)
    statuses = await operator_status.list_statuses(redis)
    return [item for item in active if statuses.get(item) == OperatorStatus.FREE.value]


async def free_cities(session: AsyncSession, redis: Redis, crypto: CryptoService) -> list[str]:
    cities: list[str] = []
    for operator_id in await free_operator_ids(session, redis):
        operator = await operator_repository.get_by_id(session, operator_id)
        if operator is None:
            continue
        city = crypto.decrypt_profile(operator.id, operator.profile_ciphertext).city
        if city and city not in cities:
            cities.append(city)
    return cities


async def apply_client_facts(
    session: AsyncSession,
    redis: Redis,
    crypto: CryptoService,
    client: Client,
    facts: MeetingFacts,
) -> ClientAdvance:
    meeting = await meeting_repository.ensure_open(session, client.id)
    status = MeetingStatus(meeting.status)
    if status in _NEGOTIATING and meeting.operator_id is None:
        return await _negotiate(session, redis, client, meeting, facts)
    if status == MeetingStatus.ADDRESS_SENT and facts.client_confirmed:
        return await _client_confirmed(session, crypto, meeting)
    return _snapshot(meeting)


async def claim(
    session: AsyncSession,
    redis: Redis,
    crypto: CryptoService,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> ClaimResult:
    operator = await operator_repository.get_by_id(session, operator_id)
    if operator is None or not operator.is_active:
        return ClaimResult(code=ClaimCode.INACTIVE, notice="Оператор недоступен.")
    if await meeting_repository.get_open_for_operator(session, operator_id) is not None:
        return ClaimResult(code=ClaimCode.HAS_MEETING, notice="Сначала завершите текущую встречу.")
    current = await operator_status.get_status(redis, operator_id)
    if current != OperatorStatus.FREE.value:
        return ClaimResult(code=ClaimCode.NOT_FREE, notice="Принять заказ можно только со статусом «Свободен».")
    matched = await _take_tentative(session, meeting_id, operator_id)
    if matched is None:
        return ClaimResult(code=ClaimCode.HAS_MEETING, notice="Сначала завершите текущую встречу.")
    if not matched:
        existing = await meeting_repository.get_by_id(session, meeting_id)
        if existing is None:
            return ClaimResult(code=ClaimCode.MISSING, notice="Встреча не найдена.")
        return ClaimResult(code=ClaimCode.LOST, notice="Заказ уже забрали.")
    meeting = await _reload(session, meeting_id)
    client = None if meeting is None else await client_repository.get_by_id(session, meeting.client_id)
    if meeting is None or client is None:
        return ClaimResult(code=ClaimCode.MISSING, notice="Встреча не найдена.")
    client.assigned_operator_id = operator_id
    client.state = ClientState.IN_DIALOG.value
    profile = crypto.decrypt_profile(operator.id, operator.profile_ciphertext)
    if profile.approx_address:
        reply = explain_address(profile.approx_address)
        notice = "Заказ ваш. Адрес отправлен клиенту."
    else:
        reply = explain_address_missing()
        notice = "Заказ ваш. Укажите примерный адрес в анкете."
    return ClaimResult(
        code=ClaimCode.CLAIMED,
        notice=notice,
        reply=reply,
        client_id=client.id,
        client_telegram_id=client.telegram_user_id,
        meeting_id=meeting.id,
    )


async def resend_address(
    session: AsyncSession,
    crypto: CryptoService,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> MeetingAction:
    meeting = await _owned(session, meeting_id, operator_id)
    if meeting is None or MeetingStatus(meeting.status) != MeetingStatus.ADDRESS_SENT:
        return MeetingAction(ok=False, notice="Адрес отправляют, пока клиент ещё не получил номер.")
    operator = await operator_repository.get_by_id(session, operator_id)
    if operator is None:
        return MeetingAction(ok=False, notice="Оператор недоступен.")
    address = crypto.decrypt_profile(operator.id, operator.profile_ciphertext).approx_address
    if not address:
        return MeetingAction(ok=False, notice="Укажите примерный адрес в анкете.")
    return await _reply(session, meeting, explain_address(address), "Адрес отправлен клиенту.")


async def send_phone(
    session: AsyncSession,
    crypto: CryptoService,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> MeetingAction:
    meeting = await _owned(session, meeting_id, operator_id)
    if meeting is None or MeetingStatus(meeting.status) != MeetingStatus.ADDRESS_SENT or not meeting.client_ready:
        return MeetingAction(ok=False, notice="Клиент ещё не подтвердил встречу.")
    operator = await operator_repository.get_by_id(session, operator_id)
    if operator is None:
        return MeetingAction(ok=False, notice="Оператор недоступен.")
    phone = crypto.decrypt_profile(operator.id, operator.profile_ciphertext).phone
    if not phone:
        return MeetingAction(ok=False, notice="Сначала укажите телефон в анкете.")
    meeting_repository.touch(meeting, MeetingStatus.PHONE_SHARED)
    return await _reply(session, meeting, explain_phone(phone), "Номер отправлен клиенту.")


async def confirm_call(
    session: AsyncSession,
    redis: Redis,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
    status_ttl_seconds: int,
) -> MeetingAction:
    meeting = await _owned(session, meeting_id, operator_id)
    if meeting is None or MeetingStatus(meeting.status) != MeetingStatus.PHONE_SHARED:
        return MeetingAction(ok=False, notice="Сначала клиент подтверждает встречу и получает номер.")
    meeting_repository.touch(meeting, MeetingStatus.CONFIRMED)
    await status_repository.upsert_status(session, operator_id, OperatorStatus.BUSY.value)
    await operator_status.set_status(redis, operator_id, OperatorStatus.BUSY, status_ttl_seconds)
    client = await client_repository.get_by_id(session, meeting.client_id)
    return MeetingAction(
        ok=True,
        notice="Встреча подтверждена. Статус: занят.",
        client_id=None if client is None else client.id,
        client_telegram_id=None if client is None else client.telegram_user_id,
        meeting_id=meeting.id,
        operator_status=OperatorStatus.BUSY,
    )


async def complete(
    session: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> MeetingAction:
    meeting = await _owned(session, meeting_id, operator_id)
    if meeting is None or MeetingStatus(meeting.status) != MeetingStatus.CONFIRMED:
        return MeetingAction(ok=False, notice="Сначала подтвердите встречу звонком.")
    meeting_repository.touch(meeting, MeetingStatus.COMPLETED)
    client = await client_repository.get_by_id(session, meeting.client_id)
    return MeetingAction(
        ok=True,
        notice="Встреча завершена. Выставьте статус стрелками.",
        client_id=None if client is None else client.id,
        client_telegram_id=None if client is None else client.telegram_user_id,
        meeting_id=meeting.id,
    )


async def cancel(
    session: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> MeetingAction:
    meeting = await _owned(session, meeting_id, operator_id)
    if meeting is None:
        return MeetingAction(ok=False, notice="Эту встречу нельзя отменить.")
    reply = explain_cancel(meeting.city)
    meeting_repository.touch(meeting, MeetingStatus.CANCELLED)
    return await _release(session, meeting, reply, "Встреча отменена.")


async def reschedule(
    session: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> MeetingAction:
    meeting = await _owned(session, meeting_id, operator_id)
    if meeting is None:
        return MeetingAction(ok=False, notice="Эту встречу нельзя перенести.")
    reply = explain_reschedule(meeting.when_text)
    meeting.when_text = None
    meeting.client_ready = False
    meeting.operator_id = None
    meeting_repository.touch(meeting, MeetingStatus.DISCUSSION)
    return await _release(session, meeting, reply, "Попросила клиента выбрать другое время.")


async def offers_for_operator(session: AsyncSession, operator_id: uuid.UUID) -> list[MeetingOfferEvent]:
    if await meeting_repository.get_open_for_operator(session, operator_id) is not None:
        return []
    events: list[MeetingOfferEvent] = []
    for meeting in await meeting_repository.list_unclaimed_tentative(session, limit=10):
        if not meeting.city or not meeting.when_text:
            continue
        client = await client_repository.get_by_id(session, meeting.client_id)
        if client is None:
            continue
        events.append(_offer(meeting, client, [operator_id]))
    return events


async def _negotiate(
    session: AsyncSession,
    redis: Redis,
    client: Client,
    meeting: Meeting,
    facts: MeetingFacts,
) -> ClientAdvance:
    if facts.city:
        meeting.city = facts.city[:80]
    if facts.when_text:
        meeting.when_text = facts.when_text[:120]
    became_tentative = bool(meeting.city and meeting.when_text and meeting.status != MeetingStatus.TENTATIVE.value)
    if became_tentative:
        meeting_repository.touch(meeting, MeetingStatus.TENTATIVE)
    else:
        meeting_repository.touch(meeting)
    offer = None
    if became_tentative and meeting.city and meeting.when_text:
        free_ids = await free_operator_ids(session, redis)
        if free_ids:
            offer = _offer(meeting, client, free_ids)
    return _snapshot(meeting, offer=offer)


async def _client_confirmed(
    session: AsyncSession,
    crypto: CryptoService,
    meeting: Meeting,
) -> ClientAdvance:
    meeting.client_ready = True
    phone = await _operator_phone(session, crypto, meeting.operator_id)
    if phone:
        meeting_repository.touch(meeting, MeetingStatus.PHONE_SHARED)
        return _snapshot(meeting, forced_reply=explain_phone(phone))
    meeting_repository.touch(meeting)
    alert = None
    if meeting.operator_id is not None:
        alert = OperatorAlertEvent(
            operator_id=meeting.operator_id,
            text="Клиент подтвердил встречу. Укажите телефон в анкете и нажмите «Отправить номер».",
        )
    return _snapshot(meeting, forced_reply=explain_phone_missing(), alert=alert)


async def _operator_phone(
    session: AsyncSession,
    crypto: CryptoService,
    operator_id: uuid.UUID | None,
) -> str | None:
    if operator_id is None:
        return None
    operator = await operator_repository.get_by_id(session, operator_id)
    if operator is None:
        return None
    return crypto.decrypt_profile(operator.id, operator.profile_ciphertext).phone


async def _take_tentative(session: AsyncSession, meeting_id: uuid.UUID, operator_id: uuid.UUID) -> bool | None:
    statement = (
        update(Meeting)
        .where(
            Meeting.id == meeting_id,
            Meeting.status == MeetingStatus.TENTATIVE.value,
            Meeting.operator_id.is_(None),
        )
        .values(
            operator_id=operator_id,
            status=MeetingStatus.ADDRESS_SENT.value,
            updated_at=datetime.now(UTC),
        )
    )
    try:
        async with session.begin_nested():
            result = await session.execute(statement.returning(Meeting.id))
            return result.scalar_one_or_none() is not None
    except IntegrityError:
        return None


async def _reload(session: AsyncSession, meeting_id: uuid.UUID) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting is not None:
        await session.refresh(meeting)
    return meeting


async def _owned(session: AsyncSession, meeting_id: uuid.UUID, operator_id: uuid.UUID) -> Meeting | None:
    meeting = await meeting_repository.get_by_id(session, meeting_id)
    if meeting is None or meeting.operator_id != operator_id:
        return None
    if MeetingStatus(meeting.status) not in _OWNED:
        return None
    return meeting


async def _release(session: AsyncSession, meeting: Meeting, reply: str, notice: str) -> MeetingAction:
    client = await client_repository.get_by_id(session, meeting.client_id)
    if client is not None:
        client.assigned_operator_id = None
        client.state = ClientState.NEW.value
    return MeetingAction(
        ok=True,
        notice=notice,
        reply=reply,
        client_id=None if client is None else client.id,
        client_telegram_id=None if client is None else client.telegram_user_id,
        meeting_id=meeting.id,
    )


async def _reply(session: AsyncSession, meeting: Meeting, reply: str, notice: str) -> MeetingAction:
    client = await client_repository.get_by_id(session, meeting.client_id)
    return MeetingAction(
        ok=True,
        notice=notice,
        reply=reply,
        client_id=None if client is None else client.id,
        client_telegram_id=None if client is None else client.telegram_user_id,
        meeting_id=meeting.id,
    )


def _offer(meeting: Meeting, client: Client, operator_ids: list[uuid.UUID]) -> MeetingOfferEvent:
    return MeetingOfferEvent(
        meeting_id=meeting.id,
        client_id=client.id,
        client_telegram_id=client.telegram_user_id,
        client_username=client.username,
        city=meeting.city or "",
        when_text=meeting.when_text or "",
        operator_ids=operator_ids,
    )


def _snapshot(
    meeting: Meeting,
    *,
    forced_reply: str | None = None,
    offer: MeetingOfferEvent | None = None,
    alert: OperatorAlertEvent | None = None,
) -> ClientAdvance:
    return ClientAdvance(
        status=MeetingStatus(meeting.status),
        city=meeting.city,
        when_text=meeting.when_text,
        forced_reply=forced_reply,
        offer=offer,
        alert=alert,
    )
