"""Serial port transport layer for the ArduPilot/PX4 uploader."""

from anyio import (
    create_memory_object_stream,
    create_task_group,
    from_thread,
    move_on_after,
    to_thread,
    BusyResourceError,
    CancelScope,
    ClosedResourceError,
    EndOfStream,
    Event,
)
from anyio.abc import ByteStream, TaskGroup
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.memory import MemoryObjectSendStream
from functools import partial
from serial import Serial, serial_for_url
from serial.serialutil import PortNotOpenError, SerialException
from typing import Any, Callable, Coroutine, Optional, TypeVar

from .base import Transport

__all__ = ("SerialPortTransport",)

T = TypeVar("T", bound="SerialPortByteStream")
T2 = TypeVar("T2", bound="SerialPortTransport")


class SerialPortByteStream(ByteStream):
    """anyio-compatible ByteStream_ object that works with an underlying
    serial port.
    """

    max_read_size: int
    """Maximum number of bytes that we attempt to read from the port in a
    single chunk.
    """

    _port: Serial
    """The ``pyserial`` serial port that this object wraps."""

    _reader_stream: Optional[BufferedByteReceiveStream] = None
    """A receive stream that will yield the bytes received from the serial
    port. ``None`` if the byte stream is not open.
    """

    _reader_thread_queue: Optional[MemoryObjectSendStream] = None
    """The sending endpoint of the queue used by the reader thread. This is
    used to signal the thread to stop when needed. Must be called when we are
    not interested in any data received from the serial port any more.
    """

    _task_group: Optional[TaskGroup] = None
    """The ``anyio`` task group that encapsulates the reader and writer
    worker task; ``None`` if the byte stream is not open.
    """

    _writer_done: Optional[Event] = None
    """Event that the writer thread will set when it is about to terminate."""

    _writer_thread_queue: Optional[MemoryObjectSendStream] = None
    """The sending endpoint of the queue used by the worker thread. This is
    used to signal the thread to stop when needed. Must be called when we know
    we will not send any more data through the serial port.
    """

    @classmethod
    def from_url(cls, url: str, *, max_read_size: int = 1024, **kwds: Any):
        """Creates a serial port from the given ``pyserial`` URL specification.
        See https://pyserial.readthedocs.io/en/latest/url_handlers.html for
        more details.
        """
        kwds["do_not_open"] = True
        port = serial_for_url(url, **kwds)
        return cls(port, max_read_size=max_read_size)

    def __init__(self, port: Serial, *, max_read_size: int = 1024) -> None:
        """Constructor.

        Creates a new SerialPortByteStream that wraps a given ``pyserial``
        serial port.

        Parameters:
            port: the port to wrap
            max_read_size: maximum number of bytes that we attempt to read in
                a single chunk
        """
        self._port = port
        self.max_read_size = max_read_size

    async def __aenter__(self: T) -> T:
        if self._task_group is not None:
            raise BusyResourceError("opening serial port")

        if not self._port.is_open:
            await to_thread.run_sync(self._port.open)

        task_group = create_task_group()
        await task_group.__aenter__()

        reader_tx, rx = create_memory_object_stream(0, item_type=bytes)
        task_group.start_soon(
            partial(
                to_thread.run_sync, self._read_worker, reader_tx.send, cancellable=True
            )
        )
        reader_stream = BufferedByteReceiveStream(rx)

        writer_tx, rx = create_memory_object_stream(0, item_type=bytes)
        writer_done = Event()
        task_group.start_soon(
            partial(
                to_thread.run_sync,
                self._write_worker,
                rx.receive,
                writer_done.set,
                cancellable=True,
            )
        )

        self._task_group = task_group
        self._reader_stream = reader_stream
        self._reader_thread_queue = reader_tx
        self._writer_done = writer_done
        self._writer_thread_queue = writer_tx

        return self

    async def __aexit__(self, *args: Any) -> None:
        await super().__aexit__(*args)  # calls aclose()
        assert self._task_group is not None
        try:
            await self._task_group.__aexit__(*args)
        finally:
            self._task_group = None

    def _read_worker(
        self, sender: Callable[[bytes], Coroutine[None, None, None]]
    ) -> None:
        """Runs the worker thread that is responsible for reading data from the
        serial port.
        """
        port = self._port
        try:
            while True:
                pending_bytes = port.in_waiting
                if not pending_bytes:
                    # Block until new data is available
                    data = port.read()
                else:
                    # Read at most max_read_size bytes
                    data = port.read(min(pending_bytes, self.max_read_size))
                from_thread.run(sender, data)
        except SerialException:
            if self._reader_thread_queue is None:
                # we are closing the port so this is okay
                pass
            else:
                raise
        except (PortNotOpenError, ClosedResourceError):
            pass

    def _write_worker(
        self,
        receiver: Callable[[], Coroutine[None, None, bytes]],
        on_exit: Callable[[], None],
    ) -> None:
        """Runs the worker thread that is responsible for writing data to the
        serial port.
        """
        port = self._port
        try:
            while True:
                data = from_thread.run(receiver)
                port.write(data)
        except (PortNotOpenError, EndOfStream):
            pass
        finally:
            from_thread.run_sync(on_exit)

    async def aclose(self) -> None:
        """Closes the serial port."""
        reader_queue, self._reader_thread_queue = self._reader_thread_queue, None
        writer_queue, self._writer_thread_queue = self._writer_thread_queue, None
        writer_done, self._writer_done = self._writer_done, None

        try:
            # Close the producers; this will ensure that the worker threads
            # unblock and close themselves
            with CancelScope(shield=True):
                # Close the writer worker
                if writer_queue:
                    await writer_queue.aclose()

                # Now wait until the writer worker actually terminates
                if writer_done:
                    with move_on_after(3):
                        await writer_done.wait()

                # Close the reader worker
                if reader_queue:
                    await reader_queue.aclose()

                # Close the serial port
                if self._port.is_open:
                    await to_thread.run_sync(self._port.close)
        finally:
            # Now we can also invalidate the reader stream. We cannot do it
            # before this point because then we would prevent the user from
            # reading the pending data from the port
            self._reader_stream = None

    async def receive(self, max_bytes: int = 65536) -> bytes:
        """Reads at most the given number of bytes from the serial port.

        Returns:
            the bytes that were read
        """
        if self._reader_stream is None:
            raise ClosedResourceError()
        return await self._reader_stream.receive(max_bytes=max_bytes)

    async def receive_exactly(self, nbytes: int) -> bytes:
        """Reads exactly the given number of bytes from the serial port.

        Returns:
            the bytes that were read
        """
        if self._reader_stream is None:
            raise ClosedResourceError()
        return await self._reader_stream.receive_exactly(nbytes)

    async def receive_until(self, delimiter: bytes, max_bytes: int) -> bytes:
        """Reads at most the given number of bytes from the serial port or until
        a delimiter character is received.

        Returns:
            the bytes that were read
        """
        if self._reader_stream is None:
            raise ClosedResourceError()
        return await self._reader_stream.receive_until(delimiter, max_bytes)

    async def send(self, data: bytes) -> None:
        """Sends the given bytes to the serial port."""
        if self._writer_thread_queue is None:
            raise ClosedResourceError()
        await self._writer_thread_queue.send(data)

    async def send_eof(self) -> None:
        raise NotImplementedError("Serial ports cannot send EOF")

    @property
    def baudrate(self) -> int:
        """The baud rate of the serial port."""
        return self._port.baudrate  # type: ignore

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        self._port.baudrate = value

    @property
    def wrapped_port(self) -> Serial:
        """Returns the raw, wrapped ``pyserial`` port instance."""
        return self._port


class SerialPortTransport(Transport):
    """Serial port transport object that writes bytes to and reads bytes from
    a serial port.
    """

    _stream: SerialPortByteStream
    """A bidirectional byte stream that is connected to the serial port."""

    @classmethod
    def from_url(cls, url: str, *, max_read_size: int = 1024, **kwds: Any):
        """Creates a serial port transport from the given ``pyserial`` URL
        specification.

        See https://pyserial.readthedocs.io/en/latest/url_handlers.html for more
        details.
        """
        kwds["do_not_open"] = True
        port = serial_for_url(url, **kwds)
        return cls(port, max_read_size=max_read_size)

    def __init__(self, port: Serial, *, max_read_size: int = 1024) -> None:
        self._stream = SerialPortByteStream(port)

    async def __aenter__(self: T2) -> T2:
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._stream.__aexit__(*args)

    async def aclose(self) -> None:
        return await self._stream.aclose()

    async def receive(self) -> bytes:
        return await self._stream.receive()

    async def send(self, data: bytes) -> None:
        return await self._stream.send(data)
