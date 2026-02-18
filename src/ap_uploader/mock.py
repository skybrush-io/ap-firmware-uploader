__all__ = ("MockBootloader",)

from random import random

from anyio import TASK_STATUS_IGNORED, fail_after, sleep

from .io.base import Transport
from .io.udp import UDPListenerTransport
from .protocol import Command, DeviceInfoItem, OpCode, Protocol, Response
from .utils import crc32


class InvalidCommandError(RuntimeError):
    pass


class SimulatedPacketDelayError(RuntimeError):
    pass


class SimulatedPacketLossError(RuntimeError):
    pass


class MockBootloader:
    """Mock bootloader that implements the same protocol as a "real"
    ArduPilot / PX4 bootloader, for testing purposes.
    """

    _buffer: bytes = b""
    """Internal buffer holding yet-unprocessed bytes."""

    _drop_next_response: bool = False
    """Stores whether to drop the next response packet forcefully, for testing
    purposes.
    """

    _flash_memory: bytearray
    """Byte array containing the simulated flash memory."""

    _flash_memory_write_ptr: int = 0
    """Index that points to the byte in the flash memory where the next write
    attempt will write.
    """

    _protocol: Protocol
    """A protocol instance used to convert commands to raw bytes."""

    _serial_number: bytes = b"\xc0\xff\xee\x00\xde\xad\xbe\xef\x00\xc0\xff\xee"
    """The simulated serial number of the device."""

    _should_exit: bool = False
    """Stores whether the bootloader has received a reboot command."""

    _transport: Transport
    """The transport object that the mock bootloader uses."""

    simulate_desync: bool = False
    """Stores whether to simulate a de-synchronization between the bootloader
    and the uploader by intentionally "losing" or "delaying" confirmation
    packets from the bootloader to the uploader script after a PROG_MULTI command.
    """

    def __init__(
        self, transport: Transport, *, flash_memory_size: int = 2080768
    ) -> None:
        """Constructor."""
        self._transport = transport
        self._flash_memory = bytearray(b"\xff") * flash_memory_size

    @property
    def flash_memory(self) -> bytearray:
        """Returns the internal simulated flash memory."""
        return self._flash_memory

    def drop_next_response(self) -> None:
        """Forces the mock bootloader to drop the next response packet."""
        self._drop_next_response = True

    def set_write_pointer(self, value: int) -> None:
        """Sets the value of the write pointer of the flash memory."""
        self._flash_memory_write_ptr = value

    async def run(self, *, task_status=TASK_STATUS_IGNORED):
        """Handles incoming commands from the transport and sends appropriate
        responses.
        """
        async with self._transport:
            task_status.started()
            while not self._should_exit:
                try:
                    command = await self._receive_next_command()
                except (InvalidCommandError, TimeoutError):
                    response = Response.invalid()
                else:
                    try:
                        response = Response.success(self._handle_command(command))
                    except InvalidCommandError:
                        response = Response.invalid()
                    except SimulatedPacketLossError:
                        response = None
                    except SimulatedPacketDelayError:
                        await sleep(0.6)  # should be larger than the timeout
                        response = Response.success(b"")
                    except Exception:  # pragma: no cover
                        response = Response.failure()
                if self._drop_next_response:
                    response = None
                    self._drop_next_response = False
                if response is not None:
                    await self._transport.send(response.to_bytes())

    def _handle_command(self, command: Command) -> bytes:
        if command.type == OpCode.GET_SYNC:
            return b""
        elif command.type == OpCode.GET_DEVICE:
            item = DeviceInfoItem(command.args[0])
            if item is DeviceInfoItem.BL_REV:
                value = 6 if self.simulate_desync else 5
            elif item is DeviceInfoItem.BOARD_ID:
                value = 9
            elif item is DeviceInfoItem.BOARD_REV:
                value = 0
            elif item is DeviceInfoItem.FLASH_SIZE:
                value = len(self._flash_memory)
            else:
                value = 0  # pragma: no cover
            return value.to_bytes(4, byteorder="little", signed=False)
        elif command.type == OpCode.GET_CHIP:
            return b"\x09"
        elif command.type == OpCode.GET_CHIP_DES:
            chip_description = b"STM32F7,mock"
            return (
                len(chip_description).to_bytes(4, byteorder="little", signed=False)
                + chip_description
            )
        elif command.type == OpCode.GET_OTP:
            return b"\xff\xff\xff\xff"
        elif command.type == OpCode.GET_SN:
            if len(command.args) != 4:
                raise InvalidCommandError  # pragma: no cover
            address = int.from_bytes(command.args, byteorder="little", signed=False)
            result = self._serial_number[address : (address + 4)].ljust(4, b"\x00")
            return result[::-1]
        elif command.type == OpCode.CHIP_ERASE:
            self._flash_memory = bytearray(b"\xff") * len(self._flash_memory)
            self._flash_memory_write_ptr = 0
            return b""
        elif command.type == OpCode.PROG_MULTI:
            self._write_to_flash_memory(command.args)
            return b""
        elif command.type == OpCode.GET_CRC:
            return crc32(self._flash_memory).to_bytes(
                4, byteorder="little", signed=False
            )
        elif command.type == OpCode.GET_WRITE_PTR:
            return self._flash_memory_write_ptr.to_bytes(
                4, byteorder="little", signed=False
            )
        elif command.type == OpCode.REBOOT:
            self._should_exit = True
            return b""
        else:
            raise InvalidCommandError

    async def _read_byte(self) -> int:
        """Reads a single byte from the transport."""
        data = await self._read_exactly(1)
        return data[0]

    async def _read_exactly(self, length: int) -> bytes:
        """Reads exactly the given number of bytes from the transport."""
        while len(self._buffer) < length:
            bytes_read = await self._transport.receive()
            self._buffer += bytes_read
        result = self._buffer[:length]
        self._buffer = self._buffer[length:]
        return result

    async def _receive_next_command(self) -> Command:
        """Receives the next command from the transport.

        Returns:
            the command that was received

        Raises:
            TimeoutError: if the remaining bytes of a command did not arrive
                in time after a valid command byte
        """
        while True:
            command_byte = await self._read_byte()
            try:
                opcode = OpCode(command_byte)
            except ValueError:
                continue

            if opcode <= OpCode.EOC:
                # These are not command codes
                continue

            partial_command = Command(opcode)
            if partial_command.payload_length < 0:
                with fail_after(0.1):
                    payload_length = await self._read_byte()
            else:
                payload_length = partial_command.payload_length

            if payload_length > 0:
                with fail_after(0.1):
                    payload = await self._read_exactly(payload_length)
            else:
                payload = b""

            await self._read_eoc()

            return Command(opcode, payload)

    async def _read_eoc(self, *, timeout: float = 0.002) -> None:
        """Waits for an end-of-command byte from the transport.

        Parameters:
            timeout: maximum number of seconds to wait for the end-of-command
                byte.
        """
        with fail_after(timeout):
            next_byte = await self._read_byte()
            if next_byte != OpCode.EOC:
                raise InvalidCommandError()

    def _write_to_flash_memory(self, data: bytes) -> None:
        """Writes to the simulated flash memory at the current write pointer and
        advances the write pointer.
        """
        if not data:
            return

        start = self._flash_memory_write_ptr
        end = start + len(data)
        excess = end - len(self._flash_memory)
        if excess <= 0:
            self._flash_memory[start:end] = data
            self._flash_memory_write_ptr = end

            # Simulate the potential packet loss that can cause the bootloader
            # and the uploader to de-sync
            if self.simulate_desync and self._flash_memory_write_ptr >= 4096:
                p = random()
                if p < 0.01:
                    raise SimulatedPacketLossError()
                elif p < 0.02:
                    raise SimulatedPacketDelayError()

        else:
            self._write_to_flash_memory(data[:-excess])
            self._flash_memory_write_ptr = 0
            self._write_to_flash_memory(data[-excess:])


