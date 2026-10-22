import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from admin_bot.services.heartbeat import _persist_live, _restore_missing
from core.config import get_settings
from core.constants import (
    CHANNEL_INCOMING_CLIENT_MESSAGE,
    CHANNEL_MEETING_OFFER,
    CHANNEL_OPERATOR_ALERT,
    MediaKind,
    MeetingStatus,
    OperatorStatus,
)
from core.crypto.service import CryptoService
from core.db.base import Base
from core.db.models import Meeting, OperatorAvailability
from core.meetings.service import ClaimCode, cancel, claim, complete, confirm_call, reschedule, send_phone
from core.redis.status import get_status, set_status, status_key
from core.repositories.clients import get_by_telegram_user_id
from core.repositories.meetings import get_open, latest
from core.repositories.operators import create_operator, save_profile_ciphertext
from core.schemas.client import IncomingPrivateMessage
from core.schemas.events import IncomingClientMessageEvent, MeetingOfferEvent, OperatorAlertEvent
from core.schemas.operator import ProfileContent
from tests.fakes import FakeMessenger, RecordingBus
from userbot.services.dispatcher import UserbotDispatcher


class _BrokenExtractor:
    async def extract(self, history: list[tuple[str, str]]) -> None:
        del history
        raise RuntimeError("extractor down")


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


@pytest.fixture
async def sessions() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _operator(
    sessions: async_sessionmaker[AsyncSession],
    crypto: CryptoService,
    *,
    telegram_user_id: int,
    name: str,
    profile: ProfileContent,
) -> uuid.UUID:
    operator_id = uuid.uuid4()
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=telegram_user_id,
            display_name=name,
            profile_ciphertext=crypto.encrypt_profile(operator_id, profile),
        )
        await session.commit()
    return operator_id


def _dispatcher(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    crypto: CryptoService,
    sender: FakeMessenger,
    bus: RecordingBus,
) -> UserbotDispatcher:
    get_settings.cache_clear()
    return UserbotDispatcher(
        get_settings(),
        sessions,
        redis,
        crypto,
        sender,
        bus,
        extractor=_BrokenExtractor(),
    )


async def test_live_status_is_stored_and_restored_from_the_table(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    crypto = CryptoService(get_settings().master_key_bytes())
    operator_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=501,
        name="Анна",
        profile=ProfileContent(city="Казань"),
    )
    await set_status(redis, operator_id, OperatorStatus.FREE)
    await _persist_live(redis, sessions)
    async with sessions() as session:
        row = await session.get(OperatorAvailability, operator_id)
    assert row is not None
    assert row.status == OperatorStatus.FREE.value
    await redis.delete(status_key(operator_id))
    await _restore_missing(redis, sessions, 60)
    assert await get_status(redis, operator_id) == OperatorStatus.FREE.value


async def test_tentative_offer_reaches_only_the_free_operator(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    crypto = CryptoService(get_settings().master_key_bytes())
    free_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=501,
        name="Анна",
        profile=ProfileContent(city="Казань", phone="+79990001122", approx_address="у метро"),
    )
    paused_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=502,
        name="Борис",
        profile=ProfileContent(city="Казань", phone="+79990003344", approx_address="у парка"),
    )
    await set_status(redis, free_id, OperatorStatus.FREE)
    await set_status(redis, paused_id, OperatorStatus.PAUSED)
    sender = FakeMessenger()
    bus = RecordingBus()
    dispatcher = _dispatcher(sessions, redis, crypto, sender, bus)
    await dispatcher.handle_private(
        IncomingPrivateMessage(
            telegram_user_id=9001,
            username="client",
            message_id=1,
            text="Казань в 19:00",
            phone_number="+1 856 583 8764",
        )
    )

    offers = [event for channel, event in bus.events if channel == CHANNEL_MEETING_OFFER]
    assert len(offers) == 1
    offer = offers[0]
    assert isinstance(offer, MeetingOfferEvent)
    assert offer.operator_ids == [free_id]
    assert offer.city == "Казань"
    assert offer.when_text == "19:00"
    assert any("у метро" not in text for _chat, text in sender.texts)
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        assert client.phone_number == "+18565838764"
        assert client.username == "client"
        meeting = await get_open(session, client.id)
    assert meeting is not None
    assert meeting.status == MeetingStatus.TENTATIVE.value
    assert meeting.operator_id is None

    await set_status(redis, paused_id, OperatorStatus.FREE)
    async with sessions() as session:
        first = await claim(session, redis, crypto, meeting_id=meeting.id, operator_id=free_id)
        second = await claim(session, redis, crypto, meeting_id=meeting.id, operator_id=paused_id)
        await session.commit()
    assert first.code == ClaimCode.CLAIMED
    assert first.reply is not None
    assert "у метро" in first.reply
    assert "кружочек" in first.reply
    assert "нетрезвый" in first.reply
    assert "напишите" not in first.reply.casefold()
    assert second.code == ClaimCode.LOST


