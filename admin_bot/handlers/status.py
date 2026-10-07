import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery
from redis.asyncio import Redis

from admin_bot.keyboards.main_menu import main_menu
from admin_bot.ui import edit_callback_message
from core.constants import CHANNEL_OPERATOR_STATUS_CHANGED, OperatorStatus
from core.db.models import Operator
from core.redis.bus import RedisEventBus
from core.redis.status import set_status
from core.schemas.events import OperatorStatusChangedEvent

router = Router(name="status")
log = structlog.get_logger(__name__)

_LABELS = {
    OperatorStatus.FREE: "Вы свободны и можете получать новых клиентов.",
    OperatorStatus.BUSY: "Вы заняты. Новые клиенты вам не назначаются.",
    OperatorStatus.PAUSED: "Пауза. Новые клиенты вам не назначаются.",
}


@router.callback_query(F.data.startswith("status:"))
async def change_status(callback: CallbackQuery, operator: Operator, redis: Redis) -> None:
    raw = (callback.data or "").split(":", maxsplit=1)[1]
    try:
        status = OperatorStatus(raw)
    except ValueError:
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
    await edit_callback_message(callback, _LABELS[status], main_menu(operator.is_superadmin))
