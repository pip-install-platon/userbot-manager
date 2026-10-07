import time
import uuid
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from core.constants import DEFAULT_STATUS_TTL_SECONDS, OperatorStatus

_CLAIM_SCRIPT = """
local ttl = tonumber(ARGV[1])
local now = ARGV[2]
for i = 1, #KEYS, 2 do
  local status_key = KEYS[i]
  local assigned_key = KEYS[i + 1]
  if redis.call('GET', status_key) == 'free' then
    redis.call('SET', status_key, 'busy', 'EX', ttl)
    redis.call('SET', assigned_key, now)
    return status_key
  end
end
return false
"""

_REFRESH_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
  return 1
end
return 0
"""


def status_key(operator_id: uuid.UUID) -> str:
    return f"operator:{operator_id}:status"


def last_assigned_key(operator_id: uuid.UUID) -> str:
    return f"operator:{operator_id}:last_assigned"


def heartbeat_key(operator_id: uuid.UUID) -> str:
    return f"operator:{operator_id}:heartbeat"


def parse_status_key(key: str) -> uuid.UUID:
    body = key.removeprefix("operator:").removesuffix(":status")
    return uuid.UUID(body)


async def set_status(
    redis: Redis,
    operator_id: uuid.UUID,
    status: OperatorStatus,
    ttl_seconds: int = DEFAULT_STATUS_TTL_SECONDS,
) -> None:
    await redis.set(status_key(operator_id), status.value, ex=ttl_seconds)


async def get_status(redis: Redis, operator_id: uuid.UUID) -> str | None:
    return await redis.get(status_key(operator_id))


async def list_statuses(redis: Redis) -> dict[uuid.UUID, str]:
    found: dict[uuid.UUID, str] = {}
    async for key in redis.scan_iter(match="operator:*:status", count=200):
        value = await redis.get(key)
        if value is None:
            continue
        found[parse_status_key(str(key))] = str(value)
    return found


async def last_assigned_map(redis: Redis, operator_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not operator_ids:
        return {}
    keys = [last_assigned_key(operator_id) for operator_id in operator_ids]
    values = await redis.mget(keys)
    mapped: dict[uuid.UUID, int] = {}
    for operator_id, raw in zip(operator_ids, values, strict=True):
        if raw is None:
            continue
        mapped[operator_id] = int(raw)
    return mapped


async def claim_first_free(
    redis: Redis,
    ordered_ids: list[uuid.UUID],
    ttl_seconds: int,
) -> uuid.UUID | None:
    if not ordered_ids:
        return None
    keys: list[str] = []
    for operator_id in ordered_ids:
        keys.append(status_key(operator_id))
        keys.append(last_assigned_key(operator_id))
    claimed = await cast(
        Awaitable[object],
        redis.eval(
            _CLAIM_SCRIPT,
            len(keys),
            *keys,
            str(ttl_seconds),
            str(int(time.time())),
        ),
    )
    if not claimed:
        return None
    return parse_status_key(str(claimed))


async def refresh_status_ttl(redis: Redis, operator_id: uuid.UUID, ttl_seconds: int) -> bool:
    refreshed = await cast(
        Awaitable[object],
        redis.eval(
            _REFRESH_SCRIPT,
            1,
            status_key(operator_id),
            str(ttl_seconds),
        ),
    )
    return bool(refreshed)


async def touch_heartbeat(redis: Redis, operator_id: uuid.UUID, ttl_seconds: int) -> None:
    await redis.set(heartbeat_key(operator_id), str(int(time.time())), ex=ttl_seconds)


def rank_operator_ids(
    operator_ids: list[uuid.UUID],
    last_assigned: dict[uuid.UUID, int],
    exclude: set[uuid.UUID],
) -> list[uuid.UUID]:
    visible = [operator_id for operator_id in operator_ids if operator_id not in exclude]
    return sorted(visible, key=lambda operator_id: last_assigned.get(operator_id, 0))
