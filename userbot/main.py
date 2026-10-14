import asyncio

import structlog
from pyrogram.sync import idle

from core.ai.openrouter import OpenRouterClient
from core.config import Settings, get_settings
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

_WARM_INTERVAL_SECONDS = 45.0


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
    model = _reply_model(settings)
    dispatcher = UserbotDispatcher(
        settings=settings,
        session_factory=session_factory,
        redis=redis,
        crypto=CryptoService(settings.master_key_bytes()),
        sender=PyrogramMessenger(client),
        bus=RedisEventBus(redis),
        model=model,
        extractor=model,
    )
    register_incoming(client, dispatcher)
    listener = asyncio.create_task(
        run_outgoing_listener(settings.redis_url, dispatcher),
        name="userbot-bus",
    )
    connection = asyncio.create_task(_hold_openrouter(model), name="openrouter-keepalive")
    await client.start()
    me = client.me
    log.info(
        "userbot_started",
        user_id=None if me is None else me.id,
        username=None if me is None else me.username,
        ai_replies=model is not None,
        openrouter_model=None if model is None else settings.openrouter_model,
    )
    try:
        await idle()
    finally:
        listener.cancel()
        connection.cancel()
        await asyncio.gather(listener, connection, return_exceptions=True)
        await client.stop()
        if model is not None:
            await model.aclose()
        await close_redis()
        await dispose_db()


async def _hold_openrouter(model: OpenRouterClient | None) -> None:
    if model is None:
        return
    while True:
        try:
            await model.warm()
        except Exception:
            log.warning("openrouter_warm_failed")
        await asyncio.sleep(_WARM_INTERVAL_SECONDS)


def _reply_model(settings: Settings) -> OpenRouterClient | None:
    if not settings.ai_replies:
        return None
    if settings.openrouter_api_key is None:
        log.warning("ai_replies_missing_key")
        return None
    return OpenRouterClient(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        timeout_seconds=settings.ai_timeout_seconds,
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
