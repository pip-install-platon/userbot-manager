import asyncio
import logging
from collections.abc import AsyncIterator

from pyrogram.connection.transport.tcp.tcp import TCP
from pyrogram.connection.transport.tcp.tcp_abridged_o import TCPAbridgedO
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

log = logging.getLogger(__name__)

_DC_HOSTS = {
    "149.154.175.50": "pluto",
    "149.154.175.53": "pluto",
    "149.154.175.10": "pluto",
    "149.154.167.51": "venus",
    "149.154.167.40": "venus",
    "149.154.167.151": "venus",
    "149.154.175.100": "aurora",
    "149.154.175.117": "aurora",
    "149.154.167.91": "vesta",
    "149.154.164.250": "vesta",
    "91.108.56.130": "flora",
}


def websocket_host(ip: str) -> str:
    host = _DC_HOSTS.get(ip)
    if host is None:
        raise OSError(f"No Telegram websocket datacenter for {ip}")
    return host


class WebsocketTCP(TCP):
    def __init__(self, ipv6: bool, proxy: dict[str, object] | None) -> None:
        self.socket = None
        self.reader = None
        self.writer = None
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_event_loop()
        self.proxy = {} if proxy is None else proxy
        self.ipv6 = ipv6
        self._buffer = bytearray()
        self._ready = asyncio.Event()
        self._closed = False
        self._ws: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self, address: tuple[str, int]) -> None:
        host = websocket_host(address[0])
        url = f"wss://{host}.web.telegram.org/apiws"
        try:
            self._ws = await connect(
                url,
                subprotocols=[Subprotocol("binary")],
                additional_headers={"Origin": "https://web.telegram.org"},
                open_timeout=TCP.TIMEOUT,
                # Telegram's MTProto websocket does not answer protocol pings.
                # The library would close a live session after the ping timeout.
                ping_interval=None,
            )
        except OSError:
            raise
        except Exception as exc:
            raise OSError(f"Telegram websocket connection failed for {host}") from exc
        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mtproto-ws-{host}")
        log.info("Telegram websocket connected: %s", url)

    async def _read_loop(self) -> None:
        ws = self._ws
        if ws is None:
            self._closed = True
            self._ready.set()
            return
        try:
            message: str | bytes
            async for message in self._messages(ws):
                if isinstance(message, bytes):
                    self._buffer.extend(message)
                    self._ready.set()
        except ConnectionClosed as exc:
            received = exc.rcvd
            log.info(
                "Telegram websocket closed code=%s reason=%s",
                None if received is None else received.code,
                None if received is None else received.reason,
            )
        finally:
            self._closed = True
            self._ready.set()

    async def _messages(self, ws: ClientConnection) -> AsyncIterator[str | bytes]:
        async for message in ws:
            yield message

    async def close(self) -> None:
        self._closed = True
        self._ready.set()
        task = self._reader_task
        ws = self._ws
        self._reader_task = None
        self._ws = None
        if task is not None:
            task.cancel()
        if ws is not None:
            await ws.close()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def send(self, data: bytes) -> None:
        ws = self._ws
        if ws is None:
            raise OSError("Telegram websocket is not connected")
        async with self.lock:
            try:
                await ws.send(data)
            except Exception as exc:
                raise OSError("Telegram websocket send failed") from exc

    async def recv(self, length: int = 0) -> bytes | None:
        while len(self._buffer) < length:
            if self._closed:
                return None
            self._ready.clear()
            if len(self._buffer) >= length:
                break
            try:
                await asyncio.wait_for(self._ready.wait(), TCP.TIMEOUT)
            except TimeoutError:
                if len(self._buffer) < length:
                    return None
        payload = bytes(self._buffer[:length])
        del self._buffer[:length]
        return payload


class WebsocketObfuscated(TCPAbridgedO, WebsocketTCP):
    """Obfuscated abridged MTProto over Telegram's official websocket byte stream."""

    def __init__(self, ipv6: bool, proxy: dict[str, object] | None) -> None:
        WebsocketTCP.__init__(self, ipv6, proxy)


def install_websocket_transport() -> None:
    import pyrogram.connection.connection as connection

    connection.TCPAbridged = WebsocketObfuscated  # type: ignore[attr-defined]
