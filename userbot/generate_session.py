import asyncio
import sys

from pyrogram.client import Client

from core.config import get_settings
from core.logging import setup_logging


async def _export() -> str:
    settings = get_settings()
    if settings.api_id is None or settings.api_hash is None:
        raise SystemExit("API_ID and API_HASH are required")
    async with Client(
        name="session-generator",
        api_id=settings.api_id,
        api_hash=settings.api_hash.get_secret_value(),
        in_memory=True,
    ) as app:
        exported = await app.export_session_string()
    if not isinstance(exported, str) or not exported:
        raise SystemExit("Pyrogram did not return a session string")
    return exported


def main() -> None:
    setup_logging(get_settings().log_level)
    session = asyncio.run(_export())
    sys.stdout.write(session + "\n")


if __name__ == "__main__":
    main()
