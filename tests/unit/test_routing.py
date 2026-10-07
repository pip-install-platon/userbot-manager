import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from core.constants import OperatorStatus
from core.redis.status import (
    claim_first_free,
    get_status,
    last_assigned_key,
    rank_operator_ids,
    set_status,
)


def test_rank_is_round_robin_and_honours_exclude() -> None:
    older = uuid.uuid4()
    newer = uuid.uuid4()
    fresh = uuid.uuid4()
    ordered = rank_operator_ids(
        [newer, older, fresh],
        {older: 10, newer: 50},
        exclude=set(),
    )
    assert ordered == [fresh, older, newer]
    assert rank_operator_ids([older, newer], {older: 10, newer: 50}, exclude={older}) == [newer]


@pytest.fixture
async def redis() -> Redis:
    client: Redis = Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True)
    try:
        await client.ping()
    except RedisConnectionError:
        await client.aclose()
        pytest.skip("Redis is not available on 127.0.0.1:6379")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_claim_skips_operator_who_is_no_longer_free(redis: Redis) -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    await set_status(redis, first, OperatorStatus.FREE)
    await set_status(redis, second, OperatorStatus.FREE)
    await redis.set(last_assigned_key(first), "10")
    await redis.set(last_assigned_key(second), "20")
    ordered = rank_operator_ids([first, second], {first: 10, second: 20}, set())

    claimed = await claim_first_free(redis, ordered, ttl_seconds=60)
    assert claimed == first
    assert await get_status(redis, first) == OperatorStatus.BUSY.value

    claimed_again = await claim_first_free(redis, ordered, ttl_seconds=60)
    assert claimed_again == second
    assert await claim_first_free(redis, [second], ttl_seconds=60) is None
