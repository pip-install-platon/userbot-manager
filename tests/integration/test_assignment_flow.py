import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from admin_bot.services.dialogs import load_dialog_for_operator
from core.ai.persona import ChatTurn
from core.config import get_settings
from core.constants import CHANNEL_AI_REPLY, ClientState, Direction, MediaKind, OperatorStatus
from core.crypto.service import CryptoService
from core.db.base import Base
from core.db.models import Client
from core.redis.media import profile_media_key, store_profile_media
from core.redis.status import set_status
from core.repositories.clients import get_by_telegram_user_id
from core.repositories.messages import list_dialog, list_recent_desk
from core.repositories.operators import add_media, create_operator
from core.schemas.client import IncomingPrivateMessage
from core.schemas.events import AiReplyEvent, IncomingClientMessageEvent, NewClientAssignedEvent
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


async def _bind_client(
    sessions: async_sessionmaker[AsyncSession],
    *,
    telegram_user_id: int,
    username: str,
    operator_id: uuid.UUID,
) -> None:
    async with sessions() as session:
        client = Client(
            id=uuid.uuid4(),
            telegram_user_id=telegram_user_id,
            username=username,
            assigned_operator_id=operator_id,
            state=ClientState.IN_DIALOG.value,
        )
        session.add(client)
        await session.commit()


async def test_client_message_is_stored_before_an_operator_is_claimed(
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
    assert sender.texts[0][0] == 9001
    assert "приветик" in sender.texts[0][1].casefold()
    assert "12000" in sender.texts[0][1]
    assert "20000" in sender.texts[0][1]
    assert not any(isinstance(event, NewClientAssignedEvent) for _channel, event in bus.events)
    assert not any(isinstance(event, IncomingClientMessageEvent) for _channel, event in bus.events)
    assert await redis.get(f"operator:{operator_id}:status") == OperatorStatus.FREE.value

    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        rows = await list_dialog(
            session,
            client_id=client.id,
            operator_id=operator_id,
            limit=10,
        )
        hidden = await load_dialog_for_operator(
            session,
            crypto,
            client_id=client.id,
            operator_id=other_id,
        )
    assert rows == []
    assert hidden is None
    assert client.username == "client"
    assert client.phone_number is None
    assert client.assigned_operator_id is None
    assert client.state == ClientState.NEW.value
    assert bio not in sender.texts[0][1]


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
    await _bind_client(sessions, telegram_user_id=9001, username="client", operator_id=operator_id)

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

    await say(2, "скинь фото")
    await say(3, "ещё фото")
    assert [item[1] for item in sender.photos] == [first_photo, second_photo]
    assert all(item[1] != foreign_photo for item in sender.photos)
    assert await redis.get(profile_media_key(first.id))

    await say(4, "кружочек")
    assert any("кружков пока нет" in text.casefold() for _chat, text in sender.texts)
    assert sender.video_notes == []


class _ScriptedModel:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.seen: list[list[ChatTurn]] = []

    async def complete(self, messages: list[ChatTurn]) -> str:
        self.seen.append(list(messages))
        return self._replies.pop(0)


class _FailingModel:
    async def complete(self, messages: list[ChatTurn]) -> str:
        raise RuntimeError("provider down")


async def test_text_reply_uses_openrouter_persona(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = uuid.uuid4()
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=501,
            display_name="Анна",
            profile_ciphertext=crypto.encrypt_profile(
                operator_id,
                ProfileContent(bio="Люблю ночные прогулки", city="Казань"),
            ),
        )
        await session.commit()
    await set_status(redis, operator_id, OperatorStatus.FREE)
    await _bind_client(sessions, telegram_user_id=9001, username="client", operator_id=operator_id)
    sender = FakeMessenger()
    bus = RecordingBus()
    model = _ScriptedModel(["Рада знакомству", "Тогда до вечера"])
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, bus, model=model)

    async def say(message_id: int, text: str) -> None:
        await dispatcher.handle_private(
            IncomingPrivateMessage(
                telegram_user_id=9001,
                username="client",
                message_id=message_id,
                text=text,
            )
        )

    await say(1, "Привет")
    await say(2, "скинь фото")
    await say(3, "Что делаешь?")

    assert [text for _chat, text in sender.texts if text in {"Рада знакомству", "Тогда до вечера"}] == [
        "Рада знакомству",
        "Тогда до вечера",
    ]
    assert len(model.seen) == 2
    assert "Люблю ночные прогулки" in model.seen[0][0].content
    assert model.seen[1][-1] == ChatTurn(role="user", content="Что делаешь?")
    assert any(turn.role == "assistant" and turn.content == "Рада знакомству" for turn in model.seen[1])
    replies = [event for channel, event in bus.events if channel == CHANNEL_AI_REPLY]
    assert len(replies) == 2
    assert all(isinstance(event, AiReplyEvent) for event in replies)
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        rows = await list_dialog(
            session,
            client_id=client.id,
            operator_id=operator_id,
            limit=10,
        )
    outgoing = [row for row in rows if row.direction == Direction.OUTGOING.value]
    assert len(outgoing) == 2


