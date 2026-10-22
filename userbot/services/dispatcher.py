import base64
import time
import uuid
from dataclasses import dataclass
from typing import Protocol, assert_never

import structlog
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.ai.openrouter import ReplyModel
from core.ai.persona import persona_messages
from core.ai.reply import usable_reply
from core.config import Settings
from core.constants import (
    AI_HISTORY_MESSAGES,
    CHANNEL_AI_REPLY,
    CHANNEL_INCOMING_CLIENT_MESSAGE,
    CHANNEL_MEETING_OFFER,
    CHANNEL_NEW_CLIENT_ASSIGNED,
    CHANNEL_OPERATOR_ALERT,
    CHANNEL_OPERATOR_STATUS_CHANGED,
    MAX_TEXT_LENGTH,
    ClientState,
    Direction,
    MediaKind,
    MeetingStatus,
    OperatorStatus,
)
from core.crypto.service import CryptoService
from core.db.models import Client, Operator
from core.meetings.facts import MeetingExtractor
from core.meetings.phrases import circle_received
from core.redis import media as media_store
from core.redis import pending as pending_store
from core.redis import status as operator_status
from core.repositories import clients as client_repository
from core.repositories import meetings as meeting_repository
from core.repositories import messages as message_repository
from core.repositories import operators as operator_repository
from core.routing.album_request import detect_album_request, missing_album_text
from core.routing.engine import pick_free_operator
from core.schemas.client import IncomingPrivateMessage, PendingAssignment
from core.schemas.events import (
    AiReplyEvent,
    IncomingClientMessageEvent,
    NewClientAssignedEvent,
    OperatorStatusChangedEvent,
    OutgoingToClientEvent,
)
from userbot.services.desk import DeskOutcome, conduct, confirm_assigned

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
class OutboundVideoNote:
    chat_id: int
    payload: bytes


@dataclass(frozen=True)
class OutboundChoice:
    chat_id: int
    text: str
    operator_id: uuid.UUID


Outbound = OutboundText | OutboundPhoto | OutboundVideo | OutboundVideoNote | OutboundChoice


