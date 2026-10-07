from pyrogram.client import Client

from core.config import Settings


def create_userbot(settings: Settings) -> Client:
    if settings.api_id is None or settings.api_hash is None or settings.session_string is None:
        raise RuntimeError("API_ID, API_HASH and SESSION_STRING are required for the userbot")
    session = settings.session_string.get_secret_value().strip()
    if not session:
        raise RuntimeError("SESSION_STRING is empty")
    return Client(
        name="userbot",
        api_id=settings.api_id,
        api_hash=settings.api_hash.get_secret_value(),
        session_string=session,
        in_memory=True,
    )
