import base64
import time
import uuid
from dataclasses import dataclass
from typing import Protocol, assert_never

import structlog
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.constants import (
    CHANNEL_INCOMING_CLIENT_MESSAGE,
    CHANNEL_NEW_CLIENT_ASSIGNED,
    CHANNEL_OPERATOR_STATUS_CHANGED,
    MAX_TEXT_LENGTH,
    ClientState,
    Direction,
    MediaKind,
    OperatorStatus,
)
from core.crypto.service import CryptoService
from core.db.models import Client, Operator
from core.redis import media as media_store
from core.redis import pending as pending_store
from core.redis import status as operator_status
from core.repositories import clients as client_repository
from core.repositories import messages as message_repository
from core.repositories import operators as operator_repository
from core.routing.engine import pick_free_operator
from core.schemas.client import IncomingPrivateMessage, PendingAssignment
from core.schemas.events import (
    IncomingClientMessageEvent,
    NewClientAssignedEvent,
    OperatorStatusChangedEvent,
    OutgoingToClientEvent,
)

log = structlog.get_logger(__name__)

_OPERATOR_CACHE_SECONDS = 15.0


@dataclass(frozen=True)
class OutboundText:
    chat_id: int
    text: str


@dataclass(frozen=True)
class OutboundPhoto:
    chat_id: int
    payload: bytes
    caption: str | None


@dataclass(frozen=True)
class OutboundVideo:
    chat_id: int
    payload: bytes
    caption: str | None


@dataclass(frozen=True)
class OutboundChoice:
    chat_id: int
    text: str
    operator_id: uuid.UUID


Outbound = OutboundText | OutboundPhoto | OutboundVideo | OutboundChoice


class EventBus(Protocol):
    async def publish(self, channel: str, event: BaseModel) -> None: ...


class OutboundMessenger(Protocol):
    async def send_text(self, telegram_user_id: int, text: str) -> None: ...

    async def send_photo(
        self,
        telegram_user_id: int,
        payload: bytes,
        caption: str | None,
    ) -> None: ...

    async def send_video(
        self,
        telegram_user_id: int,
        payload: bytes,
        caption: str | None,
    ) -> None: ...

    async def send_choices(self, telegram_user_id: int, text: str, operator_id: uuid.UUID) -> None: ...


