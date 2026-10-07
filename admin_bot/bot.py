import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_bot.handlers import dialogs, profile, start, status, superadmin
from admin_bot.middlewares.auth import AuthMiddleware
from admin_bot.middlewares.db import DbSessionMiddleware
from core.config import Settings

log = structlog.get_logger(__name__)


def create_bot(settings: Settings) -> Bot:
    if settings.bot_token is None or not settings.bot_token.get_secret_value().strip():
        raise RuntimeError("BOT_TOKEN is required for the admin bot")
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(session_factory: async_sessionmaker[AsyncSession]) -> Dispatcher:
    dispatcher = Dispatcher()
    session_middleware = DbSessionMiddleware(session_factory)
    auth_middleware = AuthMiddleware()
    dispatcher.message.middleware(session_middleware)
    dispatcher.message.middleware(auth_middleware)
    dispatcher.callback_query.middleware(session_middleware)
    dispatcher.callback_query.middleware(auth_middleware)
    dispatcher.include_router(status.router)
    dispatcher.include_router(profile.router)
    dispatcher.include_router(dialogs.router)
    dispatcher.include_router(superadmin.router)
    dispatcher.include_router(start.router)

    @dispatcher.error()
    async def on_error(event: ErrorEvent) -> None:
        log.error("admin_update_failed", error_type=type(event.exception).__name__)

    return dispatcher