async def test_circle_after_claim_goes_to_that_operator(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    crypto = CryptoService(get_settings().master_key_bytes())
    operator_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=501,
        name="Анна",
        profile=ProfileContent(city="Казань", approx_address="у метро"),
    )
    await set_status(redis, operator_id, OperatorStatus.FREE)
    sender = FakeMessenger()
    bus = RecordingBus()
    dispatcher = _dispatcher(sessions, redis, crypto, sender, bus)
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=1, text="Казань в 19:00")
    )
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        meeting = await get_open(session, client.id)
        assert meeting is not None
        await claim(session, redis, crypto, meeting_id=meeting.id, operator_id=operator_id)
        await session.commit()
    await dispatcher.handle_private(
        IncomingPrivateMessage(
            telegram_user_id=9001,
            username="client",
            message_id=2,
            media_kind=MediaKind.VIDEO_NOTE.value,
            media_file_id="circle-1",
            media_bytes=b"circle",
        )
    )
    assert any("спасибо" in text.casefold() for _chat, text in sender.texts)
    notes = [event for channel, event in bus.events if channel == CHANNEL_INCOMING_CLIENT_MESSAGE]
    assert len(notes) == 1
    assert isinstance(notes[0], IncomingClientMessageEvent)
    assert notes[0].operator_id == operator_id
    assert notes[0].media_kind == MediaKind.VIDEO_NOTE
    assert notes[0].media_redis_key


async def test_client_confirmation_shares_phone_and_the_call_marks_busy(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=501,
        name="Анна",
        profile=ProfileContent(city="Казань", phone="+79990001122", approx_address="у метро"),
    )
    await set_status(redis, operator_id, OperatorStatus.FREE)
    sender = FakeMessenger()
    dispatcher = _dispatcher(sessions, redis, crypto, sender, RecordingBus())
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=1, text="Казань в 19:00")
    )
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        meeting = await get_open(session, client.id)
        assert meeting is not None
        taken = await claim(session, redis, crypto, meeting_id=meeting.id, operator_id=operator_id)
        await session.commit()
        meeting_id = meeting.id
    assert taken.code == ClaimCode.CLAIMED

    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=2, text="Да")
    )
    assert any("+79990001122" in text for _chat, text in sender.texts)
    async with sessions() as session:
        meeting = await get_open(session, client.id)
        assert meeting is not None
        assert meeting.status == MeetingStatus.PHONE_SHARED.value
        called = await confirm_call(
            session,
            redis,
            meeting_id=meeting_id,
            operator_id=operator_id,
            status_ttl_seconds=settings.status_ttl_seconds,
        )
        await session.commit()
        stored = await session.get(OperatorAvailability, operator_id)
    assert called.ok
    assert called.operator_status == OperatorStatus.BUSY
    assert await get_status(redis, operator_id) == OperatorStatus.BUSY.value
    assert stored is not None
    assert stored.status == OperatorStatus.BUSY.value

    async with sessions() as session:
        done = await complete(session, meeting_id=meeting_id, operator_id=operator_id)
        await session.commit()
        finished = await latest(session, client.id)
    assert done.ok
    assert finished is not None
    assert finished.status == MeetingStatus.COMPLETED.value
    assert await get_status(redis, operator_id) == OperatorStatus.BUSY.value


