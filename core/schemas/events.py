import uuid

from pydantic import BaseModel

from core.constants import MediaKind, OperatorStatus


class IncomingClientMessageEvent(BaseModel):
    operator_id: uuid.UUID
    client_id: uuid.UUID
    client_telegram_id: int
    client_username: str | None = None
    telegram_message_id: int
    text: str | None = None
    media_file_id: str | None = None
    media_kind: MediaKind | None = None
    media_redis_key: str | None = None


class OutgoingToClientEvent(BaseModel):
    operator_id: uuid.UUID
    client_id: uuid.UUID
    client_telegram_id: int
    message_id: uuid.UUID
    text: str | None = None
    media_file_id: str | None = None
    media_kind: MediaKind | None = None
    media_redis_key: str | None = None


class NewClientAssignedEvent(BaseModel):
    operator_id: uuid.UUID
    client_id: uuid.UUID
    client_telegram_id: int
    client_username: str | None = None
    display_name: str


class OperatorStatusChangedEvent(BaseModel):
    operator_id: uuid.UUID
    status: OperatorStatus


class ProfileUpdatedEvent(BaseModel):
    operator_id: uuid.UUID
