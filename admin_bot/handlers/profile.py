import uuid
from io import BytesIO

import structlog
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from admin_bot.keyboards.profile_editor import (
    album_menu,
    media_keyboard,
    profile_editor,
    profile_root,
    prompt_back,
)
from admin_bot.services.profiles import add_profile_media, read_profile, write_profile
from admin_bot.ui import edit_callback_message, h
from core.config import Settings
from core.constants import CHANNEL_PROFILE_UPDATED, PROFILE_MEDIA_LIMIT, MediaKind
from core.crypto.service import CryptoService
from core.db.models import Operator
from core.redis.bus import RedisEventBus
from core.redis.media import delete_media, profile_media_key, store_profile_media
from core.repositories.operators import delete_owned_media, list_media
from core.schemas.events import ProfileUpdatedEvent
from core.schemas.operator import ProfileContent

router = Router(name="profile")
log = structlog.get_logger(__name__)


class ProfileEdit(StatesGroup):
    bio = State()
    age = State()
    city = State()
    tags = State()
    photo = State()
    video = State()
    video_note = State()


_PROFILE_PROMPTS: dict[str, tuple[State, str]] = {
    "profile:bio": (ProfileEdit.bio, "Отправьте текст анкеты. «-» очистит его."),
    "profile:age": (ProfileEdit.age, "Отправьте возраст числом. «-» очистит его."),
    "profile:city": (ProfileEdit.city, "Отправьте город. «-» очистит его."),
    "profile:tags": (ProfileEdit.tags, "Отправьте теги через запятую. «-» очистит их."),
}
_ALBUM_PROMPTS: dict[str, tuple[State, str]] = {
    "album:photo": (ProfileEdit.photo, "Отправьте фото в альбом. Подпись сохранится вместе с ним."),
    "album:video": (ProfileEdit.video, "Отправьте видео в альбом. Подпись сохранится вместе с ним."),
    "album:video_note": (ProfileEdit.video_note, "Отправьте кружок в альбом."),
}
_ALBUM_TEXT = (
    "Альбом не входит в анкету.\n"
    "Юзербот отправит фото, видео или кружок, когда клиент попросит их в диалоге."
)