class UserbotDispatcher:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        crypto: CryptoService,
        sender: OutboundMessenger,
        bus: EventBus,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._redis = redis
        self._crypto = crypto
        self._sender = sender
        self._bus = bus
        self._operator_ids: set[int] = set()
        self._operator_ids_at = 0.0
        self._profiles: dict[uuid.UUID, tuple[str, list[tuple[str, bytes, str | None]]]] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    def invalidate_profile(self, operator_id: uuid.UUID) -> None:
        self._profiles.pop(operator_id, None)
        log.info("profile_cache_invalidated", operator_id=str(operator_id))

    async def handle_private(self, message: IncomingPrivateMessage) -> None:
        structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
        try:
            await self._handle_private(message)
        finally:
            structlog.contextvars.clear_contextvars()

    async def handle_assignment_choice(
        self,
        telegram_user_id: int,
        action: str,
        operator_id: uuid.UUID,
    ) -> None:
        structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
        try:
            pending = await pending_store.load_pending(self._redis, telegram_user_id)
            if pending is None or pending.operator_id != operator_id:
                await self._sender.send_text(telegram_user_id, "Анкета устарела. Напишите ещё раз.")
                return
            if action == "accept":
                await self._accept_pending(telegram_user_id, pending)
                return
            if action == "next":
                await self._switch_pending(telegram_user_id, pending)
                return
            await self._sender.send_text(telegram_user_id, "Неизвестное действие.")
        finally:
            structlog.contextvars.clear_contextvars()

    async def deliver_outgoing(self, event: OutgoingToClientEvent) -> None:
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid.uuid4()),
            operator_id=str(event.operator_id),
        )
        try:
            if event.media_redis_key and event.media_kind is not None:
                payload = await media_store.load_media(self._redis, event.media_redis_key)
                if payload is not None and event.media_kind == MediaKind.PHOTO:
                    await self._sender.send_photo(event.client_telegram_id, payload, event.text)
                    return
                if payload is not None and event.media_kind == MediaKind.VIDEO:
                    await self._sender.send_video(event.client_telegram_id, payload, event.text)
                    return
                log.warning("outgoing_media_missing", message_id=str(event.message_id))
            if event.text:
                await self._sender.send_text(event.client_telegram_id, event.text)
                return
            await self._sender.send_text(
                event.client_telegram_id,
                "Оператор отправил вложение, но файл уже недоступен.",
            )
        finally:
            structlog.contextvars.clear_contextvars()

    async def _handle_private(self, message: IncomingPrivateMessage) -> None:
        if message.text is not None and len(message.text) > MAX_TEXT_LENGTH:
            await self._sender.send_text(message.telegram_user_id, "Сообщение слишком длинное.")
            return
        if message.media_bytes is not None and len(message.media_bytes) > self._settings.media_max_bytes:
            await self._sender.send_text(message.telegram_user_id, "Файл слишком большой.")
            return

        events: list[tuple[str, BaseModel]] = []
        outbound: list[Outbound] = []
        pending: PendingAssignment | None = None
        claimed_id: uuid.UUID | None = None
        persisted = False

        async with self._sessions() as session:
            try:
                if await self._is_operator(session, message.telegram_user_id):
                    log.info("ignore_operator_dm", telegram_user_id=message.telegram_user_id)
                    return
                if not self._settings.auto_assign:
                    existing_pending = await pending_store.load_pending(
                        self._redis,
                        message.telegram_user_id,
                    )
                    if existing_pending is not None:
                        operator = await operator_repository.get_by_id(
                            session,
                            existing_pending.operator_id,
                        )
                        if operator is None:
                            await pending_store.delete_pending(self._redis, message.telegram_user_id)
                            await session.commit()
                            persisted = True
                            await self._sender.send_text(
                                message.telegram_user_id,
                                "Анкета устарела. Напишите ещё раз.",
                            )
                            return
                        outbound.extend(
                            await self._profile_outbound(
                                session,
                                operator,
                                message.telegram_user_id,
                                with_choices=True,
                            )
                        )
                        await session.commit()
                        persisted = True
                        await self._play(outbound)
                        return

                client = await client_repository.get_or_create(
                    session,
                    telegram_user_id=message.telegram_user_id,
                    username=message.username,
                )
                operator = await self._current_operator(session, client)
                if operator is None:
                    operator = await pick_free_operator(
                        session,
                        self._redis,
                        status_ttl_seconds=self._settings.status_ttl_seconds,
                    )
                    if operator is None:
                        await session.commit()
                        persisted = True
                        await self._sender.send_text(
                            message.telegram_user_id,
                            self._settings.all_operators_busy_text,
                        )
                        return
                    claimed_id = operator.id
                    events.append(
                        (
                            CHANNEL_OPERATOR_STATUS_CHANGED,
                            OperatorStatusChangedEvent(
                                operator_id=operator.id,
                                status=OperatorStatus.BUSY,
                            ),
                        )
                    )
                    if self._settings.auto_assign:
                        self._assign(client, operator, ClientState.IN_DIALOG)
                        events.append(self._assigned_event(client, operator))
                        outbound.extend(
                            await self._profile_outbound(
                                session,
                                operator,
                                message.telegram_user_id,
                                with_choices=False,
                            )
                        )
                        await self._queue_incoming(session, client, operator, message, events)
                    else:
                        pending = await self._build_pending(client, operator, message)
                        outbound.extend(
                            await self._profile_outbound(
                                session,
                                operator,
                                message.telegram_user_id,
                                with_choices=True,
                            )
                        )
                else:
                    if client.state == ClientState.CLOSED.value:
                        client.state = ClientState.IN_DIALOG.value
                    await self._queue_incoming(session, client, operator, message, events)
                await session.commit()
                persisted = True
            except Exception:
                await session.rollback()
                if claimed_id is not None and not persisted:
                    await operator_status.set_status(
                        self._redis,
                        claimed_id,
                        OperatorStatus.FREE,
                        self._settings.status_ttl_seconds,
                    )
                raise

        if pending is not None:
            try:
                await pending_store.save_pending(self._redis, message.telegram_user_id, pending)
            except Exception:
                await operator_status.set_status(
                    self._redis,
                    pending.operator_id,
                    OperatorStatus.FREE,
                    self._settings.status_ttl_seconds,
                )
                raise
        await self._play(outbound)
        await self._publish_all(events)

    async def _accept_pending(self, telegram_user_id: int, pending: PendingAssignment) -> None:
        events: list[tuple[str, BaseModel]] = []
        async with self._sessions() as session:
            client = await client_repository.get_by_id(session, pending.client_id)
            operator = await operator_repository.get_by_id(session, pending.operator_id)
            if client is None or operator is None or not operator.is_active:
                await session.rollback()
                await pending_store.delete_pending(self._redis, telegram_user_id)
                await self._sender.send_text(telegram_user_id, "Оператор недоступен. Напишите ещё раз.")
                return
            self._assign(client, operator, ClientState.IN_DIALOG)
            plaintext = self._crypto.decrypt_for_operator(
                operator.id,
                base64.b64decode(pending.text_ciphertext_b64),
            ).decode()
            message = IncomingPrivateMessage(
                telegram_user_id=telegram_user_id,
                username=pending.username,
                message_id=pending.telegram_message_id,
                text=plaintext or None,
                media_file_id=pending.media_file_id,
                media_kind=pending.media_kind,
            )
            events.append(self._assigned_event(client, operator))
            await self._queue_incoming(
                session,
                client,
                operator,
                message,
                events,
                media_redis_key=pending.media_redis_key,
            )
            display_name = operator.display_name
            await session.commit()
        await pending_store.delete_pending(self._redis, telegram_user_id)
        await self._sender.send_text(telegram_user_id, f"Вы подключены к оператору {display_name}.")
        await self._publish_all(events)

    async def _switch_pending(self, telegram_user_id: int, pending: PendingAssignment) -> None:
        plaintext = self._crypto.decrypt_for_operator(
            pending.operator_id,
            base64.b64decode(pending.text_ciphertext_b64),
        )
        await operator_status.set_status(
            self._redis,
            pending.operator_id,
            OperatorStatus.FREE,
            self._settings.status_ttl_seconds,
        )
        async with self._sessions() as session:
            client = await client_repository.get_by_id(session, pending.client_id)
            operator = await pick_free_operator(
                session,
                self._redis,
                exclude={pending.operator_id},
                status_ttl_seconds=self._settings.status_ttl_seconds,
            )
            if client is None or operator is None:
                await session.commit()
                await pending_store.delete_pending(self._redis, telegram_user_id)
                await self._sender.send_text(
                    telegram_user_id,
                    self._settings.all_operators_busy_text,
                )
                return
            replacement = pending.model_copy(
                update={
                    "operator_id": operator.id,
                    "text_ciphertext_b64": base64.b64encode(
                        self._crypto.encrypt_for_operator(operator.id, plaintext)
                    ).decode(),
                }
            )
            chosen_id = operator.id
            outbound = await self._profile_outbound(
                session,
                operator,
                telegram_user_id,
                with_choices=True,
            )
            await session.commit()
        await pending_store.save_pending(self._redis, telegram_user_id, replacement)
        await self._play(outbound)
        await self._bus.publish(
            CHANNEL_OPERATOR_STATUS_CHANGED,
            OperatorStatusChangedEvent(operator_id=chosen_id, status=OperatorStatus.BUSY),
        )

    async def _current_operator(self, session: AsyncSession, client: Client) -> Operator | None:
        if client.assigned_operator_id is None:
            return None
        operator = await operator_repository.get_by_id(session, client.assigned_operator_id)
        if operator is None or not operator.is_active:
            client.assigned_operator_id = None
            client.state = ClientState.NEW.value
            return None
        return operator

    async def _is_operator(self, session: AsyncSession, telegram_user_id: int) -> bool:
        now = time.monotonic()
        if now - self._operator_ids_at > _OPERATOR_CACHE_SECONDS:
            self._operator_ids = await operator_repository.list_telegram_user_ids(session)
            self._operator_ids_at = now
        return telegram_user_id in self._operator_ids

    async def _queue_incoming(
        self,
        session: AsyncSession,
        client: Client,
        operator: Operator,
        message: IncomingPrivateMessage,
        events: list[tuple[str, BaseModel]],
        media_redis_key: str | None = None,
    ) -> None:
        if media_redis_key is None and message.media_bytes:
            media_redis_key = await media_store.store_transient_media(self._redis, message.media_bytes)
        ciphertext = self._crypto.encrypt_for_operator(operator.id, (message.text or "").encode())
        _row, inserted = await message_repository.insert_message(
            session,
            message_id=uuid.uuid4(),
            client_id=client.id,
            operator_id=operator.id,
            direction=Direction.INCOMING.value,
            ciphertext=ciphertext,
            media_file_id=message.media_file_id,
            telegram_message_id=message.message_id,
        )
        if not inserted:
            log.info(
                "duplicate_client_message",
                client_id=str(client.id),
                telegram_message_id=message.message_id,
            )
            return
        kind = MediaKind(message.media_kind) if message.media_kind else None
        events.append(
            (
                CHANNEL_INCOMING_CLIENT_MESSAGE,
                IncomingClientMessageEvent(
                    operator_id=operator.id,
                    client_id=client.id,
                    client_telegram_id=message.telegram_user_id,
                    client_username=message.username,
                    telegram_message_id=message.message_id,
                    text=message.text,
                    media_file_id=message.media_file_id,
                    media_kind=kind,
                    media_redis_key=media_redis_key,
                ),
            )
        )

    async def _build_pending(
        self,
        client: Client,
        operator: Operator,
        message: IncomingPrivateMessage,
    ) -> PendingAssignment:
        media_key = None
        if message.media_bytes:
            media_key = await media_store.store_transient_media(self._redis, message.media_bytes)
        ciphertext = self._crypto.encrypt_for_operator(operator.id, (message.text or "").encode())
        return PendingAssignment(
            operator_id=operator.id,
            client_id=client.id,
            telegram_message_id=message.message_id,
            username=message.username,
            text_ciphertext_b64=base64.b64encode(ciphertext).decode(),
            media_file_id=message.media_file_id,
            media_kind=message.media_kind,
            media_redis_key=media_key,
        )

    async def _profile_outbound(
        self,
        session: AsyncSession,
        operator: Operator,
        chat_id: int,
        *,
        with_choices: bool,
    ) -> list[Outbound]:
        cached = self._profiles.get(operator.id)
        if cached is None:
            profile = self._crypto.decrypt_profile(operator.id, operator.profile_ciphertext)
            media_items: list[tuple[str, bytes, str | None]] = []
            for media in await operator_repository.list_media(session, operator.id):
                payload = await media_store.load_media(
                    self._redis,
                    media_store.profile_media_key(media.id),
                )
                if payload is None:
                    continue
                caption = None
                if media.caption_ciphertext:
                    caption = self._crypto.decrypt_for_operator(
                        operator.id,
                        media.caption_ciphertext,
                    ).decode()
                media_items.append((media.kind, payload, caption))
            cached = (profile.render(operator.display_name), media_items)
            self._profiles[operator.id] = cached
        text, media_items = cached
        outbound: list[Outbound] = []
        if with_choices:
            outbound.append(OutboundChoice(chat_id=chat_id, text=text, operator_id=operator.id))
        else:
            outbound.append(OutboundText(chat_id=chat_id, text=text))
        for kind, payload, caption in media_items:
            if kind == MediaKind.PHOTO.value:
                outbound.append(OutboundPhoto(chat_id=chat_id, payload=payload, caption=caption))
            elif kind == MediaKind.VIDEO.value:
                outbound.append(OutboundVideo(chat_id=chat_id, payload=payload, caption=caption))
        return outbound

    async def _play(self, outbound: list[Outbound]) -> None:
        for item in outbound:
            if isinstance(item, OutboundText):
                await self._sender.send_text(item.chat_id, item.text)
            elif isinstance(item, OutboundPhoto):
                await self._sender.send_photo(item.chat_id, item.payload, item.caption)
            elif isinstance(item, OutboundVideo):
                await self._sender.send_video(item.chat_id, item.payload, item.caption)
            elif isinstance(item, OutboundChoice):
                await self._sender.send_choices(item.chat_id, item.text, item.operator_id)
            else:
                assert_never(item)

    async def _publish_all(self, events: list[tuple[str, BaseModel]]) -> None:
        for channel, event in events:
            try:
                await self._bus.publish(channel, event)
            except Exception:
                log.error("event_publish_failed", channel=channel)

    @staticmethod
    def _assign(client: Client, operator: Operator, state: ClientState) -> None:
        client.assigned_operator_id = operator.id
        client.state = state.value

    @staticmethod
    def _assigned_event(client: Client, operator: Operator) -> tuple[str, NewClientAssignedEvent]:
        return (
            CHANNEL_NEW_CLIENT_ASSIGNED,
            NewClientAssignedEvent(
                operator_id=operator.id,
                client_id=client.id,
                client_telegram_id=client.telegram_user_id,
                client_username=client.username,
                display_name=operator.display_name,
            ),
        )
