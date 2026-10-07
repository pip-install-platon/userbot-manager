from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User
from sqlalchemy.ext.asyncio import AsyncSession

from core.repositories.operators import get_by_telegram_user_id

log = structlog.get_logger(__name__)
_DENIED = "Доступ только для действующих операторов."


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        session = data.get("session")
        if not isinstance(user, User) or not isinstance(session, AsyncSession):
            return None
        operator = await get_by_telegram_user_id(session, user.id)
        if operator is None or not operator.is_active:
            log.info("operator_access_denied", telegram_user_id=user.id)
            await _deny(event)
            return None
        if user.username and operator.username != user.username:
            operator.username = user.username
        data["operator"] = operator
        structlog.contextvars.bind_contextvars(operator_id=str(operator.id))
        return await handler(event, data)


async def _deny(event: TelegramObject) -> None:
    if isinstance(event, Message):
        await event.answer(_DENIED)
        return
    if isinstance(event, CallbackQuery):
        await event.answer(_DENIED, show_alert=True)
