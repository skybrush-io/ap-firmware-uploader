from dataclasses import dataclass
from enum import Enum, IntEnum

__all__ = (
    "Command",
    "LocalProtocolError",
    "Protocol",
    "ProtocolError",
    "RemoteProtocolError",
    "Response",
)


class ProtocolError(RuntimeError):
    """Superclass for protocol-related errors."""

    pass


class LocalProtocolError(ProtocolError):
    """Protocol-related error that indicates a failure on the local (uploader) side."""

    pass


class RemoteProtocolError(ProtocolError):
    """Protocol-related error that indicates a failure on the remote (bootloader) side."""

    pass


class OpCode(IntEnum):
    """List of OpCode in the bootloader protocol."""

    # Protocol bytes
    IN_SYNC = 0x12
    EOC = 0x20

    # Reply bytes
    OK = 0x10
    FAILED = 0x11
    INVALID = 0x13  # rev3+
    BAD_SILICON_REV = 0x14  # rev5+

    # Command bytes
    NOP = 0x00
    GET_SYNC = 0x21
    GET_DEVICE = 0x22
    CHIP_ERASE = 0x23
    CHIP_VERIFY = 0x24  # rev2 only
    PROG_MULTI = 0x27
    READ_MULTI = 0x28  # rev2 only
    GET_CRC = 0x29  # rev3+
    GET_OTP = 0x2A  # rev4+
    GET_SN = 0x2B  # rev4+
    GET_CHIP = 0x2C  # rev5+
    SET_BOOT_DELAY = 0x2D  # rev5+
    GET_CHIP_DES = 0x2E  # rev5+
    GET_WRITE_PTR = 0x2F  # rev6+
    REBOOT = 0x30
    DEBUG = 0x31
    SET_BAUD = 0x33


PROG_MULTI_MAX_PAYLOAD_LENGTH = 252
"""Maximum allowed payload length of a single PROG_MULTI command."""


class DeviceInfoItem(IntEnum):
    """List of possible payload values for a GET_DEVICE opcode."""

    BL_REV = 0x01
    """Bootloader protocol revision"""

    BOARD_ID = 0x02
    """Board identifier"""

    BOARD_REV = 0x03
    """Board revision"""

    FLASH_SIZE = 0x04
    """Maximum firmware size in bytes"""


class ResponseType(IntEnum):
    """Enum representing the possible response types that the bootloader
    can send to us.
    """

    OK = OpCode.OK
    FAILED = OpCode.FAILED
    INVALID = OpCode.INVALID
    BAD_SILICON_REV = OpCode.BAD_SILICON_REV


class Event:
    """Base class for event objects emitted by the protocol."""

    __slots__ = ()


@dataclass(frozen=True)
class NeedMoreData(Event):
    """Event emitted by the protocol when it expects more data from the
    bootloader.
    """

    __slots__ = ()


@dataclass(frozen=True)
class ExcessData(Event):
    """Event emitted by the protocol when it is receiving excess bytes from the
    bootloader that it did not expect.
    """

    __slots__ = ("payload",)

    payload: bytes
    """The extra bytes that were received."""


@dataclass(frozen=True)
class ConnectionClosed(Event):
    """Event emitted by the protocol when the connection to the remote side
    is closed.
    """

    __slots__ = ()


