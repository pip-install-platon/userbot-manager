import uuid
from datetime import datetime
from io import BytesIO

import structlog
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from admin_bot.keyboards.main_menu import main_menu
from admin_bot.keyboards.profile_editor import dialog_actions
from admin_bot.services.dialogs import load_dialog_for_operator, store_outgoing
from admin_bot.ui import edit_callback_message, h
from core.config import Settings
from core.constants import (
    CHANNEL_OUTGOING_TO_CLIENT,
    DIALOG_PAGE_SIZE,
    MAX_TEXT_LENGTH,
    ClientState,
    Direction,
    MediaKind,
)
from core.crypto.service import CryptoService
from core.db.models import Client, Operator
from core.redis.bus import RedisEventBus
from core.redis.media import store_transient_media
from core.repositories.clients import get_owned, list_for_operator
from core.schemas.events import OutgoingToClientEvent

router = Router(name="dialogs")
log = structlog.get_logger(__name__)


class DialogReply(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "menu:clients")
async def open_clients(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
) -> None:
    await state.clear()
    await _show_page(callback, session, operator, page=0)


@router.callback_query(F.data.startswith("clients:page:"))
async def open_page(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
) -> None:
    raw = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if not raw.isdigit():
        await callback.answer("Некорректная страница.")
        return
    await _show_page(callback, session, operator, page=int(raw))


@router.callback_query(F.data.startswith("dialog:open:"))
async def open_dialog(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
) -> None:
    client_id = _parse_uuid((callback.data or "").split(":", maxsplit=1)[1])
    if client_id is None:
        await callback.answer("Некорректный клиент.")
        return
    await _render_dialog(callback, session, operator, crypto, client_id)


