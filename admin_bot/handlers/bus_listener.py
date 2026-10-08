import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_bot.ui import h
from core.constants import CHANNEL_INCOMING_CLIENT_MESSAGE, CHANNEL_NEW_CLIENT_ASSIGNED, MediaKind
from core.redis.client import open_redis
from core.redis.media import load_media
from core.repositories.operators import get_by_id
from core.schemas.events import IncomingClientMessageEvent, NewClientAssignedEvent

log = structlog.get_logger(__name__)


async def listen_bus(
    bot: Bot,
    redis_url: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    redis = open_redis(redis_url)
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL_INCOMING_CLIENT_MESSAGE, CHANNEL_NEW_CLIENT_ASSIGNED)
    try:
        async for incoming in pubsub.listen():
            if incoming.get("type") != "message":
                continue
            channel = str(incoming.get("channel", ""))
            payload = incoming.get("data")
            if not isinstance(payload, str):
                continue
            try:
                await _dispatch(bot, session_factory, redis_url, channel, payload)
            except ValidationError:
                log.error("admin_event_invalid", channel=channel)
            except Exception:
                log.error("admin_event_failed", channel=channel)
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await redis.aclose()


async def _dispatch(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    redis_url: str,
    channel: str,
    payload: str,
) -> None:
    if channel == CHANNEL_NEW_CLIENT_ASSIGNED:
        assigned = NewClientAssignedEvent.model_validate_json(payload)
        await _notify_assignment(bot, session_factory, assigned)
        return
    if channel == CHANNEL_INCOMING_CLIENT_MESSAGE:
        incoming = IncomingClientMessageEvent.model_validate_json(payload)
        await _notify_incoming(bot, session_factory, redis_url, incoming)


async def _notify_assignment(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    event: NewClientAssignedEvent,
) -> None:
    async with session_factory() as session:
        operator = await get_by_id(session, event.operator_id)
        if operator is None or not operator.is_active:
            return
        chat_id = operator.telegram_user_id
    label = f"@{event.client_username}" if event.client_username else str(event.client_telegram_id)
    await _safe_send(
        bot,
        chat_id,
        f"Новый клиент {h(label)}. Откройте «Мои клиенты».",
        event.operator_id,
    )


async def _notify_incoming(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    redis_url: str,
    event: IncomingClientMessageEvent,
) -> None:
    async with session_factory() as session:
        operator = await get_by_id(session, event.operator_id)
        if operator is None or not operator.is_active:
            return
        chat_id = operator.telegram_user_id
    label = f"@{event.client_username}" if event.client_username else str(event.client_telegram_id)
    body = event.text or ""
    text = f"Сообщение от {h(label)}:\n{h(body) if body else 'Вложение'}"
    await _safe_send(bot, chat_id, text, event.operator_id)
    if event.media_redis_key is None or event.media_kind is None:
        return
    redis = open_redis(redis_url)
    try:
        payload = await load_media(redis, event.media_redis_key)
    finally:
        await redis.aclose()
    if payload is None:
        log.warning("incoming_media_missing", operator_id=str(event.operator_id))
        return
    file = BufferedInputFile(payload, filename=f"{event.media_kind.value}.bin")
    try:
        if event.media_kind == MediaKind.PHOTO:
            await bot.send_photo(chat_id, file)
        elif event.media_kind == MediaKind.VIDEO:
            await bot.send_video(chat_id, file)
        elif event.media_kind == MediaKind.VIDEO_NOTE:
            await bot.send_video_note(chat_id, file)
    except TelegramAPIError:
        log.warning("incoming_media_undelivered", operator_id=str(event.operator_id))


async def _safe_send(bot: Bot, chat_id: int, text: str, operator_id: object) -> None:
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError:
        log.warning("operator_unreachable", operator_id=str(operator_id))
