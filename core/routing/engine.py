import uuid

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import DEFAULT_STATUS_TTL_SECONDS, OperatorStatus
from core.db.models import Operator
from core.redis import status as operator_status
from core.repositories import operators as operator_repository

log = structlog.get_logger(__name__)


async def pick_free_operator(
    session: AsyncSession,
    redis: Redis,
    exclude: set[uuid.UUID] | None = None,
    status_ttl_seconds: int = DEFAULT_STATUS_TTL_SECONDS,
) -> Operator | None:
    blocked = set(exclude or ())
    active_ids = set(await operator_repository.list_active_ids(session))
    statuses = await operator_status.list_statuses(redis)
    free_ids = [
        operator_id
        for operator_id, value in statuses.items()
        if value == OperatorStatus.FREE.value and operator_id in active_ids and operator_id not in blocked
    ]
    last_assigned = await operator_status.last_assigned_map(redis, free_ids)
    ordered = operator_status.rank_operator_ids(free_ids, last_assigned, blocked)
    claimed_id = await operator_status.claim_first_free(redis, ordered, status_ttl_seconds)
    if claimed_id is None:
        log.info("no_free_operator")
        return None
    operator = await operator_repository.get_by_id(session, claimed_id)
    if operator is None or not operator.is_active:
        await operator_status.set_status(redis, claimed_id, OperatorStatus.PAUSED, status_ttl_seconds)
        log.warning("claimed_inactive_operator", operator_id=str(claimed_id))
        return await pick_free_operator(
            session,
            redis,
            exclude=blocked | {claimed_id},
            status_ttl_seconds=status_ttl_seconds,
        )
    log.info("operator_claimed", operator_id=str(operator.id))
    return operator
