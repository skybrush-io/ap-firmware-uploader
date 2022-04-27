"""IO abstraction layer for the ArudPilot/PX4 firmware uploader."""

from abc import abstractmethod
from anyio import EndOfStream
from contextlib import AbstractAsyncContextManager

__all__ = ("EndOfStream", "Transport")


class Transport(AbstractAsyncContextManager):
    """Base class for transports that the uploader supports. A transport is
    simply an object that can do two things:

    1. takes raw bytes and sends them to a remote peer

    2. receives bytes from the remote peer and returns them

    Concrete implementations of this class can deal with serial links, UDP
    and TCP connections and so on.
    """

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self) -> None:
        """Closes the transport. No more bytes shall be sent after calling
        this method on the transport. The default implementation does nothing.
        """
        pass  # pragma: no cover

    @abstractmethod
    async def receive(self) -> bytes:
        """Receives pending bytes from the remote peer and returns them.

        Returns:
            the received bytes. Guaranteed to be at least one byte.

        Raises:
            EndOfStream: if the remote peer closed the connection and there are
                no more bytes to receive
        """
        raise NotImplementedError

    @abstractmethod
    async def send(self, data: bytes) -> None:
        """Sends the given bytes to the remote peer.

        Parameters:
            data: the bytes to send
        """
        raise NotImplementedError
