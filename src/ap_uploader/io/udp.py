"""UDP transport layer for the ArduPilot/PX4 uploader."""

from __future__ import annotations

from anyio import (
    create_udp_socket,
    ClosedResourceError,
)
from anyio.abc import SocketAttribute, UDPSocket
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from ap_uploader.network import (
    IPAddressAndPort,
    SharedUDPSocket,
    SharedUDPSocketSubscription,
)

from .base import Transport

__all__ = ("UDPTransport",)


class UDPTransport(Transport):
    """UDP transport that is using a shared UDP socket for sending datagrams and
    subscribes to incoming datagrams arriving to the shared UDP socket, restricted
    to a given remote host and port.
    """

    _remote_address: IPAddressAndPort
    """The remote host and port to send datagrams to."""

    _shared_socket: SharedUDPSocket
    """The shared UDP socket that the transport will use for sending datagrams."""

    _subscription: Optional[SharedUDPSocketSubscription] = None
    """Subscription to UDP datagrams sent by the remote host and port; ``None``
    if the transport is closed.
    """

    @classmethod
    @asynccontextmanager
    async def create(cls, host: str, port: int) -> AsyncIterator[UDPTransport]:
        shared_socket = SharedUDPSocket("0.0.0.0", 14550)
        async with shared_socket.use():
            transport = cls(shared_socket, host, port)
            async with transport:
                yield transport

    def __init__(self, socket: SharedUDPSocket, host: str, port: int):
        """Constructor.

        Parameters:
            socket: the shared UDP socket used for sending outbound datagrams and
                for subscribing to incoming datagrams
            host: the host that the transport will send datagrams to
            port: the port that the transport will send datagrams to
        """
        self._remote_address = (host, port)
        self._shared_socket = socket
        self._subscription_ctx = None
        self._subscription = None

    async def __aenter__(self):
        self._subscription_ctx = self._shared_socket.subscribed_to(self._remote_address)
        self._subscription = await self._subscription_ctx.__aenter__()
        return self

    async def aclose(self):
        if self._subscription:
            sub, self._subscription = self._subscription, None
            await sub.aclose()
        if self._subscription_ctx:
            ctx, self._subscription_ctx = self._subscription_ctx, None
            await ctx.__aexit__(None, None, None)

    @property
    def local_address(self) -> tuple[str, int]:
        """The IP address and port that the transport is listening on."""
        return self._shared_socket.local_address

    @property
    def local_port(self) -> int:
        """The port that the transport is listening on."""
        return self._shared_socket.local_port

    @property
    def remote_address(self) -> IPAddressAndPort:
        """The IP address and port that the transport is sending datagrams to."""
        return self._remote_address

    @property
    def remote_host(self) -> str:
        """The remote host that the transport is sending datagrams to."""
        return self._remote_address[0]

    @property
    def remote_port(self) -> int:
        """The remote port that the transport is sending datagrams to."""
        return self._remote_address[1]

    async def receive(self) -> bytes:
        if self._subscription is None:
            raise ClosedResourceError()
        return await self._subscription.receive()

    async def send(self, data: bytes) -> None:
        await self._shared_socket.send(data, self._remote_address)


class UDPListenerTransport(Transport):
    """UDP transport that is listening for incoming UDP datagrams on a specific
    address and latches on to the source host and port pair that first sends a
    datagram to this address.
    """

    _local_address: IPAddressAndPort
    """The local host and port to listen on."""

    _remote_address: Optional[IPAddressAndPort] = None
    """The remote host and port that the listener considers itself being
    connected to.
    """

    _socket: Optional[UDPSocket] = None
    """The UDP socket used for receiving and sending."""

    def __init__(self, host: str = "", port: int = 0):
        """Constructor.

        Parameters:
            host: the host or IP address to listen for incoming datagrams on
            port: the UDP port to listen for incoming datagrams on
        """
        self._local_address = (host, port)

    async def __aenter__(self):
        self._socket = await create_udp_socket(
            local_host=self._local_address[0], local_port=self._local_address[1]
        )
        return self

    async def aclose(self):
        if self._socket:
            socket, self._socket = self._socket, None
            await socket.aclose()

    @property
    def local_address(self) -> IPAddressAndPort:
        """The IP address and port that the transport is currently listening on."""
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

    @property
    def remote_address(self) -> Optional[IPAddressAndPort]:
        """The IP address and port that the transport considers itself being
        connected to.
        """
        return self._remote_address

    @property
    def remote_host(self) -> Optional[str]:
        """The IP address that the transport considers itself being
        connected to.
        """
        return self._remote_address[0] if self._remote_address else None

    @property
    def remote_port(self) -> Optional[int]:
        """The remote port that the transport considers itself being
        connected to.
        """
        return self._remote_address[1] if self._remote_address else None

    async def receive(self) -> bytes:
        if self._socket is None:
            raise ClosedResourceError()

        while True:
            # TODO(ntamas): implement connection timeout

            payload, sender = await self._socket.receive()
            if not self._remote_address:
                self._remote_address = sender
            elif sender != self._remote_address:
                continue

            return payload

    async def send(self, data: bytes) -> None:
        if self._socket is None:
            raise ClosedResourceError()

        if not self._remote_address:
            raise RuntimeError("socket is not connected")

        await self._socket.sendto(data, *self._remote_address)


async def _test_udp():
    from anyio import create_task_group, create_udp_socket, sleep

    async def producer(*, task_status):
        socket = await create_udp_socket(local_host="192.168.1.9", local_port=12345)
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
        async with UDPTransport.create(host=address[0], port=address[1]) as transport:
            task_status.started()
            await transport.send(b"start!\n")
            while True:
                data = await transport.receive()
                print("Received", data)

    async with create_task_group() as tg:
        socket = await tg.start(producer)
        await tg.start(consumer, socket)


async def _test_udp_listener():
    from anyio import create_task_group, create_udp_socket, sleep

    async def consumer(port, *, task_status):
        socket = await create_udp_socket(local_host="127.0.0.1")
        task_status.started(socket)

        async with socket:
            await socket.sendto(b"start!\n", "127.0.0.1", port)
            while True:
                data, _ = await socket.receive()
                print("Received", data)
                if data == b"end":
                    break

    async def producer(*, task_status):
        async with UDPListenerTransport("127.0.0.1") as transport:
            task_status.started(transport.local_port)
            data = await transport.receive()
            if data == b"start!\n":
                for i in range(10):
                    await transport.send(str(i).encode("ascii"))
                    await sleep(1)
                await transport.send(b"end")

    async with create_task_group() as tg:
        port = await tg.start(producer)
        await tg.start(consumer, port)


if __name__ == "__main__":
    from anyio import run

    run(_test_udp_listener)
