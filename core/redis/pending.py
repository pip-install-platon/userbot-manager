from redis.asyncio import Redis

from core.constants import PENDING_ASSIGNMENT_TTL_SECONDS
from core.schemas.client import PendingAssignment


def pending_key(telegram_user_id: int) -> str:
    return f"pending_assignment:{telegram_user_id}"


async def save_pending(redis: Redis, telegram_user_id: int, pending: PendingAssignment) -> None:
    await redis.set(
        pending_key(telegram_user_id),
        pending.model_dump_json(),
        ex=PENDING_ASSIGNMENT_TTL_SECONDS,
    )


async def load_pending(redis: Redis, telegram_user_id: int) -> PendingAssignment | None:
    raw = await redis.get(pending_key(telegram_user_id))
    if raw is None:
        return None
    return PendingAssignment.model_validate_json(raw)


async def delete_pending(redis: Redis, telegram_user_id: int) -> None:
    await redis.delete(pending_key(telegram_user_id))
