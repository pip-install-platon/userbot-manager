import uuid
from typing import Protocol, assert_never

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from admin_bot.keyboards.meetings import meeting_keyboard, meetings_list
from admin_bot.services.dialogs import store_outgoing
from admin_bot.services.menus import operator_main_menu
from admin_bot.ui import edit_callback_message, h
from core.config import Settings
from core.constants import CHANNEL_OPERATOR_STATUS_CHANGED, CHANNEL_OUTGOING_TO_CLIENT, MeetingStatus
from core.crypto.service import CryptoService
from core.db.models import Meeting, Operator
from core.meetings.phrases import meeting_card, status_title
from core.meetings.service import (
    ClaimCode,
    ClaimResult,
    MeetingAction,
    cancel,
    claim,
    complete,
    confirm_call,
    reschedule,
    resend_address,
    send_phone,
)
from core.redis.bus import RedisEventBus
from core.repositories import clients as client_repository
from core.repositories import meetings as meeting_repository
from core.schemas.events import OperatorStatusChangedEvent, OutgoingToClientEvent

router = Router(name="meetings")
log = structlog.get_logger(__name__)


class _ProfileAction(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        crypto: CryptoService,
        *,
        meeting_id: uuid.UUID,
        operator_id: uuid.UUID,
    ) -> MeetingAction: ...


class _ReleaseAction(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        meeting_id: uuid.UUID,
        operator_id: uuid.UUID,
    ) -> MeetingAction: ...


@router.callback_query(F.data == "menu:meetings")
async def open_meetings(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
) -> None:
    await state.clear()
    rows = await meeting_repository.list_owned_open(session, operator.id)
    if not rows:
        await edit_callback_message(
            callback,
            "Открытых встреч нет. Когда появится заказ, свободным придёт кнопка «Принять».",
            await operator_main_menu(redis, operator),
        )
        return
    items: list[tuple[uuid.UUID, str]] = []
    for meeting in rows:
        client = await client_repository.get_by_id(session, meeting.client_id)
        who = f"@{client.username}" if client is not None and client.username else "клиент"
        items.append((meeting.id, f"{who} · {meeting.city or 'город'} · {status_title(MeetingStatus(meeting.status))}"))
    await edit_callback_message(callback, "Ваши встречи.", meetings_list(items))


@router.callback_query(F.data.startswith("meet:open:"))
async def open_meeting(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
) -> None:
    meeting = await _owned_from_callback(callback, session, operator.id)
    if meeting is None:
        return
    await _show(callback, session, meeting)


