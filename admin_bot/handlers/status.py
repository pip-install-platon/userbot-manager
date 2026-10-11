import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis

from admin_bot.keyboards.main_menu import STATUS_TITLE, main_menu, parse_status, shift_status
from admin_bot.ui import edit_callback_message, h
from core.constants import CHANNEL_OPERATOR_STATUS_CHANGED, OperatorStatus
from core.db.models import Operator
from core.redis.bus import RedisEventBus
from core.redis.status import get_status, set_status
from core.schemas.events import OperatorStatusChangedEvent

router = Router(name="status")
log = structlog.get_logger(__name__)

_DIRECT = {
    "free": OperatorStatus.FREE,
    "busy": OperatorStatus.BUSY,
    "paused": OperatorStatus.PAUSED,
}


@router.callback_query(F.data == "status:current")
async def show_current_status(callback: CallbackQuery, operator: Operator, redis: Redis) -> None:
    status = parse_status(await get_status(redis, operator.id))
    await callback.answer(STATUS_TITLE[status])


@router.callback_query(F.data.startswith("status:"))
async def change_status(callback: CallbackQuery, operator: Operator, redis: Redis) -> None:
    raw = (callback.data or "").split(":", maxsplit=1)[1]
    current = parse_status(await get_status(redis, operator.id))
    if raw == "prev":
        status = shift_status(current, -1)
    elif raw == "next":
        status = shift_status(current, 1)
    elif raw in _DIRECT:
        status = _DIRECT[raw]
    else:
        await callback.answer("Неизвестный статус.")
        return
    await set_status(redis, operator.id, status)
    try:
        await RedisEventBus(redis).publish(
            CHANNEL_OPERATOR_STATUS_CHANGED,
            OperatorStatusChangedEvent(operator_id=operator.id, status=status),
        )
    except Exception:
        log.error("status_event_failed", operator_id=str(operator.id))
    log.info("operator_status_changed", operator_id=str(operator.id), status=status.value)
    message = callback.message
    greeting = f"Здравствуйте, {h(operator.display_name)}."
    text = message.text if isinstance(message, Message) and message.text else greeting
    await edit_callback_message(
        callback,
        text,
        main_menu(operator.is_superadmin, status),
        notice=f"Статус: {STATUS_TITLE[status]}",
    )
