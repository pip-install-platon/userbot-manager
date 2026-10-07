import base64
import uuid

from redis.asyncio import Redis

from core.constants import TRANSIENT_MEDIA_TTL_SECONDS


def transient_media_key() -> str:
    return f"media:{uuid.uuid4()}"


def profile_media_key(media_id: uuid.UUID) -> str:
    return f"profile_media:{media_id}"


async def store_transient_media(redis: Redis, payload: bytes) -> str:
    key = transient_media_key()
    await redis.set(key, base64.b64encode(payload).decode(), ex=TRANSIENT_MEDIA_TTL_SECONDS)
    return key


async def store_profile_media(redis: Redis, media_id: uuid.UUID, payload: bytes) -> str:
    key = profile_media_key(media_id)
    await redis.set(key, base64.b64encode(payload).decode())
    return key


async def load_media(redis: Redis, key: str) -> bytes | None:
    raw = await redis.get(key)
    if raw is None:
        return None
    return base64.b64decode(raw)


async def delete_media(redis: Redis, key: str) -> None:
    await redis.delete(key)