@dataclass(frozen=True)
class AiReplySeed:
    operator_id: uuid.UUID
    client_id: uuid.UUID
    client_telegram_id: int
    client_username: str | None


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

    async def send_video_note(self, telegram_user_id: int, payload: bytes) -> None: ...

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
        model: ReplyModel | None = None,
        extractor: MeetingExtractor | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._redis = redis
        self._crypto = crypto
        self._sender = sender
        self._bus = bus
        self._model = model
        self._extractor = extractor
        self._operator_ids: set[int] = set()
        self._operator_ids_at = 0.0
        self._profiles: dict[uuid.UUID, str] = {}

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
                if payload is None:
                    log.warning("outgoing_media_missing", message_id=str(event.message_id))
                else:
                    await self._send_operator_media(
                        event.client_telegram_id,
                        event.media_kind,
                        payload,
                        event.text,
                    )
                    return
            if event.text:
                await self._sender.send_text(event.client_telegram_id, event.text)
                return
            await self._sender.send_text(event.client_telegram_id, "Файл не открылся, кинь ещё раз 🙂")
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
        ai_reply: AiReplySeed | None = None
        desk_client_id: uuid.UUID | None = None
        confirm_seed: tuple[uuid.UUID, uuid.UUID, str] | None = None
        circle_ack: tuple[uuid.UUID, uuid.UUID, int] | None = None

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
                        outbound.extend(
                            await self._album_outbound(
                                session,
                                operator,
                                message.telegram_user_id,
                                message.text,
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
                    phone_number=message.phone_number,
                )
                operator = await self._current_operator(session, client)
                if operator is not None and await meeting_repository.should_reopen_desk(session, client.id):
                    client.assigned_operator_id = None
                    client.state = ClientState.NEW.value
                    operator = None
                if operator is None and self._settings.auto_assign:
                    inserted = await self._queue_incoming(session, client, None, message, events)
                    if inserted:
                        desk_client_id = client.id
                elif operator is None:
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
                    pending = await self._build_pending(client, operator, message)
                    outbound.extend(
                        await self._profile_outbound(
                            session,
                            operator,
                            message.telegram_user_id,
                            with_choices=True,
                        )
                    )
                    outbound.extend(
                        await self._album_outbound(
                            session,
                            operator,
                            message.telegram_user_id,
                            message.text,
                        )
                    )
                else:
                    if client.state == ClientState.CLOSED.value:
                        client.state = ClientState.IN_DIALOG.value
                    outbound.extend(
                        await self._album_outbound(
                            session,
                            operator,
                            message.telegram_user_id,
                            message.text,
                        )
                    )
                    inserted = await self._queue_incoming(session, client, operator, message, events)
                    if inserted and message.media_kind == MediaKind.VIDEO_NOTE.value:
                        circle_ack = (operator.id, client.id, message.telegram_user_id)
                    else:
                        waiting_for_yes = (
                            inserted and message.text is not None and await self._waiting_for_client(session, client.id)
                        )
                        if waiting_for_yes and message.text is not None:
                            confirm_seed = (operator.id, client.id, message.text)
                            ai_reply = self._ai_seed(client, operator, message)
                        elif inserted:
                            ai_reply = self._ai_seed(client, operator, message)
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
        if desk_client_id is not None:
            outcome = await conduct(
                self._sessions,
                self._redis,
                self._crypto,
                self._model,
                self._extractor,
                client_id=desk_client_id,
            )
            await self._deliver_desk(message.telegram_user_id, outcome)
            return
        if confirm_seed is not None:
            operator_id, client_id, text = confirm_seed
            confirmed = await confirm_assigned(
                self._sessions,
                self._redis,
                self._crypto,
                self._extractor,
                operator_id=operator_id,
                client_id=client_id,
                text=text,
            )
            if confirmed is not None:
                await self._deliver_desk(message.telegram_user_id, confirmed)
                return
        if circle_ack is not None:
            operator_id, client_id, chat_id = circle_ack
            await self._ack_circle(operator_id, client_id, chat_id)
            return
        if ai_reply is not None:
            await self._send_ai_reply(ai_reply)

    async def _ack_circle(self, operator_id: uuid.UUID, client_id: uuid.UUID, chat_id: int) -> None:
        text = circle_received()
        async with self._sessions() as session:
            await message_repository.insert_message(
                session,
                message_id=uuid.uuid4(),
                client_id=client_id,
                operator_id=operator_id,
                direction=Direction.OUTGOING.value,
                ciphertext=self._crypto.encrypt_for_operator(operator_id, text.encode()),
                media_file_id=None,
                telegram_message_id=None,
            )
            await session.commit()
        await self._sender.send_text(chat_id, text)

    async def _waiting_for_client(self, session: AsyncSession, client_id: uuid.UUID) -> bool:
        meeting = await meeting_repository.get_open(session, client_id)
        return meeting is not None and meeting.status == MeetingStatus.ADDRESS_SENT.value

    async def _deliver_desk(self, chat_id: int, outcome: DeskOutcome) -> None:
        if outcome.reply:
            await self._sender.send_text(chat_id, outcome.reply)
        if outcome.offer is not None:
            await self._bus.publish(CHANNEL_MEETING_OFFER, outcome.offer)
        if outcome.alert is not None:
            await self._bus.publish(CHANNEL_OPERATOR_ALERT, outcome.alert)

    async def _accept_pending(self, telegram_user_id: int, pending: PendingAssignment) -> None:
        events: list[tuple[str, BaseModel]] = []
        album: list[Outbound] = []
        ai_reply: AiReplySeed | None = None
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
            album = await self._album_outbound(session, operator, telegram_user_id, plaintext or None)
            inserted = await self._queue_incoming(
                session,
                client,
                operator,
                message,
                events,
                media_redis_key=pending.media_redis_key,
            )
            if inserted:
                ai_reply = self._ai_seed(client, operator, message)
            await session.commit()
        await pending_store.delete_pending(self._redis, telegram_user_id)
        await self._play(album)
        await self._publish_all(events)
        if ai_reply is not None:
            await self._send_ai_reply(ai_reply)

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
        operator: Operator | None,
        message: IncomingPrivateMessage,
        events: list[tuple[str, BaseModel]],
        media_redis_key: str | None = None,
    ) -> bool:
        if media_redis_key is None and message.media_bytes:
            media_redis_key = await media_store.store_transient_media(self._redis, message.media_bytes)
        plaintext = (message.text or "").encode()
        if operator is None:
            ciphertext = self._crypto.encrypt_for_desk(plaintext)
            operator_id = None
        else:
            ciphertext = self._crypto.encrypt_for_operator(operator.id, plaintext)
            operator_id = operator.id
        _row, inserted = await message_repository.insert_message(
            session,
            message_id=uuid.uuid4(),
            client_id=client.id,
            operator_id=operator_id,
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
            return False
        if operator is None:
            return True
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
        return True

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

    def _ai_seed(
        self,
        client: Client,
        operator: Operator,
        message: IncomingPrivateMessage,
    ) -> AiReplySeed | None:
        text = message.text
        if self._model is None or text is None or not text.strip():
            return None
        if detect_album_request(text) is not None:
            return None
        return AiReplySeed(
            operator_id=operator.id,
            client_id=client.id,
            client_telegram_id=message.telegram_user_id,
            client_username=message.username,
        )

    async def _send_ai_reply(self, seed: AiReplySeed) -> None:
        model = self._model
        if model is None:
            return
        async with self._sessions() as session:
            operator = await operator_repository.get_by_id(session, seed.operator_id)
            if operator is None or not operator.is_active:
                return
            profile = self._crypto.decrypt_profile(operator.id, operator.profile_ciphertext)
            rows = await message_repository.list_recent_visible(
                session,
                client_id=seed.client_id,
                operator_id=operator.id,
                limit=AI_HISTORY_MESSAGES,
            )
            history = [(row.direction, self._crypto.decrypt_text(row.operator_id, row.ciphertext)) for row in rows]
            display_name = operator.display_name
        turns = persona_messages(display_name=display_name, profile=profile, history=history)
        if not any(turn.role == "user" for turn in turns):
            return
        try:
            answer = await model.complete(turns)
        except Exception:
            log.warning("ai_reply_failed", operator_id=str(seed.operator_id), client_id=str(seed.client_id))
            return
        cleaned = usable_reply(answer)
        if cleaned is None or len(cleaned) > MAX_TEXT_LENGTH:
            log.warning("ai_reply_unusable", operator_id=str(seed.operator_id), client_id=str(seed.client_id))
            return
        message_id = uuid.uuid4()
        async with self._sessions() as session:
            _row, inserted = await message_repository.insert_message(
                session,
                message_id=message_id,
                client_id=seed.client_id,
                operator_id=seed.operator_id,
                direction=Direction.OUTGOING.value,
                ciphertext=self._crypto.encrypt_for_operator(seed.operator_id, cleaned.encode()),
                media_file_id=None,
                telegram_message_id=None,
            )
            if not inserted:
                await session.rollback()
                return
            await session.commit()
        await self._sender.send_text(seed.client_telegram_id, cleaned)
        await self._bus.publish(
            CHANNEL_AI_REPLY,
            AiReplyEvent(
                operator_id=seed.operator_id,
                client_id=seed.client_id,
                client_telegram_id=seed.client_telegram_id,
                client_username=seed.client_username,
                text=cleaned,
            ),
        )
        log.info("ai_reply_sent", operator_id=str(seed.operator_id), client_id=str(seed.client_id))

    async def _profile_outbound(
        self,
        _session: AsyncSession,
        operator: Operator,
        chat_id: int,
        *,
        with_choices: bool,
    ) -> list[Outbound]:
        cached = self._profiles.get(operator.id)
        if cached is None:
            profile = self._crypto.decrypt_profile(operator.id, operator.profile_ciphertext)
            cached = profile.render(operator.display_name)
            self._profiles[operator.id] = cached
        outbound: list[Outbound] = []
        if with_choices:
            outbound.append(OutboundChoice(chat_id=chat_id, text=cached, operator_id=operator.id))
        else:
            outbound.append(OutboundText(chat_id=chat_id, text=cached))
        return outbound

    async def _album_outbound(
        self,
        session: AsyncSession,
        operator: Operator,
        chat_id: int,
        text: str | None,
    ) -> list[Outbound]:
        kind = detect_album_request(text)
        if kind is None:
            return []
        chosen = await self._next_album_item(session, operator, kind)
        if chosen is None:
            return [OutboundText(chat_id=chat_id, text=missing_album_text(operator.display_name, kind))]
        payload, caption = chosen
        return [self._album_item(chat_id, kind, payload, caption)]

    async def _next_album_item(
        self,
        session: AsyncSession,
        operator: Operator,
        kind: MediaKind,
    ) -> tuple[bytes, str | None] | None:
        available: list[tuple[bytes, str | None]] = []
        for media in await operator_repository.list_media(session, operator.id):
            if media.kind != kind.value:
                continue
            payload = await media_store.load_media(self._redis, media_store.profile_media_key(media.id))
            if payload is None:
                continue
            caption = None
            if media.caption_ciphertext:
                caption = self._crypto.decrypt_for_operator(operator.id, media.caption_ciphertext).decode()
            available.append((payload, caption))
        if not available:
            return None
        cursor = await self._redis.incr(f"album_cursor:{operator.id}:{kind.value}")
        index = (int(cursor) - 1) % len(available)
        return available[index]

    @staticmethod
    def _album_item(chat_id: int, kind: MediaKind, payload: bytes, caption: str | None) -> Outbound:
        match kind:
            case MediaKind.PHOTO:
                return OutboundPhoto(chat_id=chat_id, payload=payload, caption=caption)
            case MediaKind.VIDEO:
                return OutboundVideo(chat_id=chat_id, payload=payload, caption=caption)
            case MediaKind.VIDEO_NOTE:
                return OutboundVideoNote(chat_id=chat_id, payload=payload)
            case _ as unreachable:
                assert_never(unreachable)

    async def _send_operator_media(
        self,
        chat_id: int,
        kind: MediaKind,
        payload: bytes,
        caption: str | None,
    ) -> None:
        match kind:
            case MediaKind.PHOTO:
                await self._sender.send_photo(chat_id, payload, caption)
            case MediaKind.VIDEO:
                await self._sender.send_video(chat_id, payload, caption)
            case MediaKind.VIDEO_NOTE:
                await self._sender.send_video_note(chat_id, payload)
            case _ as unreachable:
                assert_never(unreachable)

    async def _play(self, outbound: list[Outbound]) -> None:
        for item in outbound:
            if isinstance(item, OutboundText):
                await self._sender.send_text(item.chat_id, item.text)
            elif isinstance(item, OutboundPhoto):
                await self._sender.send_photo(item.chat_id, item.payload, item.caption)
            elif isinstance(item, OutboundVideo):
                await self._sender.send_video(item.chat_id, item.payload, item.caption)
            elif isinstance(item, OutboundVideoNote):
                await self._sender.send_video_note(item.chat_id, item.payload)
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
