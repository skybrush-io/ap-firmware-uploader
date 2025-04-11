from anyio import create_tcp_listener, BusyResourceError, ClosedResourceError
from anyio.abc import SocketAttribute, SocketStream
from anyio.streams.stapled import MultiListener
from pytest import fixture, raises

from ap_uploader.io.tcp import TCPTransport


TCPListener = MultiListener[SocketStream]


@fixture
async def listener() -> TCPListener:
    return await create_tcp_listener(local_host="127.0.0.1")


@fixture
async def transport_and_listener(
    listener: TCPListener,
) -> tuple[TCPTransport, TCPListener]:
    host, port = listener.extra(SocketAttribute.local_address)
    assert isinstance(port, int)

    transport = TCPTransport(host, port)
    return transport, listener


async def test_send_receive(
    transport_and_listener: tuple[TCPTransport, TCPListener], nursery
):
    transport, listener = transport_and_listener

    async def handler(stream: SocketStream) -> None:
        data = await stream.receive()
        await stream.send(data * 2)

    nursery.start_soon(listener.serve, handler)
    async with transport:
        await transport.send(b"spam")
        data = await transport.receive()
        assert data == b"spamspam"


async def test_send_receive_when_closed(
    transport_and_listener: tuple[TCPTransport, TCPListener],
):
    transport, _ = transport_and_listener
    with raises(ClosedResourceError):
        await transport.receive()
    with raises(ClosedResourceError):
        await transport.send(b"foo")


async def test_double_open(transport_and_listener: tuple[TCPTransport, TCPListener]):
    transport, _ = transport_and_listener
    async with transport:
        with raises(BusyResourceError):
            async with transport:
                pass
