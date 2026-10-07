import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import DIALOG_HISTORY_LIMIT, Direction
from core.crypto.service import CryptoService
from core.repositories.clients import get_owned
from core.repositories.messages import insert_message, list_dialog


async def load_dialog_for_operator(
    session: AsyncSession,
    crypto: CryptoService,
    *,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
) -> list[tuple[str, datetime, str]] | None:
    client = await get_owned(session, client_id, operator_id)
    if client is None:
        return None
    rows = await list_dialog(
        session,
        client_id=client_id,
        operator_id=operator_id,
        limit=DIALOG_HISTORY_LIMIT,
    )
    history: list[tuple[str, datetime, str]] = []
    for row in rows:
        text = crypto.decrypt_for_operator(operator_id, row.ciphertext).decode()
        history.append((row.direction, row.created_at, text))
    return history


async def store_outgoing(
    session: AsyncSession,
    crypto: CryptoService,
    *,
    client_id: uuid.UUID,
    operator_id: uuid.UUID,
    text: str,
    media_file_id: str | None,
) -> uuid.UUID:
    message_id = uuid.uuid4()
    await insert_message(
        session,
        message_id=message_id,
        client_id=client_id,
        operator_id=operator_id,
        direction=Direction.OUTGOING.value,
        ciphertext=crypto.encrypt_for_operator(operator_id, text.encode()),
        media_file_id=media_file_id,
        telegram_message_id=None,
    )
    return message_id
