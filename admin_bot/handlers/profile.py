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

from admin_bot.keyboards.profile_editor import media_keyboard, profile_menu
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


@router.callback_query(F.data == "menu:profile")
async def open_profile(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_callback_message(callback, "Анкета. Выберите, что изменить.", profile_menu())


@router.callback_query(F.data == "profile:bio")
async def ask_bio(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEdit.bio)
    await edit_callback_message(callback, "Отправьте текст анкеты. «-» очистит его.")


@router.callback_query(F.data == "profile:age")
async def ask_age(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEdit.age)
    await edit_callback_message(callback, "Отправьте возраст числом. «-» очистит его.")


@router.callback_query(F.data == "profile:city")
async def ask_city(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEdit.city)
    await edit_callback_message(callback, "Отправьте город. «-» очистит его.")


@router.callback_query(F.data == "profile:tags")
async def ask_tags(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEdit.tags)
    await edit_callback_message(callback, "Отправьте теги через запятую. «-» очистит их.")


@router.callback_query(F.data == "profile:photo")
async def ask_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEdit.photo)
    await edit_callback_message(callback, "Отправьте фото. Подпись сохранится вместе с ним.")


@router.callback_query(F.data == "profile:video")
async def ask_video(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileEdit.video)
    await edit_callback_message(callback, "Отправьте видео. Подпись сохранится вместе с ним.")


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
            await message.answer("Введите число от 1 до 120 или «-».")
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
        await message.answer("Нужно именно фото.")
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
        await message.answer("Нужно именно видео.")
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


@router.callback_query(F.data == "profile:show")
async def show_profile(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
) -> None:
    profile = read_profile(crypto, operator)
    media_rows = await list_media(session, operator.id)
    await edit_callback_message(
        callback,
        h(profile.render(operator.display_name)),
        media_keyboard(media_rows),
    )
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
    await edit_callback_message(callback, "Файл удалён из анкеты.", profile_menu())


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
        await message.answer("Не удалось сохранить анкету. Проверьте длину текста и тегов.")
        return
    await write_profile(session, crypto, operator, validated)
    await _publish_profile(redis, operator)
    await state.clear()
    await message.answer("Анкета сохранена.", reply_markup=profile_menu())


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
        await message.answer("Файл слишком большой.")
        return
    try:
        payload = await _download(bot, file_id, max_bytes)
    except ValueError:
        await message.answer("Файл слишком большой.")
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
        await message.answer(f"В анкете уже {PROFILE_MEDIA_LIMIT} файлов.")
        return
    await store_profile_media(redis, media.id, payload)
    await _publish_profile(redis, operator)
    await state.clear()
    log.info("profile_media_saved", operator_id=str(operator.id), media_id=str(media.id), kind=kind)
    await message.answer("Файл добавлен в анкету.", reply_markup=profile_menu())


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
