from __future__ import annotations

from anyio import (
    create_memory_object_stream,
    create_task_group,
    create_udp_socket,
    sleep_forever,
    ClosedResourceError,
    TASK_STATUS_IGNORED,
    WouldBlock,
)
from anyio.abc import SocketAttribute, UDPSocket
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from contextlib import aclosing, asynccontextmanager
from socket import SOL_SOCKET, SO_BROADCAST
from typing import AsyncIterator, Optional


IPAddressAndPort = tuple[str, int]
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

    _subscriptions: dict[IPAddressAndPort, SharedUDPSocketSubscription]
    """Dictionary mapping IP address and port pairs to the corresponding
    subscription objects. Each subscription object holds a size-bounded queue
    with the incoming datagrams from that IP address and port.
    """

    _send_queue: Optional[MemoryObjectSendStream] = None
    """Queue in which the packets to be sent from the shared UDP socket are
    placed by other tasks.
    """

    _unmatched_queue: Optional[
        MemoryObjectSendStream[tuple[bytes, IPAddressAndPort]]
    ] = None
    """Queue in which the unmatched packets are placed by the shared UDP socket."""

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

    def allow_broadcasts(self, enabled: bool = True) -> None:
        """Enables or disables the reception of broadcast packets on this
        socket.
        """
        if self._socket is None:
            raise ClosedResourceError()

        self._socket.extra(SocketAttribute.raw_socket).setsockopt(
            SOL_SOCKET, SO_BROADCAST, 1 if enabled else 0
        )

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

    async def run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        async with self.use():
            task_status.started()
            await sleep_forever()

    @asynccontextmanager
    async def subscribed_to(
        self, peer: IPAddressAndPort
    ) -> AsyncIterator[SharedUDPSocketSubscription]:
        """Async context manager that subscribes to datagrams coming from the
        given IP address and port pair while the execution is within the
        context.

        Parameters:
            peer: the IP address and port to subscribe to
        """
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

    @asynccontextmanager
    async def unmatched_handler(
        self, buffer_size: int = 256
    ) -> AsyncIterator[MemoryObjectReceiveStream[tuple[bytes, IPAddressAndPort]]]:
        """Async context manager that registers a handler for unmatched datagrams
        while the execution is within the context, and returns a queue in which
        the unmatched datagrams can be received along with their senders.
        """
        if self._unmatched_queue:
            raise RuntimeError("another unmatched packet handler is already registered")

        self._unmatched_queue, rx = create_memory_object_stream(buffer_size)
        async with self._unmatched_queue:
            async with rx:
                try:
                    yield rx
                finally:
                    self._unmatched_queue = None

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
            elif self._unmatched_queue:
                try:
                    self._unmatched_queue.send_nowait((payload, sender))
                except WouldBlock:
                    # packet dropped
                    pass

    async def _writer_worker(
        self,
        socket: UDPSocket,
        queue: MemoryObjectReceiveStream,
        *,
        task_status=TASK_STATUS_IGNORED,
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
