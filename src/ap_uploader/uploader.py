import errno
import logging

from anyio import (
    create_task_group,
    get_cancelled_exc_class,
    Lock,
    CapacityLimiter,
    to_thread,
)
from anyio.abc import TaskGroup
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from functools import partial
from ipaddress import ip_address
from itertools import repeat
from typing import Any, AsyncIterable, AsyncIterator, Callable, Optional, TypeVar

from .connection import BootloaderConnection
from .errors import NotSupportedError
from .firmware import Firmware, load_firmware
from .io.base import Transport
from .io.udp import SharedUDPSocket, UDPTransport
from .io.serial import SerialPortTransport
from .protocol import PROG_MULTI_MAX_PAYLOAD_LENGTH
from .scanners.base import Scanner, UploadTarget
from .utils import crc32


__all__ = (
    "Uploader",
    "UploadStep",
    "UploaderEvent",
    "UploaderEventHandler",
    "UploaderLifecycleEvent",
    "UploadProgressEvent",
    "LogEvent",
    "CRCMismatchEvent",
)


class UploadStep(Enum):
    """Enum describing the steps of a firmware upload process."""

    CONNECTING = "connecting", "Connecting..."
    ERASING = "erasing", "Erasing..."
    UPLOADING = "uploading", "Uploading..."
    VERIFYING = "verifying", "Verifying..."
    REBOOTING = "rebooting", "Rebooting..."
    FINISHED = "finished", "Finished."

    def __new__(cls, *args, **kwds):
        obj = object.__new__(cls)
        obj._value_ = args[0]
        return obj

    def __init__(self, _: str, description: str):
        self._description_ = description

    def __str__(self):
        return self.value

    @property
    def description(self):
        return self._description_


class UploaderEvent:
    """Base class for events emitted by the uploader task."""

    __slots__ = ()


UploaderEventHandler = Callable[[str, UploaderEvent], None]
"""Type alias for functions that take an uploader event and do something with
it. Used by user interfaces to provide a callback that the uploader can call
when an event occurs.

The first argument is the source of the event (typically a unique string
identifying the uploader task), the second is the event itself.
"""


@dataclass
class UploaderLifecycleEvent(UploaderEvent):
    """Basic lifecycle events emitted by the uploader task when it is started
    or finished. Also emitted for timeouts."""

    type: str
    """The type of the event."""

    success: Optional[bool] = None
    """Whether the upload was successful; only valid for "finished" events."""

    cancelled: Optional[bool] = None
    """Whether the upload was cancelled; only valid for "finished" events."""


@dataclass
class UploadStepEvent(UploaderEvent):
    """Event that reports that the upload process has started a new step."""

    __slots__ = ("step",)

    step: UploadStep
    """The upload step that was started."""


@dataclass
class UploadProgressEvent(UploaderEvent):
    """Event that repoprts the current progress of the upload."""

    __slots__ = ("progress",)

    progress: float
    """Progress of the upload"""


@dataclass
class CRCMismatchEvent(UploaderEvent):
    """Event that is emitted by the uploader when the CRC of the uploaded
    firmware does not match the CRC of the flash memory after upload.
    """

    __slots__ = ("expected", "observed")

    expected: int
    """The expected CRC."""

    observed: int
    """The observed CRC."""


@dataclass
class LogEvent(UploaderEvent):
    """Debug event that may contain any arbitrary message with an associated
    log leve.
    """

    __slots__ = ("level", "message")

    level: int
    """Log level of the message"""

    message: str
    """The message itself"""


C = TypeVar("C", bound="Uploader")


