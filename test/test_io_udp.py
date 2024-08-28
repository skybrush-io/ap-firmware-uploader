from contextlib import aclosing
from anyio import create_udp_socket, move_on_after, ClosedResourceError
from anyio.abc import UDPSocket, SocketAttribute
from pytest import fixture, raises
from typing import Tuple

from ap_uploader.io.udp import UDPTransport


@fixture
async def udp() -> UDPSocket:
    return await create_udp_socket(local_host="127.0.0.1")


@fixture
async def transport_and_peer(udp: UDPSocket) -> Tuple[UDPTransport, UDPSocket]:
    host, port = udp.extra(SocketAttribute.local_address)
    assert isinstance(port, int)

    transport = UDPTransport(host, port)
    return transport, udp


async def test_send_receive_with_owned_socket(
    transport_and_peer: Tuple[UDPTransport, UDPSocket],
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
    transport = UDPTransport(host, port, socket=other_socket)

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
    transport_and_peer: Tuple[UDPTransport, UDPSocket], autojump_clock
):
    transport, _ = transport_and_peer
    other_peer = await create_udp_socket(local_host="127.0.0.1")
    async with aclosing(other_peer):
        async with transport:
            await other_peer.sendto(b"bacon", "127.0.0.1", transport.local_port)
            with move_on_after(1):
                await transport.receive()
                assert False


async def test_send_receive_when_closed(
    transport_and_peer: Tuple[UDPTransport, UDPSocket],
):
    transport, _ = transport_and_peer
    with raises(ClosedResourceError):
        await transport.receive()
    with raises(ClosedResourceError):
        await transport.send(b"foo")
    with raises(ClosedResourceError):
        transport.local_port
    with raises(ClosedResourceError):
        transport.local_address


async def test_local_port_and_address(
    transport_and_peer: Tuple[UDPTransport, UDPSocket],
):
    transport, _ = transport_and_peer
    async with transport:
        assert transport.local_address[1] == transport.local_port
