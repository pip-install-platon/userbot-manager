import asyncio

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.constants import OperatorStatus
from core.redis.client import open_redis
from core.redis.status import get_status, list_statuses, refresh_status_ttl, set_status, touch_heartbeat
from core.repositories.operator_status import list_all, upsert_status

log = structlog.get_logger(__name__)


async def heartbeat_loop(
    redis_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status_ttl_seconds: int,
    heartbeat_ttl_seconds: int,
    interval_seconds: int,
) -> None:
    redis = open_redis(redis_url)
    try:
        while True:
            await _restore_missing(redis, session_factory, status_ttl_seconds)
            await _persist_live(redis, session_factory)
            statuses = await list_statuses(redis)
            for operator_id in statuses:
                refreshed = await refresh_status_ttl(redis, operator_id, status_ttl_seconds)
                if refreshed:
                    await touch_heartbeat(redis, operator_id, heartbeat_ttl_seconds)
            log.info("heartbeat_refreshed", operators=len(statuses))
            await asyncio.sleep(interval_seconds)
    finally:
        await redis.aclose()


async def _restore_missing(
    redis: Redis,
    session_factory: async_sessionmaker[AsyncSession],
    status_ttl_seconds: int,
) -> None:
    async with session_factory() as session:
        rows = await list_all(session)
    for row in rows:
        if await get_status(redis, row.operator_id) is not None:
            continue
        try:
            status = OperatorStatus(row.status)
        except ValueError:
            continue
        await set_status(redis, row.operator_id, status, status_ttl_seconds)


async def _persist_live(redis: Redis, session_factory: async_sessionmaker[AsyncSession]) -> None:
    live = await list_statuses(redis)
    if not live:
        return
    async with session_factory() as session:
        stored = {row.operator_id: row.status for row in await list_all(session)}
        changed = False
        for operator_id, raw in live.items():
            try:
                status = OperatorStatus(raw)
            except ValueError:
                continue
            if stored.get(operator_id) == status.value:
                continue
            await upsert_status(session, operator_id, status.value)
            changed = True
        if changed:
            await session.commit()
