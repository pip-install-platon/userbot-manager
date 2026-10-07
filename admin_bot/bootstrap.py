import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.crypto.service import CryptoService
from core.repositories.operators import create_operator, get_by_telegram_user_id
from core.schemas.operator import ProfileContent

log = structlog.get_logger(__name__)


async def ensure_superadmin(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoService,
) -> None:
    telegram_id = settings.superadmin_telegram_id
    if telegram_id is None:
        log.info("superadmin_bootstrap_skipped")
        return
    async with session_factory() as session:
        existing = await get_by_telegram_user_id(session, telegram_id)
        if existing is not None:
            existing.is_superadmin = True
            existing.is_active = True
            await session.commit()
            log.info("superadmin_present", operator_id=str(existing.id))
            return
        operator_id = uuid.uuid4()
        await create_operator(
            session,
            operator_id=operator_id,
            telegram_user_id=telegram_id,
            display_name=settings.superadmin_display_name.strip() or "Superadmin",
            profile_ciphertext=crypto.encrypt_profile(operator_id, ProfileContent()),
            is_superadmin=True,
        )
        await session.commit()
        log.info("superadmin_created", operator_id=str(operator_id))