class Uploader:
    _firmware: Optional[Firmware] = None
    """The firmware that the uploader will upload; ``None`` if it is not loaded
    yet.
    """

    _shared_udp_socket: Optional[SharedUDPSocket] = None
    """A shared UDP socket that will be used by the uploader tasks spawned from
    this uploader to communicate with upload targets. Constructed on-demand
    when the first UDP socket is needed.
    """

    _shared_udp_socket_lock: Lock

    _task_group: Optional[TaskGroup] = None
    """The task group used privately by the uploader to run its private tasks."""

    def __init__(self):
        self._shared_udp_socket_lock = Lock()

    def create_task_group(
        self,
        *,
        on_event: UploaderEventHandler,
        retries: int = 0,
        max_concurrency: int = 0,
    ) -> "UploaderTaskGroup":
        """Creates a task group that is responsible for running multiple upload
        tasks in parallel, with configurable retry counts, in a way that upload
        tasks are shielded from errors in other tasks.

        Parameters:
            port: the port to upload the firmware to. May be an IP address or
                the identifier of a serial port.
            on_event: a synchronous callback to call when an event happens
                during the upload process. The callback will be called with the
                port that the upload is targeting and the event itself.
                Typically this is tied to a user interface object so the UI is
                updated during the upload properly.
            retries: number of retries for failed tasks before the task group
                gives up on the task completely
            max_concurrency: maximum number of upload tasks that can be running
                in parallel in the task group. Zero or negative numbers mean
                infinity.
        """
        return UploaderTaskGroup(
            self, on_event=on_event, retries=retries, max_concurrency=max_concurrency
        )

    async def generate_targets_from(
        self, scanner: Scanner
    ) -> AsyncIterable[UploadTarget]:
        async for target in scanner.run():
            yield target

    async def get_shared_udp_socket(self) -> SharedUDPSocket:
        """Returns a shared UDP socket that listens on UDP port 14555 and can
        be used simultaneously by upload tasks and scanner tasks.
        """
        return await self._ensure_shared_udp_socket_is_up()

    async def load_firmware(self, path: str) -> None:
        """Loads the firmware file at the given path."""
        self._firmware = await load_firmware(path)

    async def upload_firmware(
        self, port: str, *, on_event: Callable[[UploaderEvent], None]
    ) -> None:
        """Runs an asynchronous task to upload the loaded firmware into the
        drone at the given port.

        Parameters:
            port: the port to upload the firmware to. May be an IP address or
                the identifier of a serial port.
            on_event: a synchronous callback to call when an event happens
                during the upload process. The callback will be called with the
                event as its only argument. Typically this is tied to a user
                interface object so the UI is updated during the upload properly.
        """
        firmware = self._firmware
        if firmware is None:
            raise RuntimeError("firmware is not loaded yet")

        on_event(UploaderLifecycleEvent(type="started"))
        on_event(UploadStepEvent(step=UploadStep.CONNECTING))

        success, cancelled = False, False

        try:
            transport = await self._create_transport(port)
            async with BootloaderConnection(transport) as connection:
                await connection.ensure_in_bootloader()

                flash_size = await connection.get_flash_memory_size()
                bl_rev = await connection.get_bootloader_revision()
                if bl_rev < 4:
                    raise NotSupportedError(
                        f"Bootloader version {bl_rev} not supported"
                    )

                await connection.get_board_id()
                await connection.get_board_revision()
                (await connection.get_serial_number()).hex(":", bytes_per_sep=1)

                on_event(UploadStepEvent(step=UploadStep.ERASING))
                await connection.erase_flash_memory()

                on_event(UploadStepEvent(step=UploadStep.UPLOADING))

                # write_pointer will store the current address where the next
                # chunk will be written in the bootloader if we know it, or
                # `None` if we don't know it. The latter may happen if a
                # program_bytes() call raises a TimeoutError because in this
                # case we don't know whether our packet reached the bootloader
                # or not
                write_pointer: Optional[int] = 0
                total_bytes = len(firmware.image)
                while write_pointer is None or write_pointer < total_bytes:
                    if write_pointer is None:
                        if bl_rev >= 6:
                            write_pointer = await connection.get_write_pointer()
                        else:
                            raise RuntimeError(
                                "acknowledgment of PROG_WRITE command lost, "
                                "bootloader may be out of sync now"
                            )

                    assert write_pointer is not None

                    chunk = firmware.image[
                        write_pointer : (write_pointer + PROG_MULTI_MAX_PAYLOAD_LENGTH)
                    ]
                    try:
                        await connection.program_bytes(chunk)
                    except TimeoutError:
                        write_pointer = None
                    else:
                        write_pointer += len(chunk)
                        progress = write_pointer / total_bytes
                        on_event(UploadProgressEvent(progress=progress))

                on_event(UploadStepEvent(step=UploadStep.VERIFYING))
                expected_crc = await to_thread.run_sync(
                    crc32, repeat(255, flash_size - firmware.image_size), firmware.crc
                )
                observed_crc = await connection.get_flash_memory_crc()
                if expected_crc != observed_crc:
                    on_event(
                        CRCMismatchEvent(expected=expected_crc, observed=observed_crc)
                    )

                on_event(UploadStepEvent(step=UploadStep.REBOOTING))
                await connection.reboot()

                on_event(UploadStepEvent(step=UploadStep.FINISHED))

                success = True
        except get_cancelled_exc_class():
            cancelled = True
            raise
        except TimeoutError:
            on_event(UploaderLifecycleEvent(type="timeout"))
            raise
        finally:
            on_event(
                UploaderLifecycleEvent(
                    type="finished", success=success, cancelled=cancelled
                )
            )

    @asynccontextmanager
    async def use(self: C) -> AsyncIterator[C]:
        async with create_task_group() as tg:
            try:
                self._task_group = tg
                yield self
                tg.cancel_scope.cancel()
            finally:
                self._task_group = None

    async def _create_transport(self, spec: UploadTarget) -> Transport:
        """Creates a transport from a specification string.

        When the specification string is a valid IPv4 or IPv6 address, optionally
        followed by a colon and a port number, the returned transport will be
        an instance of UDPTransport_; otherwise the input assumed to be a serial
        port and the returned transport will be an instance of SerialTransport_.
        """
        address, sep, port = spec.partition(":")
        if sep:
            # We have found an IP address and a port
            shared_udp_socket = await self.get_shared_udp_socket()
            return UDPTransport(shared_udp_socket, address, int(port))

        try:
            ip_address(spec)
        except ValueError:
            # It must be a serial port
            return SerialPortTransport.from_url(spec)
        else:
            # It seems like an IP address without a port number, assume 14555
            shared_udp_socket = await self.get_shared_udp_socket()
            return UDPTransport(shared_udp_socket, spec, 14555)

    async def _ensure_shared_udp_socket_is_up(self) -> SharedUDPSocket:
        """Ensures that the shared UDP socket required to handle multiple
        concurrent uploads is open.

        Returns:
            the shared UDP socket
        """
        async with self._shared_udp_socket_lock:
            if self._shared_udp_socket is None:
                if self._task_group is None:
                    raise RuntimeError("uploader is not in use yet")

                self._shared_udp_socket = SharedUDPSocket("0.0.0.0", 14550)
                await self._task_group.start(self._shared_udp_socket.run)

        return self._shared_udp_socket


