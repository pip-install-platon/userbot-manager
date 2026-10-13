import uuid
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.ai.openrouter import ReplyModel
from core.ai.persona import desk_messages
from core.constants import AI_HISTORY_MESSAGES, MAX_TEXT_LENGTH, Direction, MeetingStatus
from core.crypto.service import CryptoService
from core.meetings import service as meetings
from core.meetings.facts import MeetingExtractor, extract_local, merge_facts
from core.meetings.phrases import desk_template
from core.repositories import clients as client_repository
from core.repositories import messages as message_repository
from core.schemas.events import MeetingOfferEvent, OperatorAlertEvent

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DeskOutcome:
    reply: str | None = None
    offer: MeetingOfferEvent | None = None
    alert: OperatorAlertEvent | None = None


async def conduct(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    crypto: CryptoService,
    model: ReplyModel | None,
    extractor: MeetingExtractor | None,
    *,
    client_id: uuid.UUID,
) -> DeskOutcome:
    async with sessions() as session:
        history = await _history(session, crypto, client_id, operator_id=None)
        cities = await meetings.free_cities(session, redis, crypto)
    latest = history[-1][1] if history else ""
    facts = extract_local(latest, cities)
    if extractor is not None and history:
        try:
            facts = merge_facts(await extractor.extract(history), facts)
        except Exception:
            log.warning("meeting_extract_failed", client_id=str(client_id))
    reply = await _model_reply(model, cities, history)
    async with sessions() as session:
        client = await client_repository.get_by_id(session, client_id)
        if client is None:
            return DeskOutcome()
        advance = await meetings.apply_client_facts(session, redis, crypto, client, facts)
        text = _choose_reply(
            advance.forced_reply,
            reply,
            advance.city,
            advance.when_text,
            advance.status,
        )
        await message_repository.insert_message(
            session,
            message_id=uuid.uuid4(),
            client_id=client_id,
            operator_id=None,
            direction=Direction.OUTGOING.value,
            ciphertext=crypto.encrypt_for_desk(text.encode()),
            media_file_id=None,
            telegram_message_id=None,
        )
        await session.commit()
    return DeskOutcome(reply=text, offer=advance.offer, alert=advance.alert)


async def confirm_assigned(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    crypto: CryptoService,
    extractor: MeetingExtractor | None,
    *,
    operator_id: uuid.UUID,
    client_id: uuid.UUID,
    text: str,
) -> DeskOutcome | None:
    facts = extract_local(text, [])
    if extractor is not None:
        async with sessions() as session:
            history = await _history(session, crypto, client_id, operator_id=operator_id)
        try:
            facts = merge_facts(await extractor.extract(history), facts)
        except Exception:
            log.warning("meeting_extract_failed", client_id=str(client_id))
    if not facts.client_confirmed:
        return None
    async with sessions() as session:
        client = await client_repository.get_by_id(session, client_id)
        if client is None or client.assigned_operator_id != operator_id:
            return None
        advance = await meetings.apply_client_facts(session, redis, crypto, client, facts)
        if advance.forced_reply is None:
            return None
        await message_repository.insert_message(
            session,
            message_id=uuid.uuid4(),
            client_id=client_id,
            operator_id=operator_id,
            direction=Direction.OUTGOING.value,
            ciphertext=crypto.encrypt_for_operator(operator_id, advance.forced_reply.encode()),
            media_file_id=None,
            telegram_message_id=None,
        )
        await session.commit()
    return DeskOutcome(reply=advance.forced_reply, alert=advance.alert)


async def _history(
    session: AsyncSession,
    crypto: CryptoService,
    client_id: uuid.UUID,
    *,
    operator_id: uuid.UUID | None,
) -> list[tuple[str, str]]:
    if operator_id is None:
        rows = await message_repository.list_recent_desk(
            session,
            client_id=client_id,
            limit=AI_HISTORY_MESSAGES,
        )
    else:
        rows = await message_repository.list_recent_visible(
            session,
            client_id=client_id,
            operator_id=operator_id,
            limit=AI_HISTORY_MESSAGES,
        )
    return [(row.direction, crypto.decrypt_text(row.operator_id, row.ciphertext)) for row in rows]


async def _model_reply(
    model: ReplyModel | None,
    cities: list[str],
    history: list[tuple[str, str]],
) -> str | None:
    if model is None or not any(direction == Direction.INCOMING.value and text.strip() for direction, text in history):
        return None
    try:
        answer = await model.complete(desk_messages(cities=cities, returning=len(history) > 1, history=history))
    except Exception:
        log.warning("desk_reply_failed")
        return None
    cleaned = answer.strip()
    if not cleaned or len(cleaned) > MAX_TEXT_LENGTH:
        return None
    return cleaned


def _choose_reply(
    forced: str | None,
    generated: str | None,
    city: str | None,
    when_text: str | None,
    status: MeetingStatus,
) -> str:
    text = (forced or generated or "").strip()
    if text and len(text) <= MAX_TEXT_LENGTH:
        return text
    return desk_template(city=city, when_text=when_text, status=status)