@dataclass(init=False, frozen=True)
class Command(Event):
    """Event representing a command sent by the uploader to the bootloader."""

    __slots__ = ("type", "args")

    type: OpCode
    """Type (opcode) of the command to send."""

    args: bytes
    """Encoded arguments to send with the command."""

    def __init__(self, type: OpCode, args: bytes = b""):
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "args", args)

    @classmethod
    def erase_flash_memory(cls):
        """Constructs a command that erases the entire flash memory of the
        chip and resets the write pointer to the start of the flash memory.
        """
        return cls(OpCode.CHIP_ERASE)

    @classmethod
    def get_chip_description(cls):
        """Constructs a command that retrieves the chip description from the
        bootloader.
        """
        return cls(OpCode.GET_CHIP_DES)

    @classmethod
    def get_chip_type(cls):
        """Constructs a command that retrieves the chip type from the
        bootloader.
        """
        return cls(OpCode.GET_CHIP)

    @classmethod
    def get_crc(cls):
        """Constructs a command that retrieves the CRC32 checksum of the flash
        memory from the bootloader.
        """
        return cls(OpCode.GET_CRC)

    @classmethod
    def get_device_info(cls, item: DeviceInfoItem):
        """Constructs a command that retrieves basic device information from the
        bootloader.

        Parameters:
            item: the device info item to retrieve
        """
        return cls(
            OpCode.GET_DEVICE,
            args=bytes([int(item)]),
        )

    @classmethod
    def get_sync(cls):
        return cls(OpCode.GET_SYNC)

    @classmethod
    def get_write_pointer(cls):
        return cls(OpCode.GET_WRITE_PTR)

    @classmethod
    def program_bytes(cls, data: bytes):
        """Constructs a command to program a given number of bytes at the
        current write pointer.
        """
        if len(data) > PROG_MULTI_MAX_PAYLOAD_LENGTH:
            raise RuntimeError(
                "payload too large, maximum allowed size is "
                f"{PROG_MULTI_MAX_PAYLOAD_LENGTH} bytes"
            )
        return cls(OpCode.PROG_MULTI, args=data)

    @classmethod
    def read_word_from_otp_area(cls, address: int):
        """Constructs a command to read a word from the OTP memory area at the
        given address.
        """
        return cls(
            OpCode.GET_OTP,
            args=address.to_bytes(length=4, byteorder="little", signed=False),
        )

    @classmethod
    def read_word_from_serial_number_area(cls, address: int):
        """Constructs a command to read a word from the serial number memory
        area at the given address.
        """
        return cls(
            OpCode.GET_SN,
            args=address.to_bytes(length=4, byteorder="little", signed=False),
        )

    @classmethod
    def reboot(cls):
        """Constructs a command to reboot the device."""
        return cls(OpCode.REBOOT)

    @classmethod
    def set_boot_delay(cls, delay: int):
        """Sets the boot delay of the bootloader, in seconds."""
        if delay < 0 or delay > 255:
            raise RuntimeError("invalid boot delay, must be between 0 and 255")
        return cls(OpCode.SET_BOOT_DELAY, args=bytes([delay]))

    @classmethod
    def set_baud_rate(cls, baud_rate: int):
        """Sets the baud rate of the bootloader."""
        return cls(
            OpCode.SET_BAUD,
            args=baud_rate.to_bytes(length=4, byteorder="little", signed=False),
        )

    @property
    def payload_length(self) -> int:
        """Returns the expected length of the payload of this command, without
        the EOC marker. Zero means that the command consists of an opcode and
        the EOC marker only. Negative numbers mean that the request is
        variable-length and the first byte of the request encodes the true
        length (without the EOC marker).
        """
        code = self.type
        if code in (OpCode.GET_OTP, OpCode.GET_SN, OpCode.SET_BAUD):
            return 4
        elif code in (OpCode.GET_DEVICE, OpCode.SET_BOOT_DELAY):
            return 1
        elif code == OpCode.PROG_MULTI:
            return -1
        else:
            return 0

    @property
    def response_length(self) -> int:
        """Returns the expected length of a response to this command, without
        the sync marker. Zero means that the response is simply a sync marker.
        Negative numbers mean that the response is variable-length and the
        first four bytes of the response encode the true length (without the
        sync marker).
        """
        code = self.type
        if code == OpCode.GET_CHIP_DES:
            return -1
        elif code in (
            OpCode.GET_CRC,
            OpCode.GET_OTP,
            OpCode.GET_SN,
            OpCode.GET_DEVICE,
            OpCode.GET_CRC,
            OpCode.GET_CHIP,
            OpCode.GET_WRITE_PTR,
        ):
            return 4
        else:
            return 0

    @property
    def suggested_timeout(self) -> float:
        """Returns a suggested timeout value for this command, in seconds. The
        bootloader is expected to provide a response in the given number of
        seconds at most.
        """
        if self.type == OpCode.CHIP_ERASE:
            # chip erase takes a long time
            return 20
        elif self.type == OpCode.GET_CRC:
            # CRC calculation also takes a long time
            return 3
        else:
            # px_uploader.py used two seconds, let's make it shorter and see if it
            # causes problems
            return 0.5

    def to_bytes(self) -> bytes:
        """Converts the command into a raw byte-level representation."""
        if self.payload_length < 0:
            if len(self.args) > 255:
                raise RuntimeError("arguments too long")
            return bytes(
                [
                    int(self.type),
                    len(self.args),
                    *self.args,
                    int(OpCode.EOC),
                ]
            )
        else:
            return bytes([int(self.type), *self.args, int(OpCode.EOC)])

    __hash__ = None