async def test_model_failure_leaves_the_dialog_with_the_operator(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = uuid.uuid4()
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=501,
            display_name="Анна",
            profile_ciphertext=crypto.encrypt_profile(operator_id, ProfileContent(bio="анкета")),
        )
        await session.commit()
    await set_status(redis, operator_id, OperatorStatus.FREE)
    await _bind_client(sessions, telegram_user_id=9001, username="client", operator_id=operator_id)
    sender = FakeMessenger()
    bus = RecordingBus()
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, bus, model=_FailingModel())
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=1, text="Привет")
    )
    assert all(text != "provider down" for _chat, text in sender.texts)
    assert not any(channel == CHANNEL_AI_REPLY for channel, _event in bus.events)
    assert any(isinstance(event, IncomingClientMessageEvent) for _channel, event in bus.events)


async def test_unassigned_reply_uses_the_operator_voice(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = uuid.uuid4()
    bio = "Уникальная анкета Анны для голоса"
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=501,
            display_name="Анна",
            profile_ciphertext=crypto.encrypt_profile(
                operator_id,
                ProfileContent(bio=bio, city="Казань", age=24),
            ),
        )
        await session.commit()
    await set_status(redis, operator_id, OperatorStatus.FREE)
    sender = FakeMessenger()
    model = _ScriptedModel(
        [
            "Привет. Я в Казани, напиши во сколько.",
            "Сейчас никто не на смене, все отдыхают.",
        ]
    )
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, RecordingBus(), model=model)

    async def say(message_id: int, text: str) -> None:
        await dispatcher.handle_private(
            IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=message_id, text=text)
        )

    await say(1, "привет")
    await say(2, "[стикер]")
    await say(3, "ау")

    texts = [text for _chat, text in sender.texts]
    assert texts[0].startswith("Приветик")
    assert "12000" in texts[0]
    assert "20000" in texts[0]
    assert "подружка" in texts[0]
    assert "26 k" in texts[0]
    assert "город" not in texts[0].casefold()
    assert texts[1] == "Привет. Я в Казани, напиши во сколько."
    assert "город" in texts[2].casefold()
    assert "на смене" not in texts[2]
    assert len(model.seen) == 2
    assert model.seen[0][-1].content == "[стикер]"
    system = model.seen[0][0].content
    assert bio in system
    assert "Анна" in system
    assert "Казань" in system
    assert "Примеры коротких ответов:" in system
    assert "12000" in system
    assert "на смене" not in system
    assert "кто свобод" not in system
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
    assert client is not None
    assert client.assigned_operator_id is None


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
    assert sender.texts
    assert settings.all_operators_busy_text not in {text for _chat, text in sender.texts}
    assert "приветик" in sender.texts[0][1].casefold()
    assert "12000" in sender.texts[0][1]
    async with sessions() as session:
        stored = await get_by_telegram_user_id(session, 42)
    assert stored is not None
    assert stored.assigned_operator_id is None


async def test_price_question_repeats_the_card_without_the_model(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    sender = FakeMessenger()
    model = _ScriptedModel(["это не должно уйти клиенту"])
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, RecordingBus(), model=model)

    async def say(message_id: int, text: str) -> None:
        await dispatcher.handle_private(
            IncomingPrivateMessage(telegram_user_id=77, username="client", message_id=message_id, text=text)
        )

    await say(1, "привет")
    await say(2, "сколько стоит")
    texts = [text for _chat, text in sender.texts]
    assert texts[0].startswith("Приветик")
    assert texts[1].startswith("Стоимость:")
    assert "12000" in texts[1]
    assert "20000" in texts[1]
    assert "26 k" in texts[1]
    assert model.seen == []


async def test_overt_abuse_is_banned_and_then_ignored(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    sender = FakeMessenger()
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, RecordingBus())
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=1, text="иди нахуй")
    )
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=9001, username="client", message_id=2, text="привет")
    )
    assert sender.texts == []
    async with sessions() as session:
        client = await get_by_telegram_user_id(session, 9001)
        assert client is not None
        assert client.is_banned is True
        assert await list_recent_desk(session, client_id=client.id, limit=10) == []


async def test_operator_dm_is_not_banned(
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    crypto = CryptoService(settings.master_key_bytes())
    operator_id = uuid.uuid4()
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=501,
            display_name="Анна",
            profile_ciphertext=crypto.encrypt_profile(operator_id, ProfileContent()),
        )
        await session.commit()
    sender = FakeMessenger()
    dispatcher = UserbotDispatcher(settings, sessions, redis, crypto, sender, RecordingBus())
    await dispatcher.handle_private(
        IncomingPrivateMessage(telegram_user_id=501, username="anna", message_id=1, text="нахуй")
    )
    assert sender.texts == []
    async with sessions() as session:
        assert await get_by_telegram_user_id(session, 501) is None