async def test_missing_phone_is_not_replaced_with_a_fake_number(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    crypto = CryptoService(get_settings().master_key_bytes())
    operator_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=501,
        name="Анна",
        profile=ProfileContent(city="Казань", approx_address="у метро"),
    )
    await set_status(redis, operator_id, OperatorStatus.FREE)
    sender = FakeMessenger()
    bus = RecordingBus()
    dispatcher = _dispatcher(sessions, redis, crypto, sender, bus)
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=1, text="Казань в 19:00")
    )
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        meeting = await get_open(session, client.id)
        assert meeting is not None
        await claim(session, redis, crypto, meeting_id=meeting.id, operator_id=operator_id)
        await session.commit()
        meeting_id = meeting.id
        client_id = client.id
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=2, text="Да, конечно")
    )
    confirmation = sender.texts[-1][1]
    assert "номер" in confirmation.lower()
    assert not any(char.isdigit() for char in confirmation)
    alerts = [event for channel, event in bus.events if channel == CHANNEL_OPERATOR_ALERT]
    assert len(alerts) == 1
    assert isinstance(alerts[0], OperatorAlertEvent)
    assert alerts[0].operator_id == operator_id
    async with sessions() as session:
        meeting = await get_open(session, client_id)
        assert meeting is not None
        assert meeting.client_ready is True
        assert meeting.status == MeetingStatus.ADDRESS_SENT.value
        refused = await send_phone(session, crypto, meeting_id=meeting_id, operator_id=operator_id)
        await save_profile_ciphertext(
            session,
            operator_id,
            crypto.encrypt_profile(
                operator_id,
                ProfileContent(city="Казань", phone="+79990001122", approx_address="у метро"),
            ),
        )
        sent = await send_phone(session, crypto, meeting_id=meeting_id, operator_id=operator_id)
        await session.commit()
    assert refused.ok is False
    assert sent.ok is True
    assert sent.reply is not None
    assert "+79990001122" in sent.reply


async def test_cancel_and_reschedule_are_explained_in_plain_language(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    crypto = CryptoService(get_settings().master_key_bytes())
    operator_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=501,
        name="Анна",
        profile=ProfileContent(city="Казань", approx_address="у метро"),
    )
    other_id = await _operator(
        sessions,
        crypto,
        telegram_user_id=502,
        name="Борис",
        profile=ProfileContent(city="Москва", approx_address="у парка"),
    )
    await set_status(redis, operator_id, OperatorStatus.FREE)
    await set_status(redis, other_id, OperatorStatus.FREE)
    sender = FakeMessenger()
    dispatcher = _dispatcher(sessions, redis, crypto, sender, RecordingBus())

    async def agree(telegram_user_id: int, message_id: int, text: str) -> uuid.UUID:
        await dispatcher.handle_private(
            IncomingPrivateMessage(
                telegram_user_id=telegram_user_id,
                username="client",
                message_id=message_id,
                text=text,
            )
        )
        async with sessions() as session:
            client = await get_by_telegram_user_id(session, telegram_user_id)
            assert client is not None
            meeting = await get_open(session, client.id)
            assert meeting is not None
            return meeting.id

    first_id = await agree(9001, 1, "Казань в 19:00")
    second_id = await agree(9002, 2, "Москва в 20:00")
    async with sessions() as session:
        first = await claim(session, redis, crypto, meeting_id=first_id, operator_id=operator_id)
        blocked = await claim(session, redis, crypto, meeting_id=second_id, operator_id=operator_id)
        second = await claim(session, redis, crypto, meeting_id=second_id, operator_id=other_id)
        dropped = await cancel(session, meeting_id=first_id, operator_id=operator_id)
        moved = await reschedule(session, meeting_id=second_id, operator_id=other_id)
        await session.commit()
        rows = list(await session.scalars(select(Meeting)))
    assert first.code == ClaimCode.CLAIMED
    assert blocked.code == ClaimCode.HAS_MEETING
    assert second.code == ClaimCode.CLAIMED
    assert dropped.reply is not None
    assert "отменить" in dropped.reply
    assert moved.reply is not None
    assert "перенести" in moved.reply
    by_id = {row.id: row for row in rows}
    assert by_id[first_id].status == MeetingStatus.CANCELLED.value
    assert by_id[second_id].status == MeetingStatus.DISCUSSION.value
    assert by_id[second_id].operator_id is None
    assert by_id[second_id].when_text is None
