import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from admin_bot.services.dialogs import load_dialog_for_operator
from core.config import get_settings
from core.constants import ClientState, MediaKind, OperatorStatus
from core.crypto.service import CryptoService
from core.db.base import Base
from core.db.models import Message
from core.redis.media import profile_media_key, store_profile_media
from core.redis.status import set_status
from core.repositories.clients import get_by_telegram_user_id
from core.repositories.messages import list_dialog
from core.repositories.operators import add_media, create_operator
from core.schemas.client import IncomingPrivateMessage
from core.schemas.events import IncomingClientMessageEvent, NewClientAssignedEvent
from core.schemas.operator import ProfileContent
from tests.fakes import FakeMessenger, RecordingBus
from userbot.services.dispatcher import UserbotDispatcher


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


async def test_client_message_assigns_free_operator_and_sends_profile(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = uuid.uuid4()
    other_id = uuid.uuid4()
    bio = "Уникальная анкета Анны для клиента"
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=501,
            display_name="Анна",
            profile_ciphertext=crypto.encrypt_profile(
                operator_id,
                ProfileContent(bio=bio, city="Казань"),
            ),
        )
        await create_operator(
            session,
            operator_id=other_id,
            telegram_user_id=502,
            display_name="Борис",
            profile_ciphertext=crypto.encrypt_profile(other_id, ProfileContent(bio="чужая")),
        )
        await session.commit()
    await set_status(redis, operator_id, OperatorStatus.FREE)

    sender = FakeMessenger()
    bus = RecordingBus()
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, bus)
    incoming = IncomingPrivateMessage(
        telegram_user_id=9001,
        username="client",
        message_id=77,
        text="Здравствуйте, нужна помощь",
    )
    await dispatcher.handle_private(incoming)
    await dispatcher.handle_private(incoming)

    assert sender.texts
    assert bio in sender.texts[0][1]
    assert sender.texts[0][0] == 9001
    assigned = [event for _channel, event in bus.events if isinstance(event, NewClientAssignedEvent)]
    incoming_events = [
        event for _channel, event in bus.events if isinstance(event, IncomingClientMessageEvent)
    ]
    assert len(assigned) == 1
    assert assigned[0].operator_id == operator_id
    assert len(incoming_events) == 1
    assert incoming_events[0].text == "Здравствуйте, нужна помощь"
    assert await redis.get(f"operator:{operator_id}:status") == OperatorStatus.BUSY.value

    async with sessions() as session:
        rows = await list_dialog(
            session,
            client_id=assigned[0].client_id,
            operator_id=operator_id,
            limit=10,
        )
        assert len(rows) == 1
        stored: Message = rows[0]
        assert "Здравствуйте".encode() not in stored.ciphertext
        history = await load_dialog_for_operator(
            session,
            crypto,
            client_id=assigned[0].client_id,
            operator_id=operator_id,
        )
        hidden = await load_dialog_for_operator(
            session,
            crypto,
            client_id=assigned[0].client_id,
            operator_id=other_id,
        )
    assert history is not None
    assert history[0][2] == "Здравствуйте, нужна помощь"
    assert hidden is None
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
    assert client is not None
    assert client.assigned_operator_id == operator_id
    assert client.state == ClientState.IN_DIALOG.value


async def test_photo_request_uses_assigned_operator_album(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = uuid.uuid4()
    other_id = uuid.uuid4()
    first_photo = b"photo-python-1"
    second_photo = b"photo-python-2"
    foreign_photo = b"photo-other"
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=501,
            display_name="Питон",
            profile_ciphertext=crypto.encrypt_profile(operator_id, ProfileContent(bio="анкета")),
        )
        await create_operator(
            session,
            operator_id=other_id,
            telegram_user_id=502,
            display_name="Другой",
            profile_ciphertext=crypto.encrypt_profile(other_id, ProfileContent()),
        )
        first = await add_media(
            session,
            media_id=uuid.uuid4(),
            operator_id=operator_id,
            kind=MediaKind.PHOTO.value,
            file_id="file-1",
            caption_ciphertext=None,
            position=0,
        )
        second = await add_media(
            session,
            media_id=uuid.uuid4(),
            operator_id=operator_id,
            kind=MediaKind.PHOTO.value,
            file_id="file-2",
            caption_ciphertext=None,
            position=1,
        )
        foreign = await add_media(
            session,
            media_id=uuid.uuid4(),
            operator_id=other_id,
            kind=MediaKind.PHOTO.value,
            file_id="file-foreign",
            caption_ciphertext=None,
            position=0,
        )
        await session.commit()
    await store_profile_media(redis, first.id, first_photo)
    await store_profile_media(redis, second.id, second_photo)
    await store_profile_media(redis, foreign.id, foreign_photo)
    await set_status(redis, operator_id, OperatorStatus.FREE)

    sender = FakeMessenger()
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, RecordingBus())

    async def say(message_id: int, text: str) -> None:
        await dispatcher.handle_private(
            IncomingPrivateMessage(
                telegram_user_id=9001,
                username="client",
                message_id=message_id,
                text=text,
            )
        )

    await say(1, "Здравствуйте")
    assert sender.photos == []
    assert any("анкета" in text for _chat, text in sender.texts)

    await say(2, "скинь фото")
    await say(3, "ещё фото")
    assert [item[1] for item in sender.photos] == [first_photo, second_photo]
    assert all(item[1] != foreign_photo for item in sender.photos)
    assert await redis.get(profile_media_key(first.id))

    await say(4, "кружочек")
    assert any("пока нет кружков" in text for _chat, text in sender.texts)
    assert sender.video_notes == []


async def test_busy_pool_replies_without_assignment(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    sender = FakeMessenger()
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, RecordingBus())
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=42, username=None, message_id=1, text="алло")
    )
    assert sender.texts == [(42, settings.all_operators_busy_text)]
