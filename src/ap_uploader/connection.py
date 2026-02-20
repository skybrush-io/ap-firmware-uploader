from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Callable, Literal, TypeVar, cast

from anyio import Lock, fail_after

from .errors import ExcessDataError
from .io.base import Transport
from .protocol import (
    GET_SYNC,
    NEED_MORE_DATA,
    Command,
    DeviceInfoItem,
    ExcessData,
    Protocol,
    Response,
)

if TYPE_CHECKING:
    from .mavlink import MAVLink, MAVLinkHeartbeatMessage, MAVLinkMessage

__all__ = ("BootloaderConnection",)

C = TypeVar("C", bound="BootloaderConnection")


class BootloaderConnection(AbstractAsyncContextManager):
    """Connection class that communicates with the bootloader of an
    ArduPilot / PX4 device using a given transport.
    """

    _lock: Lock
    """Lock to ensure that only one command is being processed at a time in
    this connection object.
    """

    _mavlink: MAVLink | None = None
    """Local MAVLink protocol instance, needed if we have to send a MAVLink
    reboot command to ensure that the other end is in the bootloader."""

    _mode: Literal["bootloader", "mavlink", "unknown"] = "unknown"
    """The current mode of the connection. "bootloader" means that we are in
    sync with the bootloader and can send bootloader commands, "mavlink" means
    that we are in sync with the MAVLink firmware and can send MAVLink commands,
    "unknown" means that we don't know which one we are in, and we might have to
    try both to figure it out.
    """

    _protocol: Protocol
    """The protocol object that keeps track of the state of communication with
    the bootloader.
    """

    _system_id: int
    """MAVLink system ID to use for outbound packets."""

    _transport: Transport
    """The transport to use to communicate with the bootloader."""

    def __init__(self, transport: Transport, *, system_id: int = 254):
        """Constructor.

        Parameters:
            transport: the transport to use to communicate with the bootloader
            system_id: MAVLink system ID to use when sending MAVLink packets to
                the other end to force it into bootloader mode
        """
        self._lock = Lock()
        self._protocol = Protocol()
        self._system_id = system_id
        self._transport = transport

    async def __aenter__(self: C) -> C:
        await self._transport.__aenter__()
        return await super().__aenter__()

    async def __aexit__(self, *args: Any):
        result = await super().__aexit__(*args)
        await self._transport.__aexit__(*args)
        return result

    async def ensure_in_bootloader(
        self, *, on_rebooted: Callable[[], None] | None = None
    ) -> None:
        """Ensures that the board being updated is in the bootloader. If it
        does not respond to sync packets, the function assumes that the board
        is accepting MAVLink commands and attempts to send a MAVLink reboot
        packet.

        Args:
            on_rebooted: callback that will be called if we had to send a reboot
                command to the device.
        """
        async with self._lock:
            # Try to communicate with the bootloader by fetching the board ID
            # using a small number of quick retries. If it fails, we try a MAVLink
            # reboot command.
            try:
                await self._process_command_inner(
                    Command.get_device_info(DeviceInfoItem.BOARD_ID),
                    max_retries=1,
                    timeout=0.05,
                )
            except (TimeoutError, ExcessDataError):
                await self._send_mavlink_reboot_command(to_bootloader=True)
                if on_rebooted:
                    on_rebooted()
                await self._process_command_inner(
                    Command.get_device_info(DeviceInfoItem.BOARD_ID),
                    max_retries=5,
                    timeout=0.5,
                )

        self._mode = "bootloader"

    async def ensure_in_mavlink(
        self, *, on_rebooted: Callable[[], None] | None = None, timeout: float = 2.0
    ) -> None:
        """Ensures that the board being updated is running its "normal" firmware.
        If it does not send a MAVLink heartbeat packet within a given number of
        seconds, the function assumes that the board is in the bootloader and asks
        it to boot the main firmware.

        Args:
            on_rebooted: callback that will be called if we had to send a boot
                command to the device.
        """
        async with self._lock:
            try:
                with fail_after(timeout):
                    await self._wait_for_heartbeat()
            except (TimeoutError, ExcessDataError):
                await self._send_bootloader_reboot_command()
                if on_rebooted:
                    on_rebooted()

        self._mode = "mavlink"

    async def erase_flash_memory(self) -> None:
        """Erases the flash memory of the board, and resets the write pointer
        to address zero.
        """
        await self._process_command(Command.erase_flash_memory())

    async def get_board_id(self) -> int:
        """Retrieves the numeric board identifier from the bootloader."""
        return await self._get_device_info(DeviceInfoItem.BOARD_ID)

    async def get_board_revision(self) -> int:
        """Retrieves the board revision from the bootloader."""
        return await self._get_device_info(DeviceInfoItem.BOARD_REV)

    async def get_bootloader_revision(self) -> int:
        """Retrieves the protocol revision number of the bootloader."""
        return await self._get_device_info(DeviceInfoItem.BL_REV)

    async def get_chip_description(self) -> bytes:
        """Retrieves the chip description from the bootloader."""
        return await self._process_command(Command.get_chip_description())

    async def get_flash_memory_crc(self) -> int:
        """Calculates the CRC32 checksum of the flash memory."""
        crc = await self._process_command(Command.get_crc())
        return int.from_bytes(crc, byteorder="little")

    async def get_flash_memory_size(self) -> int:
        """Retrieves the size of the flash memory from the bootloader."""
        return await self._get_device_info(DeviceInfoItem.FLASH_SIZE)

    async def get_serial_number(self) -> bytes:
        """Returns the serial number of the chip from the bootloader."""
        result: list[int] = []
        for address in range(0, 12, 4):
            word = await self._process_command(
                Command.read_word_from_serial_number_area(address)
            )
            result.extend(reversed(word))
        return bytes(result)

    async def get_write_pointer(self) -> int:
        """Retrieves the current value of the flash memory write pointer from
        the bootloader.
        """
        write_ptr = await self._process_command(Command.get_write_pointer())
        return int.from_bytes(write_ptr, byteorder="little")

    async def program_bytes(self, data: bytes) -> None:
        """Writes some bytes into the flash memory of the board at the current
        write pointer, and advances the write pointer as needed.

        Note that if the function fails, we have no way to know whether the
        bootloader actually managed to process the packet and write the data
        (and we only lost the acknowledgment), or whether the packet did not
        reach the bootloader at all. This has to be taken care of by the
        caller.
        """
        # program_bytes() is a fragile operation; if we lose an acknowledgment
        # from the bootloader, we have no way to know whether the operation
        # succeeded or not, so we do not allow any retries
        await self._process_command(Command.program_bytes(data), max_retries=0)

    async def reboot(self, *, to_bootloader: bool = False) -> None:
        """Reboots the device."""
        if self._mode == "mavlink":
            await self._send_mavlink_reboot_command(to_bootloader=to_bootloader)
        elif self._mode == "bootloader":
            if to_bootloader:
                raise RuntimeError(
                    "cannot reboot to bootloader when already in bootloader"
                )
            await self._send_bootloader_reboot_command()
        else:
            raise RuntimeError("unknown mode, cannot reboot")

    async def flash_bootloader(self) -> None:
        """Sends a MAVLink command to flash the bootloader embedded in the main
        firmware image.

        The drone needs to be running the firmware (i.e. not the bootloader).
        Works on ArduPilot only.
        """
        from .mavlink import MAV_CMD_FLASH_BOOTLOADER, MAVLinkCommandLongMessage

        await self._send_mavlink_message(
            MAVLinkCommandLongMessage(
                0,  # target_system
                1,  # target_component
                MAV_CMD_FLASH_BOOTLOADER,
                0,
                0,
                0,
                0,
                290876,  # magic number
            )
        )

    async def reset_to_factory_defaults(self) -> None:
        """Sends a MAVLink command to reset the parameters of the drone to factory
        defaults.

        The drone needs to be running the firmware (i.e. not the bootloader).
        Works on ArduPilot only.
        """
        from .mavlink import MAVLinkParamSetMessage

        await self._send_mavlink_message(
            MAVLinkParamSetMessage(
                0,  # target_system
                1,  # target_component
                b"FORMAT_VERSION",
                0.0,
                0,
            )
        )

    async def _get_device_info(self, item: DeviceInfoItem) -> int:
        """Retrieves the given device info item from the bootloader.

        Parameters:
            item: the device info item to retrieve

        Returns:
            the numeric value of the device info item
        """
        response = await self._process_command(Command.get_device_info(item))
        return int.from_bytes(response, byteorder="little")

    async def _process_command(
        self,
        command: Command,
        *,
        max_retries: int = 10,
    ) -> bytes:
        async with self._lock:
            return await self._process_command_inner(command, max_retries=max_retries)

    async def _process_command_inner(
        self,
        command: Command,
        *,
        max_retries: int = 10,
        timeout: float | None = None,
    ) -> bytes:
        command_to_send: Command | None = None
        retries_left = max_retries
        sent = False
        has_timeout = timeout is not None

        while True:
            event = self._protocol.next_event()

            if event is None:
                # It's our turn, obtain sync and send the command
                if not self._protocol.in_sync:
                    command_to_send = GET_SYNC
                    sent = False
                elif not sent:
                    command_to_send = command
                else:
                    raise RuntimeError("invalid state")

                raw_bytes = self._protocol.send(command_to_send)
                try:
                    await self._transport.send(raw_bytes)
                except Exception:
                    self._protocol.send_failed()
                    if not retries_left:
                        raise
                    else:
                        retries_left -= 1
                else:
                    if command_to_send is command:
                        sent = True

            elif event is NEED_MORE_DATA:
                # Read data from the transport and feed it into the protocol
                # TODO(ntamas): what if the other side is constantly sending
                # garbage?
                if not has_timeout:
                    timeout = (
                        command_to_send.suggested_timeout if command_to_send else 1
                    )
                assert timeout is not None
                try:
                    with fail_after(timeout):
                        data = await self._transport.receive()
                except TimeoutError:
                    self._protocol.reset()
                    if not retries_left:
                        raise
                    else:
                        retries_left -= 1
                else:
                    self._protocol.feed(data)

            elif isinstance(event, Response):
                if sent:
                    # This is a response to the command
                    return event.get_payload_or_raise_exception()
                else:
                    # This is a response to the sync request; we retrieve it
                    # but we don't return it to the user
                    event.get_payload_or_raise_exception()

            elif isinstance(event, ExcessData):
                # Hmmm, we got de-synced, we might have received data that
                # belonged to a previous command
                self._protocol.reset()
                if not retries_left:
                    raise ExcessDataError()
                else:
                    retries_left -= 1

            else:
                raise RuntimeError(f"unexpected event: {event!r}")

    async def _send_bootloader_reboot_command(self) -> None:
        """Sends a reboot command to the bootloader to ask it to reboot into the
        main firmware.
        """
        await self._process_command(Command.reboot())

    async def _send_mavlink_reboot_command(
        self, *, to_bootloader: bool = False
    ) -> None:
        """Sends a MAVLink "reboot to bootloader" command over the link to ask the
        main firmware to reboot into the bootloader.
        """
        from .mavlink import (
            MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            MAVLinkCommandLongMessage,
        )

        await self._send_mavlink_message(
            MAVLinkCommandLongMessage(
                0,  # target_system
                1,  # target_component
                MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                1,  # confirmation
                3 if to_bootloader else 1,  # reboot or reboot to bootloader
            )
        )

    def _get_mavlink(self) -> MAVLink:
        from .mavlink import MAVLink

        if self._mavlink is None:
            self._mavlink = MAVLink(self._system_id, 190)  # comp ID = GCS
        return self._mavlink

    async def _send_mavlink_message(self, message: MAVLinkMessage) -> None:
        data = self._get_mavlink().encode(message, force_mavlink1=True)
        await self._transport.send(data)

    async def _wait_for_heartbeat(self) -> MAVLinkHeartbeatMessage:
        """Waits for a MAVLink heartbeat message to be received from the other end.

        This is useful to wait for the device to reboot into the firmware after
        programming, before we attempt to communicate with it using MAVLink.
        """
        mavlink = self._get_mavlink()

        while True:
            data = await self._transport.receive()
            for message in mavlink.parse_buffer(data) or ():
                if message.get_type() == "HEARTBEAT":
                    return cast("MAVLinkHeartbeatMessage", message)
