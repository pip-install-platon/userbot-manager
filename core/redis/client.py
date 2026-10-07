from redis.asyncio import Redis
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import Settings

_redis: Redis | None = None


def create_redis(settings: Settings) -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def open_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


async def wait_for_redis(settings: Settings) -> None:
    client = open_redis(settings.redis_url)

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    async def _ping() -> None:
        await client.ping()

    try:
        await _ping()
    finally:
        await client.aclose()
