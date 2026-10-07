import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
        try:
            async with self._session_factory() as session:
                data["session"] = session
                try:
                    result = await handler(event, data)
                except Exception as exc:
                    await session.rollback()
                    log.error("handler_failed", error_type=type(exc).__name__)
                    raise
                else:
                    await session.commit()
                    return result
        finally:
            structlog.contextvars.clear_contextvars()
