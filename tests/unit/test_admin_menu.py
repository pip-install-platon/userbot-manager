import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from admin_bot.bot import create_dispatcher
from core.db.base import Base
from core.repositories.operators import create_operator


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self.values[key] = value

    async def publish(self, channel: str, payload: str) -> int:
        del channel, payload
        return 1


def _labels(markup: object) -> list[str]:
    keyboard = getattr(markup, "inline_keyboard", None)
    assert keyboard is not None
    return [button.text for row in keyboard for button in row]


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.methods: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        del bot, timeout
        self.methods.append(method)
        if isinstance(method, SendMessage):
            return _message(int(method.chat_id), method.text)
        if isinstance(method, EditMessageText):
            return _message(int(method.chat_id or 0), method.text or "")
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        del url, headers, timeout, chunk_size, raise_for_status
        yield b""


def _message(chat_id: int, text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private"),
        text=text,
    )


def _user(user_id: int, first_name: str) -> User:
    return User(id=user_id, is_bot=False, first_name=first_name)


@pytest.fixture
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
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


async def _seed(sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, superadmin: bool) -> None:
    async with sessions() as session:
        await create_operator(
            session,
            operator_id=uuid.uuid4(),
            telegram_user_id=telegram_user_id,
            display_name="Админ" if superadmin else "Оператор",
            profile_ciphertext=b"ciphertext",
            is_superadmin=superadmin,
        )
        await session.commit()


def _start_update(user: User, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=user.id, type="private"),
            from_user=user,
            text="/start",
        ),
    )


async def test_admin_menu_routes(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _seed(sessions, telegram_user_id=8779587662, superadmin=True)
    recorded = RecordingSession()
    bot = Bot(token="123:token", session=recorded)
    dispatcher = create_dispatcher(sessions)
    dispatcher["redis"] = MemoryRedis()
    user = _user(8779587662, "Админ")

    await dispatcher.feed_update(bot, _start_update(user, 1))
    sent = [method for method in recorded.methods if isinstance(method, SendMessage)]
    assert len(sent) == 1
    assert sent[0].text == "Здравствуйте, Админ."
    assert _labels(sent[0].reply_markup) == [
        "<",
        "Не работаю",
        ">",
        "📝 Моя анкета",
        "💬 Мои клиенты",
        "⚙️ Настройки",
        "🛡 Операторы",
    ]

    origin = Message(
        message_id=7,
        date=datetime.now(UTC),
        chat=Chat(id=user.id, type="private"),
        from_user=user,
        text="Здравствуйте, Админ.",
    )

    async def click(update_id: int, data: str, callback_id: str) -> None:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=callback_id,
                    from_user=user,
                    chat_instance="chat",
                    data=data,
                    message=origin,
                ),
            ),
        )

    def last_edit() -> EditMessageText:
        edits = [method for method in recorded.methods if isinstance(method, EditMessageText)]
        assert edits
        return edits[-1]

    await click(2, "status:next", "cb-status")
    assert _labels(last_edit().reply_markup)[1] == "Занят"

    await click(3, "menu:profile", "cb-profile")
    assert last_edit().text == "Анкета."
    assert _labels(last_edit().reply_markup) == ["Показать", "Редактировать", "В меню"]

    await click(4, "profile:edit", "cb-edit")
    assert _labels(last_edit().reply_markup) == [
        "Текст",
        "Возраст",
        "Город",
        "Теги",
        "Фото",
        "Видео",
        "Кружок",
        "Назад",
    ]

    await click(5, "profile:photo", "cb-photo")
    assert last_edit().text is not None
    assert last_edit().text.startswith("Отправьте фото")
    assert _labels(last_edit().reply_markup) == ["Назад"]

    await click(6, "menu:superadmin", "cb-superadmin")
    assert last_edit().text == "Операторы. Анкеты и переписки отсюда не открываются."
    assert _labels(last_edit().reply_markup) == ["Добавить оператора", "Список и статусы", "В меню"]

    await dispatcher.feed_update(bot, _start_update(_user(999, "Гость"), 7))
    denied = [
        method
        for method in recorded.methods
        if isinstance(method, SendMessage) and method.text == "Доступ только для действующих операторов."
    ]
    assert len(denied) == 1
    assert denied[0].reply_markup is None
    await bot.session.close()
