import asyncio

import structlog

from admin_bot.bootstrap import ensure_superadmin
from admin_bot.bot import create_bot, create_dispatcher
from admin_bot.handlers.bus_listener import listen_bus
from admin_bot.services.heartbeat import heartbeat_loop
from core.config import get_settings
from core.crypto.service import CryptoService
from core.db.engine import dispose_db, init_db, wait_for_database
from core.logging import setup_logging
from core.redis.client import close_redis, create_redis, wait_for_redis

log = structlog.get_logger(__name__)


async def _run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.bot_token is None or not settings.bot_token.get_secret_value().strip():
        raise SystemExit("admin bot requires BOT_TOKEN")
    await wait_for_database(settings)
    await wait_for_redis(settings)
    session_factory = init_db(settings)
    redis = create_redis(settings)
    crypto = CryptoService(settings.master_key_bytes())
    await ensure_superadmin(settings, session_factory, crypto)
    bot = create_bot(settings)
    dispatcher = create_dispatcher(session_factory)
    dispatcher["redis"] = redis
    dispatcher["settings"] = settings
    dispatcher["crypto"] = crypto
    heartbeat = asyncio.create_task(
        heartbeat_loop(
            settings.redis_url,
            status_ttl_seconds=settings.status_ttl_seconds,
            heartbeat_ttl_seconds=settings.heartbeat_ttl_seconds,
            interval_seconds=settings.heartbeat_interval_seconds,
        ),
        name="operator-heartbeat",
    )
    listener = asyncio.create_task(
        listen_bus(bot, settings.redis_url, session_factory),
        name="admin-bus",
    )
    log.info("admin_bot_started")
    try:
        await dispatcher.start_polling(bot)
    finally:
        heartbeat.cancel()
        listener.cancel()
        await asyncio.gather(heartbeat, listener, return_exceptions=True)
        await bot.session.close()
        await close_redis()
        await dispose_db()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