@router.callback_query(F.data.startswith("dialog:reply:"))
async def start_reply(callback: CallbackQuery, state: FSMContext, session: AsyncSession, operator: Operator) -> None:
    client_id = _parse_uuid((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if client_id is None:
        await callback.answer("Некорректный клиент.")
        return
    client = await get_owned(session, client_id, operator.id)
    if client is None:
        await callback.answer("Это не ваш клиент.", show_alert=True)
        return
    await state.set_state(DialogReply.waiting)
    await state.update_data(client_id=str(client.id))
    await edit_callback_message(callback, "Отправьте текст, фото или видео для клиента.")


@router.message(DialogReply.waiting)
async def send_reply(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    redis: Redis,
    settings: Settings,
) -> None:
    data = await state.get_data()
    client_id = _parse_uuid(str(data.get("client_id", "")))
    if client_id is None:
        await state.clear()
        await message.answer("Диалог не выбран.", reply_markup=main_menu(operator.is_superadmin))
        return
    client = await get_owned(session, client_id, operator.id)
    if client is None:
        await state.clear()
        await message.answer("Это не ваш клиент.", reply_markup=main_menu(operator.is_superadmin))
        return
    text = message.text or message.caption or ""
    if len(text) > MAX_TEXT_LENGTH:
        await message.answer("Сообщение слишком длинное.")
        return
    media_file_id: str | None = None
    media_kind: MediaKind | None = None
    media_key: str | None = None
    if message.photo is not None:
        media_kind = MediaKind.PHOTO
        media_file_id = message.photo[-1].file_id
        payload = await _download(bot, media_file_id, settings.media_max_bytes)
        if payload is None:
            await message.answer("Файл слишком большой.")
            return
        media_key = await store_transient_media(redis, payload)
    elif message.video is not None:
        media_kind = MediaKind.VIDEO
        media_file_id = message.video.file_id
        payload = await _download(bot, media_file_id, settings.media_max_bytes)
        if payload is None:
            await message.answer("Файл слишком большой.")
            return
        media_key = await store_transient_media(redis, payload)
    elif not text:
        await message.answer("Отправьте текст, фото или видео.")
        return
    if client.state == ClientState.CLOSED.value:
        client.state = ClientState.IN_DIALOG.value
    message_id = await store_outgoing(
        session,
        crypto,
        client_id=client.id,
        operator_id=operator.id,
        text=text,
        media_file_id=media_file_id,
    )
    await RedisEventBus(redis).publish(
        CHANNEL_OUTGOING_TO_CLIENT,
        OutgoingToClientEvent(
            operator_id=operator.id,
            client_id=client.id,
            client_telegram_id=client.telegram_user_id,
            message_id=message_id,
            text=text or None,
            media_file_id=media_file_id,
            media_kind=media_kind,
            media_redis_key=media_key,
        ),
    )
    await state.clear()
    log.info("outgoing_queued", operator_id=str(operator.id), client_id=str(client.id), message_id=str(message_id))
    await message.answer("Ответ поставлен в отправку.", reply_markup=dialog_actions(client.id))


@router.callback_query(F.data.startswith("dialog:close:"))
async def close_dialog(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
) -> None:
    client_id = _parse_uuid((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if client_id is None:
        await callback.answer("Некорректный клиент.")
        return
    client = await get_owned(session, client_id, operator.id)
    if client is None:
        await callback.answer("Это не ваш клиент.", show_alert=True)
        return
    client.state = ClientState.CLOSED.value
    await state.clear()
    log.info("dialog_closed", operator_id=str(operator.id), client_id=str(client.id))
    await edit_callback_message(
        callback,
        "Диалог завершён. История остаётся у вас, новое сообщение клиента откроет его снова.",
        main_menu(operator.is_superadmin),
    )


async def _show_page(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    page: int,
) -> None:
    offset = page * DIALOG_PAGE_SIZE
    clients = await list_for_operator(
        session,
        operator.id,
        limit=DIALOG_PAGE_SIZE + 1,
        offset=offset,
    )
    has_next = len(clients) > DIALOG_PAGE_SIZE
    visible = clients[:DIALOG_PAGE_SIZE]
    if not visible and page == 0:
        await edit_callback_message(callback, "У вас пока нет клиентов.", main_menu(operator.is_superadmin))
        return
    rows: list[list[InlineKeyboardButton]] = []
    for client in visible:
        rows.append(
            [InlineKeyboardButton(text=_client_label(client), callback_data=f"dialog:open:{client.id}")]
        )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="←", callback_data=f"clients:page:{page - 1}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="→", callback_data=f"clients:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="В меню", callback_data="menu:main")])
    await edit_callback_message(
        callback,
        "Ваши клиенты.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _render_dialog(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
    client_id: uuid.UUID,
) -> None:
    history = await load_dialog_for_operator(
        session,
        crypto,
        client_id=client_id,
        operator_id=operator.id,
    )
    if history is None:
        await callback.answer("Это не ваш клиент.", show_alert=True)
        return
    client = await get_owned(session, client_id, operator.id)
    title = _client_label(client) if client is not None else "Клиент"
    await edit_callback_message(
        callback,
        f"{h(title)}\n\n{h(_format_history(history))}",
        dialog_actions(client_id),
    )


def _format_history(history: list[tuple[str, datetime, str]]) -> str:
    if not history:
        return "Сообщений пока нет."
    chunks: list[str] = []
    for direction, created, text in history:
        mark = "←" if direction == Direction.INCOMING.value else "→"
        stamp = created.strftime("%d.%m %H:%M")
        chunks.append(f"{mark} {stamp}\n{text or '[пусто]'}")
    rendered = "\n\n".join(chunks)
    if len(rendered) > 3500:
        return rendered[-3500:]
    return rendered


def _client_label(client: Client) -> str:
    name = f"@{client.username}" if client.username else str(client.telegram_user_id)
    return f"{name} · {client.state}"


def _parse_uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _download(bot: Bot, file_id: str, max_bytes: int) -> bytes | None:
    remote = await bot.get_file(file_id)
    if remote.file_size is not None and remote.file_size > max_bytes:
        return None
    if remote.file_path is None:
        return None
    buffer = BytesIO()
    await bot.download_file(remote.file_path, buffer)
    payload = buffer.getvalue()
    if len(payload) > max_bytes:
        return None
    return payload
