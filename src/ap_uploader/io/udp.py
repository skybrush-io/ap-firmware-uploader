"""UDP transport layer for the ArduPilot/PX4 uploader."""

from anyio import create_udp_socket, ClosedResourceError
from anyio.abc import SocketAttribute, UDPSocket
from typing import Optional, Tuple

from .base import Transport

__all__ = ("UDPTransport",)


class UDPTransport(Transport):
    """UDP transport layer for the ArduPilot/PX4 uploader."""

    remote_host: str
    """The remote host to send packets to."""

    remote_port: int
    """The remote port to send packets to."""

    _socket: Optional[UDPSocket]
    """The UDP socket that the transport will use for sending packets."""

    _socket_owned: bool
    """Whether the socket is owned by the transport. If this is ``True``, the
    socket will be closed when the transport is not used any more.
    """

    def __init__(self, host: str, port: int, *, socket: Optional[UDPSocket] = None):
        """Constructor.

        Parameters:
            host: the host that the transport will send packets to
            port: the port that the transport will send packets to
            socket: when specified, the given socket will be used to send
                packets. When ``None``, a new socket will be created and it
                will be closed when the transport is not used any more. The
                socket must _not_ be connected.
        """
        self.remote_host = host
        self.remote_port = port
        self._sender = (host, port)

        self._socket_owned = socket is None
        self._socket = socket

    async def __aenter__(self):
        if self._socket_owned:
            assert self._socket is None
            self._socket = await create_udp_socket(
                local_host="0.0.0.0", local_port=14550
            )
        return self

    async def aclose(self):
        if not self._socket_owned:
            return

        if self._socket:
            try:
                await self._socket.aclose()
            finally:
                self._socket = None
        else:
            pass  # pragma: no cover

    @property
    def local_address(self) -> Tuple[str, int]:
        """The IP address and port that the transport is listening on."""
        if self._socket is None:
            raise ClosedResourceError()

        host, port = self._socket.extra(SocketAttribute.local_address)
        assert isinstance(port, int)

        return host, port

    @property
    def local_port(self) -> int:
        """The port that the transport is listening on."""
        if self._socket is None:
            raise ClosedResourceError()

        port = self._socket.extra(SocketAttribute.local_port)
        assert isinstance(port, int)

        return port

    async def receive(self) -> bytes:
        if self._socket is None:
            raise ClosedResourceError()

        socket = self._socket

        while True:
            payload, sender = await socket.receive()
            if sender == self._sender:
                return payload

            # packet sent from elsewhere, ignore it

    async def send(self, data: bytes) -> None:
        if self._socket is None:
            raise ClosedResourceError()
        await self._socket.sendto(data, self.remote_host, self.remote_port)


async def _test_udp():
    from anyio import create_task_group, create_udp_socket, sleep

    async def producer(*, task_status):
        socket = await create_udp_socket(local_host="192.168.1.10", local_port=12345)
        task_status.started(socket)

        payload, sender = await socket.receive()

        i = 0
        while i < 100:
            await socket.sendto(str(i).encode("ascii"), *sender)
            await sleep(1)
            i += 1

        await socket.aclose()

    async def consumer(socket, *, task_status):
        address = socket.extra(SocketAttribute.local_address)
        transport = UDPTransport(host=address[0], port=address[1])
        async with transport:
            task_status.started()
            await transport.send(b"start!\n")
            while True:
                data = await transport.receive()
                print("Received", data)

    async with create_task_group() as tg:
        socket = await tg.start(producer)
        await tg.start(consumer, socket)


if __name__ == "__main__":
    from anyio import run

    run(_test_udp)
