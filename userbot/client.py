from typing import assert_never

from pyrogram.client import Client

from core.config import Settings
from core.constants import MtprotoTransport
from userbot.network.websocket_transport import install_websocket_transport


def create_userbot(settings: Settings) -> Client:
    if settings.api_id is None or settings.api_hash is None or settings.session_string is None:
        raise RuntimeError("API_ID, API_HASH and SESSION_STRING are required for the userbot")
    session = settings.session_string.get_secret_value().strip()
    if not session:
        raise RuntimeError("SESSION_STRING is empty")
    return open_client(settings, name="userbot", session_string=session)


def open_client(
    settings: Settings,
    *,
    name: str,
    session_string: str | None = None,
) -> Client:
    if settings.api_id is None or settings.api_hash is None:
        raise RuntimeError("API_ID and API_HASH are required")
    _install_transport(settings)
    return Client(
        name=name,
        api_id=settings.api_id,
        api_hash=settings.api_hash.get_secret_value(),
        session_string="" if session_string is None else session_string,
        in_memory=True,
        lang_code=settings.lang_code,
        device_model=settings.device_model or Client.DEVICE_MODEL,
        system_version=settings.system_version or Client.SYSTEM_VERSION,
        app_version=settings.app_version or Client.APP_VERSION,
    )


def _install_transport(settings: Settings) -> None:
    match settings.mtproto_transport:
        case MtprotoTransport.DIRECT:
            return
        case MtprotoTransport.WEBSOCKET:
            install_websocket_transport()
        case _ as unreachable:
            assert_never(unreachable)
