from aiogram.types import InlineKeyboardMarkup
from redis.asyncio import Redis

from admin_bot.keyboards.main_menu import main_menu, parse_status
from core.db.models import Operator
from core.redis.status import get_status


async def operator_main_menu(redis: Redis, operator: Operator) -> InlineKeyboardMarkup:
    raw = await get_status(redis, operator.id)
    return main_menu(operator.is_superadmin, parse_status(raw))