@router.callback_query(F.data == "menu:profile")
async def open_profile(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_callback_message(
        callback,
        "Анкета — текст, который клиент видит в начале диалога.",
        profile_root(),
    )


@router.callback_query(F.data == "menu:album")
async def open_album(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_callback_message(callback, _ALBUM_TEXT, album_menu())


@router.callback_query(F.data == "profile:edit")
async def open_editor(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_callback_message(callback, "Что изменить в анкете?", profile_editor())


@router.callback_query(F.data.in_({*_PROFILE_PROMPTS, *_ALBUM_PROMPTS}))
async def ask_field(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data or ""
    if key in _ALBUM_PROMPTS:
        field_state, text = _ALBUM_PROMPTS[key]
        back = "menu:album"
    else:
        field_state, text = _PROFILE_PROMPTS[key]
        back = "profile:edit"
    await state.set_state(field_state)
    await edit_callback_message(callback, text, prompt_back(back))


@router.message(ProfileEdit.bio)
async def save_bio(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
) -> None:
    profile = read_profile(crypto, operator)
    raw = (message.text or "").strip()
    profile.bio = "" if raw == "-" else raw
    await _save(message, state, session, operator, crypto, redis, profile)


@router.message(ProfileEdit.age)
async def save_age(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
) -> None:
    profile = read_profile(crypto, operator)
    raw = (message.text or "").strip()
    if raw == "-":
        profile.age = None
    else:
        try:
            profile.age = int(raw)
            ProfileContent.model_validate(profile.model_dump())
        except (ValueError, ValidationError):
            await message.answer("Введите число от 1 до 120 или «-».", reply_markup=prompt_back())
            return
    await _save(message, state, session, operator, crypto, redis, profile)


@router.message(ProfileEdit.city)
async def save_city(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
) -> None:
    profile = read_profile(crypto, operator)
    raw = (message.text or "").strip()
    profile.city = None if raw == "-" else raw
    await _save(message, state, session, operator, crypto, redis, profile)


@router.message(ProfileEdit.tags)
async def save_tags(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
) -> None:
    profile = read_profile(crypto, operator)
    raw = (message.text or "").strip()
    profile.tags = [] if raw == "-" else [part.strip() for part in raw.split(",") if part.strip()]
    await _save(message, state, session, operator, crypto, redis, profile)


@router.message(ProfileEdit.photo)
async def save_photo(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
    settings: Settings,
) -> None:
    if message.photo is None:
        await message.answer("Нужно именно фото.", reply_markup=prompt_back("menu:album"))
        return
    await _save_media(
        message,
        bot,
        state,
        session,
        operator,
        crypto,
        redis,
        kind=MediaKind.PHOTO.value,
        file_id=message.photo[-1].file_id,
        file_size=message.photo[-1].file_size,
        max_bytes=settings.media_max_bytes,
    )


@router.message(ProfileEdit.video)
async def save_video(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
    settings: Settings,
) -> None:
    if message.video is None:
        await message.answer("Нужно именно видео.", reply_markup=prompt_back("menu:album"))
        return
    await _save_media(
        message,
        bot,
        state,
        session,
        operator,
        crypto,
        redis,
        kind=MediaKind.VIDEO.value,
        file_id=message.video.file_id,
        file_size=message.video.file_size,
        max_bytes=settings.media_max_bytes,
    )


@router.message(ProfileEdit.video_note)
async def save_video_note(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
    settings: Settings,
) -> None:
    if message.video_note is None:
        await message.answer("Нужно именно кружок.", reply_markup=prompt_back("menu:album"))
        return
    await _save_media(
        message,
        bot,
        state,
        session,
        operator,
        crypto,
        redis,
        kind=MediaKind.VIDEO_NOTE.value,
        file_id=message.video_note.file_id,
        file_size=message.video_note.file_size,
        max_bytes=settings.media_max_bytes,
    )


@router.callback_query(F.data == "profile:show")
async def show_profile(callback: CallbackQuery, operator: Operator, crypto: CryptoService) -> None:
    profile = read_profile(crypto, operator)
    await edit_callback_message(
        callback,
        h(profile.render(operator.display_name)),
        profile_root(),
    )


@router.callback_query(F.data == "album:show")
async def show_album(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
) -> None:
    media_rows = await list_media(session, operator.id)
    if not media_rows:
        await edit_callback_message(callback, "Альбом пуст.", album_menu())
        return
    await edit_callback_message(callback, "Файлы альбома. В анкету они не входят.", media_keyboard(media_rows))
    message = callback.message
    if not isinstance(message, Message):
        return
    for media in media_rows:
        caption = None
        if media.caption_ciphertext:
            caption = crypto.decrypt_for_operator(operator.id, media.caption_ciphertext).decode()
        if media.kind == MediaKind.PHOTO.value:
            await bot.send_photo(message.chat.id, media.file_id, caption=caption)
        elif media.kind == MediaKind.VIDEO.value:
            await bot.send_video(message.chat.id, media.file_id, caption=caption)
        elif media.kind == MediaKind.VIDEO_NOTE.value:
            if caption:
                await bot.send_message(message.chat.id, caption)
            await bot.send_video_note(message.chat.id, media.file_id)


@router.callback_query(F.data.startswith("profile:del:"))
async def remove_media(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
) -> None:
    raw = (callback.data or "").split(":", maxsplit=2)[2]
    try:
        media_id = uuid.UUID(raw)
    except ValueError:
        await callback.answer("Некорректный идентификатор.")
        return
    deleted = await delete_owned_media(session, media_id, operator.id)
    if not deleted:
        await callback.answer("Файл не найден.")
        return
    await delete_media(redis, profile_media_key(media_id))
    await RedisEventBus(redis).publish(
        CHANNEL_PROFILE_UPDATED,
        ProfileUpdatedEvent(operator_id=operator.id),
    )
    log.info("profile_media_deleted", operator_id=str(operator.id), media_id=str(media_id))
    await edit_callback_message(callback, "Файл удалён из альбома.", album_menu())


async def _save(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
    profile: ProfileContent,
) -> None:
    try:
        validated = ProfileContent.model_validate(profile.model_dump())
    except ValidationError:
        await message.answer(
            "Не удалось сохранить анкету. Проверьте длину текста и тегов.",
            reply_markup=prompt_back(),
        )
        return
    await write_profile(session, crypto, operator, validated)
    await _publish_profile(redis, operator)
    await state.clear()
    await message.answer("Анкета сохранена.", reply_markup=profile_editor())


async def _save_media(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
    *,
    kind: str,
    file_id: str,
    file_size: int | None,
    max_bytes: int,
) -> None:
    if file_size is not None and file_size > max_bytes:
        await message.answer("Файл слишком большой.", reply_markup=prompt_back("menu:album"))
        return
    try:
        payload = await _download(bot, file_id, max_bytes)
    except ValueError:
        await message.answer("Файл слишком большой.", reply_markup=prompt_back("menu:album"))
        return
    media = await add_profile_media(
        session,
        crypto,
        operator,
        kind=kind,
        file_id=file_id,
        caption=message.caption,
    )
    if media is None:
        await message.answer(
            f"В альбоме уже {PROFILE_MEDIA_LIMIT} файлов.",
            reply_markup=prompt_back("menu:album"),
        )
        return
    await store_profile_media(redis, media.id, payload)
    await _publish_profile(redis, operator)
    await state.clear()
    log.info("profile_media_saved", operator_id=str(operator.id), media_id=str(media.id), kind=kind)
    await message.answer("Файл добавлен в альбом.", reply_markup=album_menu())


async def _download(bot: Bot, file_id: str, max_bytes: int) -> bytes:
    remote = await bot.get_file(file_id)
    if remote.file_size is not None and remote.file_size > max_bytes:
        raise ValueError("too large")
    if remote.file_path is None:
        raise ValueError("missing file path")
    buffer = BytesIO()
    await bot.download_file(remote.file_path, buffer)
    payload = buffer.getvalue()
    if len(payload) > max_bytes:
        raise ValueError("too large")
    return payload


async def _publish_profile(redis: Redis, operator: Operator) -> None:
    await RedisEventBus(redis).publish(
        CHANNEL_PROFILE_UPDATED,
        ProfileUpdatedEvent(operator_id=operator.id),
    )
