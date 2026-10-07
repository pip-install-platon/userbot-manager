import asyncio

import structlog

from core.redis.client import open_redis
from core.redis.status import list_statuses, refresh_status_ttl, touch_heartbeat

log = structlog.get_logger(__name__)


async def heartbeat_loop(
    redis_url: str,
    *,
    status_ttl_seconds: int,
    heartbeat_ttl_seconds: int,
    interval_seconds: int,
) -> None:
    redis = open_redis(redis_url)
    try:
        while True:
            statuses = await list_statuses(redis)
            for operator_id in statuses:
                refreshed = await refresh_status_ttl(redis, operator_id, status_ttl_seconds)
                if refreshed:
                    await touch_heartbeat(redis, operator_id, heartbeat_ttl_seconds)
            log.info("heartbeat_refreshed", operators=len(statuses))
            await asyncio.sleep(interval_seconds)
    finally:
        await redis.aclose()
