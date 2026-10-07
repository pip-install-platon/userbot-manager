import uuid

from pydantic import BaseModel


class FakeMessenger:
    def __init__(self) -> None:
        self.texts: list[tuple[int, str]] = []
        self.photos: list[tuple[int, bytes, str | None]] = []
        self.videos: list[tuple[int, bytes, str | None]] = []
        self.choices: list[tuple[int, str, uuid.UUID]] = []

    async def send_text(self, telegram_user_id: int, text: str) -> None:
        self.texts.append((telegram_user_id, text))

    async def send_photo(
        self,
        telegram_user_id: int,
        payload: bytes,
        caption: str | None,
    ) -> None:
        self.photos.append((telegram_user_id, payload, caption))

    async def send_video(
        self,
        telegram_user_id: int,
        payload: bytes,
        caption: str | None,
    ) -> None:
        self.videos.append((telegram_user_id, payload, caption))

    async def send_choices(self, telegram_user_id: int, text: str, operator_id: uuid.UUID) -> None:
        self.choices.append((telegram_user_id, text, operator_id))


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, BaseModel]] = []

    async def publish(self, channel: str, event: BaseModel) -> None:
        self.events.append((channel, event))
