"""UDP transport layer for the ArduPilot/PX4 uploader."""

from anyio import (
    create_memory_object_stream,
    create_task_group,
    create_udp_socket,
    sleep_forever,
    ClosedResourceError,
    TASK_STATUS_IGNORED,
)
from anyio.abc import SocketAttribute, UDPSocket
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from contextlib import aclosing, asynccontextmanager
from typing import AsyncIterator, Dict, Optional, Tuple

from .base import Transport

__all__ = ("UDPTransport",)

IPAddressAndPort = Tuple[str, int]
"""Type alias for a pair of an IP address and a port."""


class SharedUDPSocket:
    """Shared UDP socket that can be used by multiple readers and writers if
    they are connected to different target IP addresses and ports.
    """

    _buffer_size: int
    """Buffer size for incoming and outgoing datagrams, measured in datagrams
    (not bytes).
    """

    _local_address: IPAddressAndPort
    """The local address of the socket that it will bind to."""

    _socket: Optional[UDPSocket]
    """The UDP socket wrapped by the wrapper class."""

    _subscriptions: Dict[IPAddressAndPort, "SharedUDPSocketSubscription"]
    """Dictionary mapping IP address and port pairs to the corresponding
    subscription objects. Each subscription object holds a size-bounded queue
    with the incoming datagrams from that IP address and port.
    """

    _send_queue: Optional[MemoryObjectSendStream] = None

    def __init__(self, local_host: str, local_port: int = 0, *, buffer_size: int = 64):
        """Constructor.

        Parameters:
            local_host: the host or IP address to listen on
            local_port: the UDP port to listen on for incoming datagrams; also
                used for sending
        """
        self._buffer_size = buffer_size
        self._local_address = local_host, local_port
        self._socket = None
        self._subscriptions = {}

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

    async def send(self, data: bytes, destination: IPAddressAndPort) -> None:
        """Sends a UDP datagram to the given host and port.

        Parameters:
            data: the data to send
            destination: the destination host and port to send the data to
        """
        if self._send_queue is None:
            raise ClosedResourceError()
        await self._send_queue.send((data, destination[0], destination[1]))

    @asynccontextmanager
    async def subscribed_to(
        self, peer: IPAddressAndPort
    ) -> AsyncIterator["SharedUDPSocketSubscription"]:
        if peer in self._subscriptions:
            raise RuntimeError("Another subscription already exists for this address")

        subscription = self._subscriptions[peer] = SharedUDPSocketSubscription(
            self._buffer_size
        )
        try:
            async with aclosing(subscription):
                yield subscription
        finally:
            self._subscriptions.pop(peer, None)

    async def run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        async with self.use():
            task_status.started()
            await sleep_forever()

    @asynccontextmanager
    async def use(self) -> AsyncIterator[None]:
        """Context manager that runs the receive loop of the socket in a
        background task group when the context is entered.
        """
        if self._socket is not None:
            raise RuntimeError("SharedUDPSocket is already being used")

        host, port = self._local_address
        socket = await create_udp_socket(local_host=host, local_port=port)

        async with aclosing(socket):
            send_queue_tx, send_queue_rx = create_memory_object_stream(
                self._buffer_size
            )
            async with create_task_group() as tg:
                tg.start_soon(self._reader_worker, socket)
                tg.start_soon(self._writer_worker, socket, send_queue_rx)
                try:
                    self._socket = socket
                    self._send_queue = send_queue_tx
                    yield
                finally:
                    self._send_queue = None
                    self._socket = None

    async def _reader_worker(self, socket: UDPSocket):
        """Asynchronous task that reads datagrams from the shared UDP socket
        and dispatches them to callers that are currently being blocked in
        ``receive()``.
        """
        while True:
            try:
                payload, sender = await socket.receive()
            except ClosedResourceError:
                break

            sub = self._subscriptions.get(sender, None)
            if sub:
                sub.dispatch(payload)

    async def _writer_worker(
        self,
        socket: UDPSocket,
        queue: MemoryObjectReceiveStream,
        *,
        task_status=TASK_STATUS_IGNORED
    ) -> None:
        """Asynchronous task that writes datagrams to the shared UDP socket,
        according to the requests posted in by callers via ``send()``.
        """
        async with queue:
            task_status.started()
            async for args in queue:
                if args is None:
                    break
                else:
                    await socket.sendto(*args)


class SharedUDPSocketSubscription:
    """Represents a subscription to datagrams from a shared UDP socket that are
    sent from a given IP address and port.
    """

    _tx: MemoryObjectSendStream[bytes]
    _rx: MemoryObjectReceiveStream[bytes]

    def __init__(self, buffer_size: int):
        """Constructor."""
        self._tx, self._rx = create_memory_object_stream(max_buffer_size=buffer_size)

    async def aclose(self) -> None:
        await self._tx.aclose()
        await self._rx.aclose()

    async def receive(self) -> bytes:
        return await self._rx.receive()

    def dispatch(self, data: bytes) -> None:
        self._tx.send_nowait(data)


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
    async def create(cls, host: str, port: int) -> AsyncIterator["UDPTransport"]:
        shared_socket = SharedUDPSocket("0.0.0.0", 14555)
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
    def local_address(self) -> Tuple[str, int]:
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
    def remote_port(self) -> int:
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
