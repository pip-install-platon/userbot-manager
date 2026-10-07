import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PROFILE_MEDIA_LIMIT
from core.crypto.service import CryptoService
from core.db.models import Operator, ProfileMedia
from core.repositories import operators as operator_repository
from core.schemas.operator import ProfileContent


def read_profile(crypto: CryptoService, operator: Operator) -> ProfileContent:
    return crypto.decrypt_profile(operator.id, operator.profile_ciphertext)


async def write_profile(
    session: AsyncSession,
    crypto: CryptoService,
    operator: Operator,
    profile: ProfileContent,
) -> None:
    ciphertext = crypto.encrypt_profile(operator.id, profile)
    await operator_repository.save_profile_ciphertext(session, operator.id, ciphertext)
    operator.profile_ciphertext = ciphertext


async def add_profile_media(
    session: AsyncSession,
    crypto: CryptoService,
    operator: Operator,
    *,
    kind: str,
    file_id: str,
    caption: str | None,
) -> ProfileMedia | None:
    if await operator_repository.count_media(session, operator.id) >= PROFILE_MEDIA_LIMIT:
        return None
    caption_ciphertext = None
    if caption:
        caption_ciphertext = crypto.encrypt_for_operator(operator.id, caption.encode())
    return await operator_repository.add_media(
        session,
        media_id=uuid.uuid4(),
        operator_id=operator.id,
        kind=kind,
        file_id=file_id,
        caption_ciphertext=caption_ciphertext,
        position=await operator_repository.next_media_position(session, operator.id),
    )
