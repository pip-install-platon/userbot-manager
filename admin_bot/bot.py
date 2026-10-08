import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.event.telegram import TelegramEventObserver
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


def _bind_operator_context(
    observer: TelegramEventObserver,
    session_middleware: DbSessionMiddleware,
    auth_middleware: AuthMiddleware,
) -> None:
    # Outer middleware runs before router filters. The first one registered runs first,
    # so the database session exists when auth loads the operator for SuperadminFilter.
    observer.outer_middleware(session_middleware)
    observer.outer_middleware(auth_middleware)


def create_dispatcher(session_factory: async_sessionmaker[AsyncSession]) -> Dispatcher:
    dispatcher = Dispatcher()
    session_middleware = DbSessionMiddleware(session_factory)
    auth_middleware = AuthMiddleware()
    _bind_operator_context(dispatcher.message, session_middleware, auth_middleware)
    _bind_operator_context(dispatcher.callback_query, session_middleware, auth_middleware)
    dispatcher.include_router(status.router)
    dispatcher.include_router(profile.router)
    dispatcher.include_router(dialogs.router)
    dispatcher.include_router(superadmin.router)
    dispatcher.include_router(start.router)

    @dispatcher.error()
    async def on_error(event: ErrorEvent) -> None:
        log.error(
            "admin_update_failed",
            error_type=type(event.exception).__name__,
            error=str(event.exception),
        )

    return dispatcher
