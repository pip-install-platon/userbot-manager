import asyncio
from collections.abc import Awaitable, Callable
from io import BytesIO
from uuid import UUID

import structlog
from pyrogram.client import Client
from pyrogram.errors.exceptions.flood_420 import FloodWait
from pyrogram.errors.rpc_error import RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = structlog.get_logger(__name__)


class PyrogramMessenger:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def send_text(self, telegram_user_id: int, text: str) -> None:
        await self._send(lambda: self._client.send_message(telegram_user_id, text))

    async def send_photo(self, telegram_user_id: int, payload: bytes, caption: str | None) -> None:
        async def _send_photo() -> object:
            buffer = BytesIO(payload)
            buffer.name = "photo.jpg"
            return await self._client.send_photo(telegram_user_id, photo=buffer, caption=caption or "")

        await self._send(_send_photo)

    async def send_video(self, telegram_user_id: int, payload: bytes, caption: str | None) -> None:
        async def _send_video() -> object:
            buffer = BytesIO(payload)
            buffer.name = "video.mp4"
            return await self._client.send_video(telegram_user_id, video=buffer, caption=caption or "")

        await self._send(_send_video)

    async def send_choices(self, telegram_user_id: int, text: str, operator_id: UUID) -> None:
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Выбрать", callback_data=f"assign:accept:{operator_id}"),
                    InlineKeyboardButton("Другой", callback_data=f"assign:next:{operator_id}"),
                ]
            ]
        )
        await self._send(
            lambda: self._client.send_message(telegram_user_id, text, reply_markup=markup)
        )

    async def _send(self, factory: Callable[[], Awaitable[object]]) -> None:
        for attempt in range(5):
            try:
                await factory()
                return
            except FloodWait as exc:
                delay = float(exc.value) + 0.1
                log.warning("telegram_flood_wait", delay_seconds=delay, attempt=attempt)
                await asyncio.sleep(delay)
            except RPCError:
                log.warning("telegram_rpc_error", attempt=attempt)
                if attempt == 4:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
