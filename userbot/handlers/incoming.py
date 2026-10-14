import uuid
from io import BytesIO

import structlog
from pyrogram import filters
from pyrogram.client import Client
from pyrogram.types import CallbackQuery, Message

from core.constants import MAX_TEXT_LENGTH, MediaKind
from core.schemas.client import IncomingPrivateMessage
from userbot.services.dispatcher import UserbotDispatcher

log = structlog.get_logger(__name__)


def register_incoming(app: Client, dispatcher: UserbotDispatcher) -> None:
    @app.on_message(filters.private & ~filters.me & ~filters.service)
    async def on_private(client: Client, message: Message) -> None:
        incoming = await _to_incoming(client, message, dispatcher.settings.media_max_bytes)
        if incoming is None:
            return
        await dispatcher.handle_private(incoming)

    @app.on_callback_query(filters.regex(r"^assign:(accept|next):[0-9a-fA-F-]{36}$"))
    async def on_choice(_client: Client, callback: CallbackQuery) -> None:
        data = callback.data or ""
        if isinstance(data, bytes):
            data = data.decode()
        _prefix, action, raw_operator_id = data.split(":")
        user = callback.from_user
        if user is None:
            return
        await dispatcher.handle_assignment_choice(
            telegram_user_id=user.id,
            action=action,
            operator_id=uuid.UUID(raw_operator_id),
        )
        await callback.answer()


async def _to_incoming(client: Client, message: Message, max_bytes: int) -> IncomingPrivateMessage | None:
    user = message.from_user
    if user is None or message.id is None:
        return None
    text = message.text or message.caption
    phone = _phone(message)
    if text is not None and len(text) > MAX_TEXT_LENGTH:
        await message.reply("Сообщение слишком длинное.")
        return None
    media_kind: str | None = None
    file_id: str | None = None
    file_size: int | None = None
    if message.photo is not None:
        media_kind = MediaKind.PHOTO.value
        file_id, file_size = _file_ref(message.photo)
    elif message.video is not None:
        media_kind = MediaKind.VIDEO.value
        file_id, file_size = _file_ref(message.video)
    elif message.video_note is not None:
        media_kind = MediaKind.VIDEO_NOTE.value
        file_id, file_size = _file_ref(message.video_note)
    elif text is None and phone is None:
        text = bare_marker(_bare_kind(message))
    if file_size is not None and file_size > max_bytes:
        await message.reply("Файл слишком большой.")
        return None
    media_bytes: bytes | None = None
    if media_kind is not None:
        downloaded = await client.download_media(message, in_memory=True)
        if not isinstance(downloaded, BytesIO):
            log.warning("media_download_failed", telegram_message_id=message.id)
            await message.reply("Не удалось получить файл. Отправьте его ещё раз.")
            return None
        media_bytes = downloaded.getvalue()
        if len(media_bytes) > max_bytes:
            await message.reply("Файл слишком большой.")
            return None
    return IncomingPrivateMessage(
        telegram_user_id=user.id,
        username=user.username,
        message_id=message.id,
        text=text,
        phone_number=phone,
        media_file_id=file_id,
        media_kind=media_kind,
        media_bytes=media_bytes,
    )


def bare_marker(kind: str | None) -> str:
    markers = {
        "sticker": "[стикер]",
        "animation": "[гиф]",
        "voice": "[голосовое]",
        "audio": "[аудио]",
        "document": "[файл]",
    }
    if kind is None:
        return "[сообщение]"
    return markers.get(kind, "[сообщение]")


def _bare_kind(message: Message) -> str | None:
    if message.sticker is not None:
        return "sticker"
    if message.animation is not None:
        return "animation"
    if message.voice is not None:
        return "voice"
    if message.audio is not None:
        return "audio"
    if message.document is not None:
        return "document"
    return None


def _phone(message: Message) -> str | None:
    contact = message.contact
    if contact is not None and isinstance(contact.phone_number, str) and contact.phone_number.strip():
        return contact.phone_number
    user = message.from_user
    number = getattr(user, "phone_number", None)
    if isinstance(number, str) and number.strip():
        return number
    return None


def _file_ref(media: object) -> tuple[str | None, int | None]:
    current = media[-1] if isinstance(media, list) and media else media
    file_id = getattr(current, "file_id", None)
    file_size = getattr(current, "file_size", None)
    return (
        str(file_id) if isinstance(file_id, str) else None,
        file_size if isinstance(file_size, int) else None,
    )