@dataclass(init=False, frozen=True)
class Response(Event):
    """Event representing a response sent by the bootloader to the uploader."""

    __slots__ = ("type", "payload")

    type: ResponseType
    """Type of the response."""

    payload: bytes
    """Payload of the response"""

    @classmethod
    def success(cls, payload: bytes = b""):
        return cls(ResponseType.OK, payload)

    @classmethod
    def failure(cls):
        return cls(ResponseType.FAILED)

    @classmethod
    def invalid(cls):
        return cls(ResponseType.INVALID)

    @classmethod
    def from_opcode(cls, opcode: int, payload: bytes = b""):
        return cls(ResponseType(opcode), payload)

    def __init__(self, type: ResponseType, payload: bytes = b""):
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "payload", payload)

    def get_payload_or_raise_exception(self) -> bytes:
        """Returns the payload of the response or raises an exception if the
        response indicates failure.
        """
        if self.type is ResponseType.BAD_SILICON_REV:
            raise RuntimeError("bad silicon revision")
        elif self.type is ResponseType.INVALID:
            raise RuntimeError("invalid operation")
        elif self.type is ResponseType.FAILED:
            raise RuntimeError("operation failed")
        else:
            return self.payload

    @property
    def successful(self) -> bool:
        """Returns whether this response code indicates a successful operation."""
        return self.type is ResponseType.OK

    def to_bytes(self) -> bytes:
        """Converts the response into a raw byte-level representation."""
        if self.type is ResponseType.OK:
            return bytes([*self.payload, int(OpCode.IN_SYNC), int(OpCode.OK)])
        else:
            return bytes([int(OpCode.IN_SYNC), int(self.type)])

    __hash__ = None


GET_SYNC = Command.get_sync()
"""Singleton instance of the GET_SYNC command."""

NEED_MORE_DATA = NeedMoreData()
"""Singleton instance of the NeedMoreData_ event."""


class ProtocolState(Enum):
    """Enum representing the possible internal states of the protocol"""

    IDLE = "idle"
    """Starting state"""

    IN_SYNC = "inSync"
    """We are in sync with the bootloader and the bootloader is ready to receive
    a command.
    """

    WAIT_RESPONSE_LENGTH = "waitResponseLength"
    """We are waiting for a byte indicating the length of the response from the
    bootloader."""

    WAIT_RESPONSE = "waitResponse"
    """We are waiting for a response from the bootloader."""

    WAIT_SYNC = "waitSync"
    """We are waiting for a sync marker from the bootloader."""

    ERROR = "error"
    """We have encountered an error and need to re-sync to make sure that we
    are still communicating with the bootloader.
    """