@router.callback_query(F.data.startswith("meet:accept:"))
async def accept_meeting(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
) -> None:
    meeting_id = _parse((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if meeting_id is None:
        await callback.answer("Некорректная встреча.")
        return
    result = await claim(
        session,
        redis,
        crypto,
        meeting_id=meeting_id,
        operator_id=operator.id,
    )
    if result.code != ClaimCode.CLAIMED:
        await callback.answer(_claim_notice(result), show_alert=True)
        return
    await _publish_reply(session, redis, crypto, operator, result.reply, result.client_id, result.client_telegram_id)
    meeting = await meeting_repository.get_by_id(session, meeting_id)
    if meeting is None:
        await callback.answer(result.notice)
        return
    log.info("meeting_claimed", operator_id=str(operator.id), meeting_id=str(meeting.id))
    await _show(callback, session, meeting, notice=result.notice)


@router.callback_query(F.data.startswith("meet:address:"))
async def send_address(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
) -> None:
    await _act(callback, session, operator, redis, crypto, resend_address)


@router.callback_query(F.data.startswith("meet:phone:"))
async def share_phone(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
) -> None:
    await _act(callback, session, operator, redis, crypto, send_phone)


@router.callback_query(F.data.startswith("meet:call:"))
async def mark_called(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    settings: Settings,
) -> None:
    meeting_id = _parse((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if meeting_id is None:
        await callback.answer("Некорректная встреча.")
        return
    action = await confirm_call(
        session,
        redis,
        meeting_id=meeting_id,
        operator_id=operator.id,
        status_ttl_seconds=settings.status_ttl_seconds,
    )
    if not action.ok:
        await callback.answer(action.notice, show_alert=True)
        return
    if action.operator_status is not None:
        await RedisEventBus(redis).publish(
            CHANNEL_OPERATOR_STATUS_CHANGED,
            OperatorStatusChangedEvent(operator_id=operator.id, status=action.operator_status),
        )
    log.info("meeting_confirmed", operator_id=str(operator.id), meeting_id=str(meeting_id))
    await _show_action(callback, session, meeting_id, action.notice)


@router.callback_query(F.data.startswith("meet:done:"))
async def finish_meeting(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
) -> None:
    meeting_id = _parse((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if meeting_id is None:
        await callback.answer("Некорректная встреча.")
        return
    action = await complete(session, meeting_id=meeting_id, operator_id=operator.id)
    if not action.ok:
        await callback.answer(action.notice, show_alert=True)
        return
    log.info("meeting_completed", operator_id=str(operator.id), meeting_id=str(meeting_id))
    await edit_callback_message(callback, action.notice, await operator_main_menu(redis, operator))


@router.callback_query(F.data.startswith("meet:move:"))
async def move_meeting(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
) -> None:
    await _release(callback, session, operator, redis, crypto, reschedule)


@router.callback_query(F.data.startswith("meet:drop:"))
async def drop_meeting(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
) -> None:
    await _release(callback, session, operator, redis, crypto, cancel)


async def _act(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
    action: _ProfileAction,
) -> None:
    meeting_id = _parse((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if meeting_id is None:
        await callback.answer("Некорректная встреча.")
        return
    result = await action(session, crypto, meeting_id=meeting_id, operator_id=operator.id)
    if not result.ok:
        await callback.answer(result.notice, show_alert=True)
        return
    await _publish_reply(
        session,
        redis,
        crypto,
        operator,
        result.reply,
        result.client_id,
        result.client_telegram_id,
    )
    await _show_action(callback, session, meeting_id, result.notice)


async def _release(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
    crypto: CryptoService,
    action: _ReleaseAction,
) -> None:
    meeting_id = _parse((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if meeting_id is None:
        await callback.answer("Некорректная встреча.")
        return
    result = await action(session, meeting_id=meeting_id, operator_id=operator.id)
    if not result.ok:
        await callback.answer(result.notice, show_alert=True)
        return
    await _publish_reply(
        session,
        redis,
        crypto,
        operator,
        result.reply,
        result.client_id,
        result.client_telegram_id,
    )
    log.info("meeting_released", operator_id=str(operator.id), meeting_id=str(meeting_id))
    await edit_callback_message(callback, result.notice, await operator_main_menu(redis, operator))


async def _show_action(
    callback: CallbackQuery,
    session: AsyncSession,
    meeting_id: uuid.UUID,
    notice: str,
) -> None:
    meeting = await meeting_repository.get_by_id(session, meeting_id)
    if meeting is None:
        await callback.answer(notice)
        return
    await _show(callback, session, meeting, notice=notice)


async def _show(
    callback: CallbackQuery,
    session: AsyncSession,
    meeting: Meeting,
    notice: str | None = None,
) -> None:
    client = await client_repository.get_by_id(session, meeting.client_id)
    username = None if client is None else client.username
    telegram_user_id = 0 if client is None else client.telegram_user_id
    status = MeetingStatus(meeting.status)
    await edit_callback_message(
        callback,
        h(
            meeting_card(
                username=username,
                telegram_user_id=telegram_user_id,
                status=status,
                city=meeting.city,
                when_text=meeting.when_text,
                client_ready=meeting.client_ready,
            )
        ),
        meeting_keyboard(meeting.id, status, client_ready=meeting.client_ready),
        notice=notice,
    )


async def _owned_from_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    operator_id: uuid.UUID,
) -> Meeting | None:
    meeting_id = _parse((callback.data or "").rsplit(":", maxsplit=1)[-1])
    if meeting_id is None:
        await callback.answer("Некорректная встреча.")
        return None
    meeting = await meeting_repository.get_by_id(session, meeting_id)
    if meeting is None or meeting.operator_id != operator_id:
        await callback.answer("Это не ваша встреча.", show_alert=True)
        return None
    return meeting


async def _publish_reply(
    session: AsyncSession,
    redis: Redis,
    crypto: CryptoService,
    operator: Operator,
    reply: str | None,
    client_id: uuid.UUID | None,
    client_telegram_id: int | None,
) -> None:
    if reply is None or client_id is None or client_telegram_id is None:
        return
    message_id = await store_outgoing(
        session,
        crypto,
        client_id=client_id,
        operator_id=operator.id,
        text=reply,
        media_file_id=None,
    )
    await RedisEventBus(redis).publish(
        CHANNEL_OUTGOING_TO_CLIENT,
        OutgoingToClientEvent(
            operator_id=operator.id,
            client_id=client_id,
            client_telegram_id=client_telegram_id,
            message_id=message_id,
            text=reply,
        ),
    )


def _claim_notice(result: ClaimResult) -> str:
    match result.code:
        case ClaimCode.CLAIMED:
            return result.notice
        case ClaimCode.LOST:
            return "Заказ уже забрали."
        case ClaimCode.NOT_FREE:
            return "Принять заказ можно только со статусом «Свободен»."
        case ClaimCode.HAS_MEETING:
            return "Сначала завершите текущую встречу."
        case ClaimCode.MISSING:
            return "Встреча не найдена."
        case ClaimCode.INACTIVE:
            return "Оператор недоступен."
        case _ as unreachable:
            assert_never(unreachable)


def _parse(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
