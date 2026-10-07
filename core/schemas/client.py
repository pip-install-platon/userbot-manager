import uuid

from pydantic import BaseModel, Field


class PendingAssignment(BaseModel):
    """Manual routing choice stored until the client accepts an operator.

    The message body is already encrypted for the candidate operator.
    """

    operator_id: uuid.UUID
    client_id: uuid.UUID
    telegram_message_id: int
    username: str | None = None
    text_ciphertext_b64: str
    media_file_id: str | None = None
    media_kind: str | None = None
    media_redis_key: str | None = None


class IncomingPrivateMessage(BaseModel):
    telegram_user_id: int
    username: str | None = None
    message_id: int
    text: str | None = None
    media_file_id: str | None = None
    media_kind: str | None = None
    media_bytes: bytes | None = Field(default=None, exclude=True)
