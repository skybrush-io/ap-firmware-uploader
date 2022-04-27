from dataclasses import dataclass
from enum import Enum
from itertools import repeat
from typing import Callable, Optional

from ap_uploader.io.factory import create_transport

from .connection import BootloaderConnection
from .errors import NotSupportedError
from .firmware import Firmware, load_firmware
from .io.serial import SerialPortTransport
from .io.udp import UDPTransport
from .protocol import PROG_MULTI_MAX_PAYLOAD_LENGTH
from .utils import crc32


__all__ = (
    "Uploader",
    "UploadStep",
    "UploaderEvent",
    "UploaderLifecycleEvent",
    "UploadProgressEvent",
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


@dataclass
class UploaderLifecycleEvent(UploaderEvent):
    """Basic lifecycle events emitted by the uploader task when it is started
    or finished. Also emitted for timeouts."""

    type: str
    """The type of the event."""


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


class Uploader:
    _firmware: Optional[Firmware]
    """The firmware that the uploader will upload; ``None`` if it is not loaded
    yet.
    """

    def __init__(self):
        """Constructor."""
        self._firmware = None

    async def load_firmware(self, path: str) -> None:
        self._firmware = await load_firmware(path)

    async def upload_firmware(
        self, port: str, *, on_event: Callable[[UploaderEvent], None]
    ) -> None:
        firmware = self._firmware
        if firmware is None:
            raise RuntimeError("firmware is not loaded yet")

        on_event(UploaderLifecycleEvent(type="started"))
        on_event(UploadStepEvent(step=UploadStep.CONNECTING))

        try:
            transport = create_transport(port)
            async with BootloaderConnection(transport) as connection:
                await connection.ensure_in_bootloader()

                flash_size = await connection.get_flash_memory_size()
                bl_rev = await connection.get_bootloader_revision()
                if bl_rev < 4:
                    raise NotSupportedError(
                        f"Bootloader version {bl_rev} not supported"
                    )

                board_id = await connection.get_board_id()
                board_revision = await connection.get_board_revision()
                serial_number = (await connection.get_serial_number()).hex(
                    ":", bytes_per_sep=1
                )

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
                # TODO(ntamas): this is potentially CPU-intensive, make it faster
                expected_crc = crc32(
                    repeat(255, flash_size - firmware.image_size), state=firmware.crc
                )
                observed_crc = await connection.get_flash_memory_crc()
                if expected_crc != observed_crc:
                    on_event(
                        CRCMismatchEvent(expected=expected_crc, observed=observed_crc)
                    )

                on_event(UploadStepEvent(step=UploadStep.REBOOTING))
                await connection.reboot()

                on_event(UploadStepEvent(step=UploadStep.FINISHED))
        except TimeoutError:
            on_event(UploaderLifecycleEvent(type="timeout"))
        finally:
            on_event(UploaderLifecycleEvent(type="finished"))
