import asyncio

from pyrogram.connection.transport.tcp.tcp import TCP
from pyrogram.session.internals.data_center import DataCenter
from pytest import MonkeyPatch

from userbot.network.websocket_transport import (
    WebsocketObfuscated,
    WebsocketTCP,
    install_websocket_transport,
    websocket_host,
)


def test_production_datacenters_have_websocket_hosts() -> None:
    for dc_id in range(1, 6):
        websocket_host(DataCenter.PROD[dc_id])
    for ip in DataCenter.PROD_MEDIA.values():
        websocket_host(ip)


def test_obfuscated_websocket_prefers_websocket_tcp() -> None:
    method_order = WebsocketObfuscated.__mro__
    assert method_order.index(WebsocketTCP) < method_order.index(TCP)


def test_install_switches_pyrogram_transport() -> None:
    import pyrogram.connection.connection as connection

    original = connection.TCPAbridged
    try:
        install_websocket_transport()
        assert connection.TCPAbridged is WebsocketObfuscated
    finally:
        connection.TCPAbridged = original


async def test_recv_returns_bytes_that_arrive_after_the_call() -> None:
    transport = WebsocketTCP(False, None)

    async def produce() -> None:
        await asyncio.sleep(0.01)
        transport._buffer.extend(b"xyz")
        transport._ready.set()

    producer = asyncio.create_task(produce())
    payload = await transport.recv(3)
    await producer
    assert payload == b"xyz"


async def test_connect_does_not_send_protocol_pings(monkeypatch: MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    class FakeSocket:
        def __aiter__(self) -> "FakeSocket":
            return self

        async def __anext__(self) -> bytes:
            await asyncio.Future()
            return b""

        async def close(self) -> None:
            return None

    async def fake_connect(*args: object, **kwargs: object) -> FakeSocket:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return FakeSocket()

    monkeypatch.setattr("userbot.network.websocket_transport.connect", fake_connect)
    transport = WebsocketTCP(False, None)
    await transport.connect(("149.154.175.53", 443))
    try:
        kwargs = calls["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs["ping_interval"] is None
        args = calls["args"]
        assert isinstance(args, tuple)
        assert args[0] == "wss://pluto.web.telegram.org/apiws"
    finally:
        await transport.close()


async def test_recv_returns_none_when_stream_closes_short() -> None:
    transport = WebsocketTCP(False, None)
    transport._buffer.extend(b"ab")
    transport._closed = True
    assert await transport.recv(4) is None