class Protocol:
    """State machine that tracks the state of the uploader protocol without
    actually doing any I/O.
    """

    _buffer: bytes = b""
    """Buffer in which the incoming bytes are stored until they are processed."""

    _buffer_closed: bool = False
    """Stores whether the receive buffer is closed."""

    _payload: bytes = b""
    """The last response payload received from the bootloader."""

    _state: ProtocolState = ProtocolState.IDLE
    """Current state of the protocol"""

    _bytes_expected: int = 0
    """Number of bytes that we expect from the bootloader."""

    def __init__(self):
        """Constructor."""
        self.reset()

    def feed(self, data: bytes) -> None:
        """Feeds incoming bytes from the transport to the internal buffer of
        the state machine, without processing it.

        Parameters:
            data: the bytes that were received. Feeding an empty bytes object
                into this function assumes that the connection was closed.
        """
        if data:
            if self._buffer_closed:
                raise RuntimeError("receiver closed")
            self._buffer += data
        else:
            self._buffer_closed = True

    @property
    def in_sync(self) -> bool:
        """Returns whether we are in sync with the bootloader and can send the
        next command.
        """
        return self._state is ProtocolState.IN_SYNC

    def next_event(self) -> Event | None:
        """Parses the front of the buffer of received bytes, updates the
        internal state and returns an appropriate event.

        Returns:
            an event or ``None`` if the uploader needs to send some bytes before
            receiving an event
        """
        while True:
            if not self._buffer and self._buffer_closed:
                return ConnectionClosed()

            if self._state is ProtocolState.WAIT_RESPONSE_LENGTH:
                assert self._bytes_expected > 0

                if len(self._buffer) < self._bytes_expected:
                    return NEED_MORE_DATA

                # Got the full response, now start waiting for sync
                length = int.from_bytes(self._buffer[0:4], byteorder="little")
                self._buffer = self._buffer[self._bytes_expected :]
                self._start_waiting_for_response(length)

            if self._state is ProtocolState.WAIT_RESPONSE:
                assert self._bytes_expected > 0

                if len(self._buffer) < self._bytes_expected:
                    return NEED_MORE_DATA

                # Got the full response, now start waiting for sync
                self._payload = self._buffer[: self._bytes_expected]
                self._buffer = self._buffer[self._bytes_expected :]
                self._start_waiting_for_sync()

            if self._state is ProtocolState.WAIT_SYNC:
                assert self._bytes_expected > 0

                if not self._buffer:
                    return NEED_MORE_DATA

                # Do we have a sync byte anywhere in the buffer?
                sync_index = self._buffer.find(OpCode.IN_SYNC)
                if sync_index > 0:
                    # Report the current payload and the buffer up to the sync
                    # byte as excess data if the sync byte is not the first
                    return self._purge_excess_data(up_to=sync_index)
                elif sync_index < 0:
                    # No sync byte, report the payload and the entire unparsed
                    # buffer as excess
                    return self._purge_excess_data() or NEED_MORE_DATA

                # If we already have it, check the response byte
                if len(self._buffer) < 2:
                    return NEED_MORE_DATA

                response = self._buffer[1]
                if response not in (OpCode.OK, OpCode.FAILED, OpCode.INVALID):
                    return self._purge_excess_data(up_to=2)

                # Consume the response and update state
                self._buffer = self._buffer[2:]
                payload = self._payload
                self._move_to_in_sync_state()
                return Response.from_opcode(response, payload)

            else:
                return self._purge_excess_data()

    def reset(self):
        """Resets the internal state of the protocol."""
        self._buffer = b""
        self._buffer_closed = False
        self._state = ProtocolState.IDLE

    def send(self, event: Event) -> bytes:
        """Converts the given event into a byte-level representation that we
        can then send to the bootloader in the transport layer.
        """
        if isinstance(event, Command):
            # If we are not in sync with the remote side, the only command that
            # we can send is Command.GET_SYNC
            if not self.in_sync and event.type is not OpCode.GET_SYNC:
                raise LocalProtocolError(
                    "need to send GET_SYNC before sending more commands"
                )

            # Encode the command
            result = event.to_bytes()

            # Move to the "waiting for response" or "waiting for sync" state
            self._start_waiting_for_response(event.response_length)
        else:
            raise LocalProtocolError(f"event cannot be sent: {event!r}")

        return result

    def send_failed(self) -> None:
        """Notifies the state machine that an attempt to send the bytes it
        returned from ``send()`` has failed.
        """
        # Move to the error state; we will attempt to re-gain sync later
        self._state = ProtocolState.ERROR

    def _move_to_error_state(self) -> None:
        self._state = ProtocolState.ERROR
        self._payload = b""
        self._bytes_expected = 0

    def _move_to_in_sync_state(self) -> None:
        self._state = ProtocolState.IN_SYNC
        self._payload = b""
        self._bytes_expected = 0

    def _purge_excess_data(self, up_to: int | None = None) -> ExcessData | None:
        if up_to is None:
            up_to = len(self._buffer)
        else:
            up_to = min(up_to, len(self._buffer))
        if self._payload or up_to > 0:
            event = ExcessData(self._payload + self._buffer[:up_to])
            self._payload = b""
            self._buffer = self._buffer[up_to:]
        else:
            event = None
        return event

    def _start_waiting_for_response(self, length: int) -> None:
        if length < 0:
            self._state = ProtocolState.WAIT_RESPONSE_LENGTH
            self._payload = b""
            self._bytes_expected = 4
        elif length == 0:
            self._start_waiting_for_sync()
        else:
            self._state = ProtocolState.WAIT_RESPONSE
            self._payload = b""
            self._bytes_expected = length

    def _start_waiting_for_sync(self) -> None:
        self._state = ProtocolState.WAIT_SYNC
        self._bytes_expected = 2
