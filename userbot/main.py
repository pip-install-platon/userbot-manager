import asyncio

import structlog
from pyrogram.sync import idle

from core.config import get_settings
from core.crypto.service import CryptoService
from core.db.engine import dispose_db, init_db, wait_for_database
from core.logging import setup_logging
from core.redis.bus import RedisEventBus
from core.redis.client import close_redis, create_redis, wait_for_redis
from userbot.client import create_userbot
from userbot.handlers.incoming import register_incoming
from userbot.handlers.outgoing import run_outgoing_listener
from userbot.services.dispatcher import UserbotDispatcher
from userbot.services.sender import PyrogramMessenger

log = structlog.get_logger(__name__)


async def _run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.api_id is None or settings.api_hash is None or settings.session_string is None:
        raise SystemExit("userbot requires API_ID, API_HASH and SESSION_STRING")
    await wait_for_database(settings)
    await wait_for_redis(settings)
    session_factory = init_db(settings)
    redis = create_redis(settings)
    client = create_userbot(settings)
    dispatcher = UserbotDispatcher(
        settings=settings,
        session_factory=session_factory,
        redis=redis,
        crypto=CryptoService(settings.master_key_bytes()),
        sender=PyrogramMessenger(client),
        bus=RedisEventBus(redis),
    )
    register_incoming(client, dispatcher)
    listener = asyncio.create_task(
        run_outgoing_listener(settings.redis_url, dispatcher),
        name="userbot-bus",
    )
    await client.start()
    log.info("userbot_started")
    try:
        await idle()
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        await client.stop()
        await close_redis()
        await dispose_db()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
