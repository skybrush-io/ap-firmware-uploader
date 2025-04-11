"""In-memory transport, mostly for testing purposes."""

from anyio import create_memory_object_stream
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from typing import Optional

from .base import Transport

__all__ = ("InMemoryTransport",)


class InMemoryTransport(Transport):
    """In-memory transport, mostly for testing purposes."""

    _receiver: ObjectReceiveStream[bytes]
    _sender: ObjectSendStream[bytes]
    _peer: "InMemoryTransport"

    def __init__(
        self,
        buffer_size: int = 0,
        *,
        _sender: Optional[ObjectSendStream[bytes]] = None,
        _receiver: Optional[ObjectReceiveStream[bytes]] = None,
        _peer: Optional["InMemoryTransport"] = None,
    ):
        """Constructor."""
        if _peer is None:
            sender_tx, sender_rx = create_memory_object_stream[bytes](buffer_size)
            receiver_tx, receiver_rx = create_memory_object_stream[bytes](buffer_size)

            self._sender = sender_tx
            self._receiver = receiver_rx

            self._peer = InMemoryTransport(
                _sender=receiver_tx, _receiver=sender_rx, _peer=self
            )
        else:
            assert _sender is not None
            assert _receiver is not None
            self._sender = _sender
            self._receiver = _receiver
            self._peer = _peer

    async def __aenter__(self):
        await self._sender.__aenter__()
        await self._receiver.__aenter__()
        return await super().__aenter__()

    async def aclose(self) -> None:
        await self._sender.aclose()
        await self._receiver.aclose()

    async def receive(self) -> bytes:
        return await self._receiver.receive()

    async def send(self, data: bytes) -> None:
        return await self._sender.send(data)

    @property
    def peer(self) -> "InMemoryTransport":
        return self._peer
