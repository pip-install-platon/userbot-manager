from pydantic import BaseModel
from redis.asyncio import Redis
from tenacity import retry, stop_after_attempt, wait_exponential


class RedisEventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, event: BaseModel) -> None:
        await _publish(self._redis, channel, event.model_dump_json())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2),
    reraise=True,
)
async def _publish(redis: Redis, channel: str, payload: str) -> None:
    await redis.publish(channel, payload)
