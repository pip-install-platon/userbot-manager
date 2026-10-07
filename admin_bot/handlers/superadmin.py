import uuid

import structlog
from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from admin_bot.ui import edit_callback_message, h
from core.constants import OperatorStatus
from core.crypto.service import CryptoService
from core.db.models import Operator
from core.redis.status import get_status, set_status
from core.repositories.operators import create_operator, list_public_summaries, set_active
from core.schemas.operator import ProfileContent

router = Router(name="superadmin")
log = structlog.get_logger(__name__)


class SuperadminFilter(BaseFilter):
    async def __call__(self, event: TelegramObject, operator: Operator) -> bool:
        return operator.is_superadmin


router.message.filter(SuperadminFilter())
router.callback_query.filter(SuperadminFilter())


class AddOperator(StatesGroup):
    telegram_id = State()
    display_name = State()


@router.callback_query(F.data == "menu:superadmin")
async def open_panel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_callback_message(callback, "Операторы. Анкеты и переписки отсюда не открываются.", _panel())


@router.callback_query(F.data == "sa:add")
async def ask_telegram_id(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddOperator.telegram_id)
    await edit_callback_message(callback, "Отправьте числовой Telegram ID нового оператора.")


@router.message(AddOperator.telegram_id)
async def capture_telegram_id(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Нужен положительный числовой Telegram ID.")
        return
    await state.update_data(telegram_id=int(raw))
    await state.set_state(AddOperator.display_name)
    await message.answer("Отправьте отображаемое имя оператора.")


@router.message(AddOperator.display_name)
async def create_new_operator(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    operator: Operator,
    crypto: CryptoService,
) -> None:
    data = await state.get_data()
    telegram_id = data.get("telegram_id")
    display_name = (message.text or "").strip()
    if not isinstance(telegram_id, int) or not display_name or len(display_name) > 128:
        await message.answer("Имя должно быть непустым и короче 128 символов.")
        return
    operator_id = uuid.uuid4()
    try:
        async with session.begin_nested():
            await create_operator(
                session,
                operator_id=operator_id,
                telegram_user_id=telegram_id,
                display_name=display_name,
                profile_ciphertext=crypto.encrypt_profile(operator_id, ProfileContent()),
                is_superadmin=False,
            )
    except IntegrityError:
        await message.answer("Оператор с таким Telegram ID уже есть.")
        return
    await state.clear()
    log.info("operator_created", actor_id=str(operator.id), operator_id=str(operator_id))
    await message.answer("Оператор добавлен. Он должен нажать /start у этого бота.", reply_markup=_panel())


@router.callback_query(F.data == "sa:list")
async def list_operators(
    callback: CallbackQuery,
    session: AsyncSession,
    redis: Redis,
) -> None:
    summaries = await list_public_summaries(session)
    if not summaries:
        await edit_callback_message(callback, "Операторов нет.", _panel())
        return
    lines: list[str] = []
    rows: list[list[InlineKeyboardButton]] = []
    for summary in summaries:
        status = await get_status(redis, summary.id) or "offline"
        active = "активен" if summary.is_active else "выключен"
        lines.append(
            f"{h(summary.display_name)} · {summary.telegram_user_id} · {h(status)} · {active}"
        )
        if summary.is_active:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Выключить {summary.display_name}",
                        callback_data=f"sa:off:{summary.id}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:superadmin")])
    await edit_callback_message(
        callback,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("sa:off:"))
async def deactivate(
    callback: CallbackQuery,
    session: AsyncSession,
    operator: Operator,
    redis: Redis,
) -> None:
    raw = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    try:
        target_id = uuid.UUID(raw)
    except ValueError:
        await callback.answer("Некорректный оператор.")
        return
    if target_id == operator.id:
        await callback.answer("Нельзя выключить собственную учётную запись.", show_alert=True)
        return
    await set_active(session, target_id, False)
    await set_status(redis, target_id, OperatorStatus.PAUSED)
    log.info("operator_deactivated", actor_id=str(operator.id), operator_id=str(target_id))
    await edit_callback_message(callback, "Оператор выключен.", _panel())


def _panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить оператора", callback_data="sa:add")],
            [InlineKeyboardButton(text="Список и статусы", callback_data="sa:list")],
            [InlineKeyboardButton(text="В меню", callback_data="menu:main")],
        ]
    )
