import structlog
from pydantic import ValidationError

from core.constants import CHANNEL_OUTGOING_TO_CLIENT, CHANNEL_PROFILE_UPDATED
from core.redis.client import open_redis
from core.schemas.events import OutgoingToClientEvent, ProfileUpdatedEvent
from userbot.services.dispatcher import UserbotDispatcher

log = structlog.get_logger(__name__)


async def run_outgoing_listener(redis_url: str, dispatcher: UserbotDispatcher) -> None:
    redis = open_redis(redis_url)
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL_OUTGOING_TO_CLIENT, CHANNEL_PROFILE_UPDATED)
    try:
        async for incoming in pubsub.listen():
            if incoming.get("type") != "message":
                continue
            channel = str(incoming.get("channel", ""))
            payload = incoming.get("data")
            if not isinstance(payload, str):
                continue
            try:
                if channel == CHANNEL_OUTGOING_TO_CLIENT:
                    event = OutgoingToClientEvent.model_validate_json(payload)
                    await dispatcher.deliver_outgoing(event)
                elif channel == CHANNEL_PROFILE_UPDATED:
                    updated = ProfileUpdatedEvent.model_validate_json(payload)
                    dispatcher.invalidate_profile(updated.operator_id)
            except ValidationError:
                log.error("userbot_event_invalid", channel=channel)
            except Exception:
                log.error("userbot_event_failed", channel=channel)
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await redis.aclose()