async def run_mock_bootloader(port: str, transport: Transport, options) -> None:
    from rich import print

    print(
        f"[bold green]:heavy_check_mark:[/bold green] Starting mock bootloader on [b]{port}[/b]..."
    )
    try:
        while True:
            bl = MockBootloader(transport)
            bl.simulate_desync = options.desync
            await bl.run()
            print(f":sparkles: [b]{port}[/b] Bootloader was requested to reboot.")
    finally:
        print(f":door: [b]{port}[/b] Bootloader exited.")


async def run_mock_bootloaders(options) -> None:
    from anyio import create_task_group

    from .io.serial import SerialPortTransport

    async with create_task_group() as tg:
        for port in options.port:
            try:
                port_number = int(port)
                transport = UDPListenerTransport("127.0.0.1", port_number)
                port = f"127.0.0.1:{port}"
            except ValueError:
                transport = SerialPortTransport.from_url(
                    f"spy://{port}" if options.debug else port
                )
            tg.start_soon(run_mock_bootloader, port, transport, options)


def mock_main():  # pragma: no cover
    """Entry point for a CLI application that connects a mock bootloader to
    a serial port.
    """
    from argparse import ArgumentParser

    from anyio import run

    parser = ArgumentParser()
    parser.add_argument(
        "port",
        help="serial port or UDP port number to bind the mock bootloader to",
        nargs="*",
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", default=False, help="show debug output"
    )
    parser.add_argument(
        "--desync",
        action="store_true",
        default=False,
        help="simulate bootloader de-synchronization due to packet loss and delays",
    )
    options = parser.parse_args()

    try:
        run(run_mock_bootloaders, options)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    mock_main()
