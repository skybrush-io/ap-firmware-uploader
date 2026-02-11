from contextlib import aclosing
from anyio import create_udp_socket, move_on_after, ClosedResourceError
from anyio.abc import UDPSocket, SocketAttribute
from pytest import fail, fixture, raises

from ap_uploader.io.udp import UDPTransport


@fixture
async def udp() -> UDPSocket:
    return await create_udp_socket(local_host="127.0.0.1")


@fixture
async def transport_and_peer(udp: UDPSocket) -> tuple[UDPTransport, UDPSocket]:
    host, port = udp.extra(SocketAttribute.local_address)
    assert isinstance(port, int)

    transport = UDPTransport(udp, host, port)
    return transport, udp


async def test_send_receive_with_owned_socket(
    transport_and_peer: tuple[UDPTransport, UDPSocket],
):
    transport, peer = transport_and_peer

    async with transport:
        await transport.send(b"spam")
        data, address = await peer.receive()
        assert data == b"spam"
        assert address[1] == transport.local_port

        await peer.sendto(b"bacon", "127.0.0.1", transport.local_port)
        data = await transport.receive()
        assert data == b"bacon"


async def test_send_receive_with_borrowed_socket(udp: UDPSocket):
    peer = udp
    host, port = udp.extra(SocketAttribute.local_address)
    assert isinstance(port, int)

    other_socket = await create_udp_socket(local_host="127.0.0.1")
    transport = UDPTransport(socket=other_socket, host=host, port=port)

    async with transport:
        assert transport.local_address == other_socket.extra(
            SocketAttribute.local_address
        )

        await transport.send(b"spam")
        data, address = await peer.receive()
        assert data == b"spam"
        assert address[1] == other_socket.extra(SocketAttribute.local_port)

        await peer.sendto(
            b"bacon", "127.0.0.1", other_socket.extra(SocketAttribute.local_port)
        )
        data = await transport.receive()
        assert data == b"bacon"


async def test_receive_from_other_socket(
    transport_and_peer: tuple[UDPTransport, UDPSocket], autojump_clock
):
    transport, _ = transport_and_peer
    other_peer = await create_udp_socket(local_host="127.0.0.1")
    async with aclosing(other_peer):
        async with transport:
            await other_peer.sendto(b"bacon", "127.0.0.1", transport.local_port)
            with move_on_after(1):
                await transport.receive()
                fail("No response received in time")


async def test_send_receive_when_closed(
    transport_and_peer: tuple[UDPTransport, UDPSocket],
):
    transport, _ = transport_and_peer
    with raises(ClosedResourceError):
        await transport.receive()
    with raises(ClosedResourceError):
        await transport.send(b"foo")
    with raises(ClosedResourceError):
        _ = transport.local_port
    with raises(ClosedResourceError):
        _ = transport.local_address


async def test_local_port_and_address(
    transport_and_peer: tuple[UDPTransport, UDPSocket],
):
    transport, _ = transport_and_peer
    async with transport:
        assert transport.local_address[1] == transport.local_port
