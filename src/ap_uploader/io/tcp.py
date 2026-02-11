"""TCP transport layer for the ArduPilot/PX4 uploader."""

from anyio import BusyResourceError, ClosedResourceError, connect_tcp
from anyio.abc import ByteStream

from .base import Transport

__all__ = ("TCPTransport",)


class TCPTransport(Transport):
    """TCP transport layer for the ArduPilot/PX4 uploader."""

    remote_host: str
    """The remote host to connect to."""

    remote_port: int
    """The remote port to connect to."""

    max_bytes: int
    """Maximum number of bytes to receive from the TCP connection in a
    single batch.
    """

    _stream: ByteStream | None = None
    """The byte stream that the transport will use for sending packets."""

    def __init__(self, host: str, port: int, *, max_bytes: int = 65536):
        """Constructor.

        Parameters:
            host: the host that the transport will connect to
            port: the port that the transport will connect to
            max_bytes: m
        """
        self.max_bytes = max_bytes
        self.remote_host = host
        self.remote_port = port

    async def __aenter__(self):
        if self._stream is not None:
            raise BusyResourceError("opening TCP connection")

        self._stream = await connect_tcp(self.remote_host, self.remote_port)

        return self

    async def aclose(self):
        if self._stream:
            try:
                await self._stream.aclose()
            finally:
                self._stream = None
        else:
            pass  # pragma: no cover

    async def receive(self) -> bytes:
        if self._stream is None:
            raise ClosedResourceError()

        return await self._stream.receive(self.max_bytes)

    async def send(self, data: bytes) -> None:
        if self._stream is None:
            raise ClosedResourceError()

        await self._stream.send(data)