T = TypeVar("T", bound="UploaderTaskGroup")


class UploaderTaskGroup:
    """A task group that is responsible for running multiple upload tasks in
    parallel, with configurable retry counts, in a way that upload tasks are
    shielded from errors in other tasks.
    """

    _on_event: UploaderEventHandler
    """Callable that will be called with port identifiers and upload events
    during the upload process. This callback can be used to update an
    attached UI.
    """

    _limiter: Optional[CapacityLimiter] = None
    """An optional capacity limiter that can be used to limit the maximum
    number of concurrent upload tasks.
    """

    _retries: int
    """Number of retries for failed tasks before the task group gives up on the
    task completely.
    """

    _uploader: Uploader
    """The uploader that owns this task group."""

    _task_group: TaskGroup
    """The anyio task group that runs the upload tasks."""

    def __init__(
        self,
        uploader: Uploader,
        *,
        on_event: UploaderEventHandler,
        retries: int = 0,
        max_concurrency: int = 0,
    ):
        """Constructor."""
        self._limiter = (
            CapacityLimiter(max_concurrency) if max_concurrency > 0 else None
        )
        self._retries = retries
        self._on_event = on_event
        self._uploader = uploader
        self._task_group = create_task_group()

    async def __aenter__(self: T) -> T:
        await self._task_group.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> Optional[bool]:
        return await self._task_group.__aexit__(*args)

    def start_upload_to(self, port: str) -> None:
        self._task_group.start_soon(self._run_upload_in_protected_context, port)

    async def _run_upload_in_protected_context(self, port: str) -> None:
        on_event = partial(self._on_event, port)
        attempt = 0
        while attempt <= self._retries:
            acquired = False
            try:
                if self._limiter:
                    await self._limiter.acquire()
                    acquired = True
                await self._uploader.upload_firmware(port, on_event=on_event)
            except Exception as ex:
                if isinstance(ex, TimeoutError):
                    message = "Upload timed out"
                elif isinstance(ex, RuntimeError):
                    message = f"Upload failed: {ex}"
                elif isinstance(ex, OSError):
                    message = f"Upload failed: {ex.strerror}"
                else:
                    message = f"Upload failed: {ex!r}"

                should_retry = True
                if isinstance(ex, OSError) and ex.errno in (
                    errno.ENOENT,
                    errno.EADDRINUSE,
                ):
                    should_retry = False

                if not should_retry:
                    attempt = self._retries

                if attempt < self._retries:
                    on_event(LogEvent(logging.WARNING, f"{message}, retrying..."))
                else:
                    on_event(LogEvent(logging.ERROR, message))
                attempt += 1
            else:
                break
            finally:
                if acquired and self._limiter:
                    self._limiter.release()
