"""Auto-generated MAVLink protocol implementation for the subset of packets that
we need in the uploader.

The auto-generated implementation was modified by hand to make it fit more into
the Python syntax.
"""

import hashlib
import json
import struct

from typing import Callable, ClassVar, Dict, List, Optional, Tuple, Type, Union, cast

WIRE_PROTOCOL_VERSION = "2.0"
DIALECT = "mavlink"

PROTOCOL_MARKER_V1 = 0xFE
PROTOCOL_MARKER_V2 = 0xFD
HEADER_LEN_V1 = 6
HEADER_LEN_V2 = 10

MAVLINK_SIGNATURE_BLOCK_LEN = 13

MAVLINK_IFLAG_SIGNED = 0x01

# some base types from mavlink_types.h
MAVLINK_TYPE_CHAR = 0
MAVLINK_TYPE_UINT8_T = 1
MAVLINK_TYPE_INT8_T = 2
MAVLINK_TYPE_UINT16_T = 3
MAVLINK_TYPE_INT16_T = 4
MAVLINK_TYPE_UINT32_T = 5
MAVLINK_TYPE_INT32_T = 6
MAVLINK_TYPE_UINT64_T = 7
MAVLINK_TYPE_INT64_T = 8
MAVLINK_TYPE_FLOAT = 9
MAVLINK_TYPE_DOUBLE = 10


class x25crc:
    """CRC-16/MCRF4XX - based on checksum.h from mavlink library"""

    def __init__(self, buf: Union[bytes, bytearray, None] = None) -> None:
        self.crc = 0xFFFF
        if buf is not None:
            self.accumulate(buf)

    def accumulate(self, buf: Union[bytes, bytearray]) -> None:
        """add in some more bytes"""
        accum = self.crc
        for b in buf:
            tmp = b ^ (accum & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            accum = (accum >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        self.crc = accum


# swiped from DFReader.py
def to_string(s: Union[bytes, bytearray]) -> str:
    """desperate attempt to convert a string regardless of what garbage we get"""
    try:
        return s.decode("utf-8")
    except Exception:
        # so it's a nasty one. Let's grab as many characters as we can
        return s.decode("ascii", "ignore") + "_XXX"


class MAVLinkHeader:
    """MAVLink message header"""

    def __init__(
        self,
        msg_id: int,
        incompat_flags: int = 0,
        compat_flags: int = 0,
        mlen: int = 0,
        seq: int = 0,
        source_system: int = 0,
        source_component: int = 0,
    ) -> None:
        self.mlen = mlen
        self.seq = seq
        self.source_system = source_system
        self.source_component = source_component
        self.msg_id = msg_id
        self.incompat_flags = incompat_flags
        self.compat_flags = compat_flags

    def pack(self, force_mavlink1: bool = False) -> bytes:
        if WIRE_PROTOCOL_VERSION == "2.0" and not force_mavlink1:
            return struct.pack(
                "<BBBBBBBHB",
                253,
                self.mlen,
                self.incompat_flags,
                self.compat_flags,
                self.seq,
                self.source_system,
                self.source_component,
                self.msg_id & 0xFFFF,
                self.msg_id >> 16,
            )
        return struct.pack(
            "<BBBBBB",
            PROTOCOL_MARKER_V1,
            self.mlen,
            self.seq,
            self.source_system,
            self.source_component,
            self.msg_id,
        )


class MAVLinkMessage:
    """base MAVLink message class"""

    id = 0
    msgname = ""
    field_names: ClassVar[Tuple[str, ...]] = ()
    ordered_field_names: ClassVar[Tuple[str, ...]] = ()
    fieldtypes: ClassVar[Tuple[str, ...]] = ()
    fielddisplays_by_name: Dict[str, str] = {}
    fieldenums_by_name: Dict[str, str] = {}
    fieldunits_by_name: Dict[str, str] = {}
    format = ""
    native_format = bytearray("", "ascii")
    orders: List[int] = []
    lengths: List[int] = []
    array_lengths: List[int] = []
    crc_extra = 0
    unpacker = struct.Struct("")

    def __init__(self, msg_id: int, name: str) -> None:
        self._header = MAVLinkHeader(msg_id)
        self._payload: Union[bytes, bytearray, None] = None
        self._msgbuf: Union[bytes, bytearray, None] = None
        self._crc: Optional[int] = None
        self._type = name
        self._signed = False
        self._link_id: Optional[int] = None
        self._instances: Optional[Dict[str, str]] = None

    def format_attr(self, field: str) -> Union[str, float, int]:
        """override field getter"""
        raw_attr: Union[bytes, str, float, int] = cast(
            Union[bytes, float, int],
            getattr(self, field),
        )
        if isinstance(raw_attr, bytes):
            raw_attr = to_string(raw_attr).rstrip("\00")
        return raw_attr

    def get_msgbuf(self) -> bytearray:
        if self._msgbuf is None:
            raise TypeError("_msgbuf is not initialized")
        if isinstance(self._msgbuf, bytearray):
            return self._msgbuf
        return bytearray(self._msgbuf)

    def get_header(self) -> MAVLinkHeader:
        return self._header

    def get_payload(self) -> Union[bytes, bytearray, None]:
        return self._payload

    def get_crc(self) -> Optional[int]:
        return self._crc

    def get_type(self) -> str:
        return self._type

    def get_msg_id(self) -> int:
        return self._header.msg_id

    def get_source_system(self) -> int:
        return self._header.source_system

    def get_source_component(self) -> int:
        return self._header.source_component

    def get_seq(self) -> int:
        return self._header.seq

    def get_signed(self) -> bool:
        return self._signed

    def get_link_id(self) -> Optional[int]:
        return self._link_id

    def __str__(self) -> str:
        key_values = [(a, self.format_attr(a)) for a in self.field_names]
        str_key_values = ", ".join([f"{a}: {v}" for (a, v) in key_values])
        return self._type + f"{self._type} {{{str_key_values}}}"

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __eq__(self, other: object) -> bool:
        if other is None:
            return False

        if not isinstance(other, MAVLinkMessage):
            return False

        if self.get_type() != other.get_type():
            return False

        # We do not compare CRC because native code doesn't provide it
        # if self.get_crc() != other.get_crc():
        #    return False

        if self.get_seq() != other.get_seq():
            return False

        if self.get_source_system() != other.get_source_system():
            return False

        if self.get_source_component() != other.get_source_component():
            return False

        for a in self.field_names:
            if self.format_attr(a) != other.format_attr(a):
                return False

        return True

    def to_dict(self) -> Dict[str, Union[str, float, int]]:
        d: Dict[str, Union[str, float, int]] = {}
        d["mavpackettype"] = self._type
        for a in self.field_names:
            d[a] = self.format_attr(a)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def sign_packet(self, mav: "MAVLink") -> None:
        if self._msgbuf is None or mav.signing.secret_key is None:
            raise TypeError("_msgbuf is not initialized")

        h = hashlib.new("sha256")
        self._msgbuf += struct.pack("<BQ", mav.signing.link_id, mav.signing.timestamp)[
            :7
        ]
        h.update(mav.signing.secret_key)
        h.update(self._msgbuf)
        sig = h.digest()[:6]
        self._msgbuf += sig
        mav.signing.timestamp += 1

    def pack_msg(
        self,
        mav: "MAVLink",
        crc_extra: int,
        payload: bytes,
        force_mavlink1: bool = False,
    ) -> bytes:
        plen = len(payload)
        if WIRE_PROTOCOL_VERSION != "1.0" and not force_mavlink1:
            # in MAVLink2 we can strip trailing zeros off payloads. This allows for simple
            # variable length arrays and smaller packets
            while plen > 1 and payload[plen - 1] == 0:
                plen -= 1
        self._payload = payload[:plen]
        incompat_flags = 0
        if mav.signing.sign_outgoing:
            incompat_flags |= MAVLINK_IFLAG_SIGNED
        self._header = MAVLinkHeader(
            self._header.msg_id,
            incompat_flags=incompat_flags,
            compat_flags=0,
            mlen=len(self._payload),
            seq=mav.seq,
            source_system=mav.source_system,
            source_component=mav.source_component,
        )
        self._msgbuf = self._header.pack(force_mavlink1=force_mavlink1) + self._payload
        crc = x25crc(self._msgbuf[1:])
        if True:  # using CRC extra
            crc.accumulate(struct.pack("B", crc_extra))
        self._crc = crc.crc
        self._msgbuf += struct.pack("<H", self._crc)
        if mav.signing.sign_outgoing and not force_mavlink1:
            self.sign_packet(mav)
        return self._msgbuf

    def pack(self, mav: "MAVLink", force_mavlink1: bool = False) -> bytes:
        raise NotImplementedError("MAVLinkMessage cannot be serialized directly")

    def __getitem__(self, key: str) -> str:
        """support indexing, allowing for multi-instance sensors in one message"""
        if self._instances is None:
            raise IndexError()
        if key not in self._instances:
            raise IndexError()
        return self._instances[key]


# enums
MAV_COMP_ID_ALL = 0
MAV_COMP_ID_AUTOPILOT1 = 1
MAV_COMPONENT_ENUM_END = 2
MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN = 246
MAV_CMD_ENUM_END = 247


# message IDs
MAVLINK_MSG_ID_BAD_DATA = -1
MAVLINK_MSG_ID_UNKNOWN = -2
MAVLINK_MSG_ID_HEARTBEAT = 0
MAVLINK_MSG_ID_COMMAND_LONG = 76
MAVLINK_MSG_ID_COMMAND_ACK = 77


class MAVLinkHeartbeatMessage(MAVLinkMessage):
    id = MAVLINK_MSG_ID_HEARTBEAT
    msgname = "HEARTBEAT"
    field_names: ClassVar[Tuple[str, ...]] = (
        "type",
        "autopilot",
        "base_mode",
        "custom_mode",
        "system_status",
        "mavlink_version",
    )
    ordered_field_names: ClassVar[Tuple[str, ...]] = (
        "custom_mode",
        "type",
        "autopilot",
        "base_mode",
        "system_status",
        "mavlink_version",
    )
    fieldtypes: ClassVar[Tuple[str, ...]] = (
        "uint8_t",
        "uint8_t",
        "uint8_t",
        "uint32_t",
        "uint8_t",
        "uint8_t",
    )
    fielddisplays_by_name: Dict[str, str] = {
        "base_mode": "bitmask",
    }
    fieldenums_by_name: Dict[str, str] = {}
    fieldunits_by_name: Dict[str, str] = {}
    format = "<IBBBBB"
    native_format = bytearray("<IBBBBB", "ascii")
    orders = [1, 2, 3, 0, 4, 5]
    lengths = [1, 1, 1, 1, 1, 1]
    array_lengths = [0, 0, 0, 0, 0, 0]
    crc_extra = 50
    unpacker = struct.Struct("<IBBBBB")

    def __init__(
        self,
        type: int,
        autopilot: int,
        base_mode: int,
        custom_mode: int,
        system_status: int,
        mavlink_version: int,
    ):
        super().__init__(MAVLINK_MSG_ID_HEARTBEAT, "HEARTBEAT")
        self.type = type
        self.autopilot = autopilot
        self.base_mode = base_mode
        self.custom_mode = custom_mode
        self.system_status = system_status
        self.mavlink_version = mavlink_version

    def pack(self, mav: "MAVLink", force_mavlink1: bool = False) -> bytes:
        return self.pack_msg(
            mav,
            50,
            struct.pack(
                "<IBBBBB",
                self.custom_mode,
                self.type,
                self.autopilot,
                self.base_mode,
                self.system_status,
                self.mavlink_version,
            ),
            force_mavlink1=force_mavlink1,
        )


class MAVLinkCommandLongMessage(MAVLinkMessage):
    id = MAVLINK_MSG_ID_COMMAND_LONG
    msgname = "COMMAND_LONG"
    field_names: ClassVar[Tuple[str, ...]] = (
        "target_system",
        "target_component",
        "command",
        "confirmation",
        "param1",
        "param2",
        "param3",
        "param4",
        "param5",
        "param6",
        "param7",
    )
    ordered_field_names: ClassVar[Tuple[str, ...]] = (
        "param1",
        "param2",
        "param3",
        "param4",
        "param5",
        "param6",
        "param7",
        "command",
        "target_system",
        "target_component",
        "confirmation",
    )
    fieldtypes: ClassVar[Tuple[str, ...]] = (
        "uint8_t",
        "uint8_t",
        "uint16_t",
        "uint8_t",
        "float",
        "float",
        "float",
        "float",
        "float",
        "float",
        "float",
    )
    fielddisplays_by_name: Dict[str, str] = {}
    fieldenums_by_name: Dict[str, str] = {
        "command": "MAV_CMD",
    }
    fieldunits_by_name: Dict[str, str] = {}
    format = "<fffffffHBBB"
    native_format = bytearray("<fffffffHBBB", "ascii")
    orders = [8, 9, 7, 10, 0, 1, 2, 3, 4, 5, 6]
    lengths = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    array_lengths = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    crc_extra = 152
    unpacker = struct.Struct("<fffffffHBBB")

    def __init__(
        self,
        target_system: int,
        target_component: int,
        command: int,
        confirmation: int = 0,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        param5: float = 0,
        param6: float = 0,
        param7: float = 0,
    ):
        super().__init__(MAVLINK_MSG_ID_COMMAND_LONG, "COMMAND_LONG")
        self.target_system = target_system
        self.target_component = target_component
        self.command = command
        self.confirmation = confirmation
        self.param1 = param1
        self.param2 = param2
        self.param3 = param3
        self.param4 = param4
        self.param5 = param5
        self.param6 = param6
        self.param7 = param7

    def pack(self, mav: "MAVLink", force_mavlink1: bool = False) -> bytes:
        return self.pack_msg(
            mav,
            152,
            struct.pack(
                "<fffffffHBBB",
                self.param1,
                self.param2,
                self.param3,
                self.param4,
                self.param5,
                self.param6,
                self.param7,
                self.command,
                self.target_system,
                self.target_component,
                self.confirmation,
            ),
            force_mavlink1=force_mavlink1,
        )


class MAVLinkCommandACKMessage(MAVLinkMessage):
    id = MAVLINK_MSG_ID_COMMAND_ACK
    msgname = "COMMAND_ACK"
    field_names: ClassVar[Tuple[str, ...]] = (
        "command",
        "result",
        "progress",
        "result_param2",
        "target_system",
        "target_component",
    )
    ordered_field_names: ClassVar[Tuple[str, ...]] = (
        "command",
        "result",
        "progress",
        "result_param2",
        "target_system",
        "target_component",
    )
    fieldtypes: ClassVar[Tuple[str, ...]] = (
        "uint16_t",
        "uint8_t",
        "uint8_t",
        "int32_t",
        "uint8_t",
        "uint8_t",
    )
    fielddisplays_by_name: Dict[str, str] = {}
    fieldenums_by_name: Dict[str, str] = {
        "command": "MAV_CMD",
        "result": "MAV_RESULT",
    }
    fieldunits_by_name: Dict[str, str] = {}
    format = "<HBBiBB"
    native_format = bytearray("<HBBiBB", "ascii")
    orders = [0, 1, 2, 3, 4, 5]
    lengths = [1, 1, 1, 1, 1, 1]
    array_lengths = [0, 0, 0, 0, 0, 0]
    crc_extra = 143
    unpacker = struct.Struct("<HBBiBB")

    def __init__(
        self,
        command: int,
        result: int,
        progress: int = 0,
        result_param2: int = 0,
        target_system: int = 0,
        target_component: int = 0,
    ):
        super().__init__(MAVLINK_MSG_ID_COMMAND_ACK, "COMMAND_ACK")
        self.command = command
        self.result = result
        self.progress = progress
        self.result_param2 = result_param2
        self.target_system = target_system
        self.target_component = target_component

    def pack(self, mav: "MAVLink", force_mavlink1: bool = False) -> bytes:
        return self.pack_msg(
            mav,
            143,
            struct.pack(
                "<HBBiBB",
                self.command,
                self.result,
                self.progress,
                self.result_param2,
                self.target_system,
                self.target_component,
            ),
            force_mavlink1=force_mavlink1,
        )


mavlink_map = {
    MAVLINK_MSG_ID_HEARTBEAT: MAVLinkHeartbeatMessage,
    MAVLINK_MSG_ID_COMMAND_LONG: MAVLinkCommandLongMessage,
    MAVLINK_MSG_ID_COMMAND_ACK: MAVLinkCommandACKMessage,
}


class MAVError(Exception):
    """MAVLink error class"""

    def __init__(self, msg: str) -> None:
        super().__init__(msg)
        self.message = msg


def nulstr_to_str(s: str):
    i = s.find(chr(0))
    return s[:i] if i >= 0 else s


class MAVLinkBadData(MAVLinkMessage):
    """A piece of bad data in a MAVLink stream."""

    field_names: ClassVar[Tuple[str, ...]] = ("data", "reason")

    def __init__(self, data: Union[bytearray, bytes], reason: str) -> None:
        super().__init__(MAVLINK_MSG_ID_BAD_DATA, "BAD_DATA")
        self.data = data
        self.reason = reason
        self._msgbuf = data

    def __str__(self) -> str:
        """Override the __str__ function from MAVLinkMessages because non-printable characters are common in to be the reason for this message to exist."""
        data = [f"{i:x}" for i in self.data]
        return f"{self._type} {{{self.reason}, data:{data}}}"


class MAVLinkUnknownMessage(MAVLinkMessage):
    """
    a message that we don't have in the XML used when built
    """

    field_names: ClassVar[Tuple[str, ...]] = ("data",)

    def __init__(self, msg_id: int, data: Union[bytearray, bytes]) -> None:
        super().__init__(MAVLINK_MSG_ID_UNKNOWN, f"UNKNOWN_{msg_id}")
        self.data = data
        self._msgbuf = data

    def __str__(self) -> str:
        """Override the __str__ function from MAVLinkMessage because non-printable
        characters are common.
        """
        data = [f"{i:x}" for i in self.data]
        return f"{self._type} {{data:{data}}}"


class MAVLinkSigning:
    """MAVLink signing state class"""

    def __init__(self) -> None:
        self.secret_key: Optional[bytes] = None
        self.timestamp = 0
        self.link_id = 0
        self.sign_outgoing = False
        self.allow_unsigned_callback: Optional[Callable[["MAVLink", int], bool]] = None
        self.stream_timestamps: Dict[Tuple[int, int, int], int] = {}
        self.sig_count = 0
        self.badsig_count = 0
        self.goodsig_count = 0
        self.unsigned_count = 0
        self.reject_count = 0


class MAVLink:
    """MAVLink protocol handling class"""

    def __init__(
        self,
        source_system: int = 0,
        source_component: int = 0,
    ) -> None:
        self.seq = 0
        self.source_system = source_system
        self.source_component = source_component
        self.buf = bytearray()
        self.buf_index = 0
        self.expected_length = HEADER_LEN_V1 + 2
        self.have_prefix_error = False
        self.robust_parsing = False
        self.protocol_marker = 253
        self.little_endian = True
        self.crc_extra = True
        self.sort_fields = True
        self.signing = MAVLinkSigning()
        self.mav20_unpacker = struct.Struct("<cBBBBBBHB")
        self.mav10_unpacker = struct.Struct("<cBBBBB")
        self.mav20_h3_unpacker = struct.Struct("BBB")
        self.mav_csum_unpacker = struct.Struct("<H")
        self.mav_sign_unpacker = struct.Struct("<IH")

    def buf_len(self) -> int:
        return len(self.buf) - self.buf_index

    def bytes_needed(self) -> int:
        """return number of bytes needed for next parsing stage"""
        ret = self.expected_length - self.buf_len()
        return max(1, ret)

    def encode(self, msg: MAVLinkMessage, force_mavlink1: bool = False) -> bytes:
        """Encodes a MAVLink message and increases the sequence number."""
        buf = msg.pack(self, force_mavlink1=force_mavlink1)
        self.seq = (self.seq + 1) % 256
        return buf

    def parse_char(self, c: bytes) -> Optional[MAVLinkMessage]:
        """input some data bytes, possibly returning a new message"""
        self.buf.extend(c)

        m = self._parse_char_legacy()
        if m is None:
            # XXX The idea here is if we've read something and there's nothing left in
            # the buffer, reset it to 0 which frees the memory
            if self.buf_len() == 0 and self.buf_index != 0:
                self.buf = bytearray()
                self.buf_index = 0

        return m

    def _parse_char_legacy(self) -> Optional[MAVLinkMessage]:
        """input some data bytes, possibly returning a new message (uses no native code)"""
        m: MAVLinkMessage

        header_len = HEADER_LEN_V1
        if self.buf_len() >= 1 and self.buf[self.buf_index] == PROTOCOL_MARKER_V2:
            header_len = HEADER_LEN_V2

        if (
            self.buf_len() >= 1
            and self.buf[self.buf_index] != PROTOCOL_MARKER_V1
            and self.buf[self.buf_index] != PROTOCOL_MARKER_V2
        ):
            magic = self.buf[self.buf_index]
            self.buf_index += 1
            if self.robust_parsing:
                m = MAVLinkBadData(bytearray([magic]), "Bad prefix")
                self.expected_length = header_len + 2
                return m
            if self.have_prefix_error:
                return None
            self.have_prefix_error = True
            raise MAVError(f"invalid MAVLink prefix '{hex(magic)}'")
        self.have_prefix_error = False
        if self.buf_len() >= 3:
            sbuf = self.buf[self.buf_index : 3 + self.buf_index]
            (magic, self.expected_length, incompat_flags) = cast(
                Tuple[int, int, int],
                self.mav20_h3_unpacker.unpack(sbuf),
            )
            if magic == PROTOCOL_MARKER_V2 and (incompat_flags & MAVLINK_IFLAG_SIGNED):
                self.expected_length += MAVLINK_SIGNATURE_BLOCK_LEN
            self.expected_length += header_len + 2
        if (
            self.expected_length >= (header_len + 2)
            and self.buf_len() >= self.expected_length
        ):
            mbuf = self.buf[self.buf_index : self.buf_index + self.expected_length]
            self.buf_index += self.expected_length
            self.expected_length = header_len + 2
            if self.robust_parsing:
                try:
                    if (
                        magic == PROTOCOL_MARKER_V2
                        and (incompat_flags & ~MAVLINK_IFLAG_SIGNED) != 0
                    ):
                        raise MAVError(
                            f"invalid incompat_flags 0x{incompat_flags:x} 0x{magic:x} {self.expected_length}"
                        )
                    m = self.decode(mbuf)
                except MAVError as reason:
                    m = MAVLinkBadData(mbuf, reason.message)
            else:
                if (
                    magic == PROTOCOL_MARKER_V2
                    and (incompat_flags & ~MAVLINK_IFLAG_SIGNED) != 0
                ):
                    raise MAVError(
                        f"invalid incompat_flags 0x{incompat_flags:x} 0x{magic:x} {self.expected_length}"
                    )
                m = self.decode(mbuf)
            return m
        return None

    def parse_buffer(self, s: bytes) -> Optional[List[MAVLinkMessage]]:
        """input some data bytes, possibly returning a list of new messages"""
        m = self.parse_char(s)
        if m is None:
            return None
        ret = [m]
        while True:
            m = self.parse_char(b"")
            if m is None:
                return ret
            ret.append(m)

    def check_signature(
        self,
        msgbuf: Union[bytearray, bytes],
        source_system: int,
        source_component: int,
    ) -> bool:
        """check signature on incoming message"""
        if self.signing.secret_key is None:
            raise TypeError("No secret key supplied")

        timestamp_buf = msgbuf[-12:-6]
        link_id = msgbuf[-13]
        (tlow, thigh) = cast(
            Tuple[int, int],
            self.mav_sign_unpacker.unpack(timestamp_buf),
        )
        timestamp = tlow + (thigh << 32)

        # see if the timestamp is acceptable
        stream_key = (link_id, source_system, source_component)
        if stream_key in self.signing.stream_timestamps:
            if timestamp <= self.signing.stream_timestamps[stream_key]:
                # reject old timestamp
                # print("old timestamp")
                return False
        else:
            # a new stream has appeared. Accept the timestamp if it is at most
            # one minute behind our current timestamp
            if timestamp + 6000 * 1000 < self.signing.timestamp:
                # print("bad new stream ", timestamp/(100.0*1000*60*60*24*365), self.signing.timestamp/(100.0*1000*60*60*24*365))
                return False
            self.signing.stream_timestamps[stream_key] = timestamp
            # print("new stream")

        h = hashlib.new("sha256")
        h.update(self.signing.secret_key)
        h.update(msgbuf[:-6])
        sig1 = h.digest()[:6]
        sig2 = msgbuf[-6:]
        if sig1 != sig2:
            # print("sig mismatch")
            return False

        # the timestamp we next send with is the max of the received timestamp and
        # our current timestamp
        self.signing.timestamp = max(self.signing.timestamp, timestamp)
        return True

    def decode(self, msgbuf: Union[bytes, bytearray]) -> MAVLinkMessage:
        """decode a buffer as a MAVLink message"""
        # decode the header
        if msgbuf[0] != PROTOCOL_MARKER_V1:
            headerlen = 10
            try:
                (
                    magic,
                    mlen,
                    incompat_flags,
                    compat_flags,
                    seq,
                    source_system,
                    source_component,
                    msgIdlow,
                    msgIdhigh,
                ) = cast(
                    Tuple[bytes, int, int, int, int, int, int, int, int],
                    self.mav20_unpacker.unpack(msgbuf[:headerlen]),
                )
            except struct.error as emsg:
                raise MAVError(f"Unable to unpack MAVLink header: {emsg}") from None
            msgId = msgIdlow | (msgIdhigh << 16)
            mapkey = msgId
        else:
            headerlen = 6
            try:
                magic, mlen, seq, source_system, source_component, msgId = cast(
                    Tuple[bytes, int, int, int, int, int],
                    self.mav10_unpacker.unpack(msgbuf[:headerlen]),
                )
                incompat_flags = 0
                compat_flags = 0
            except struct.error as emsg:
                raise MAVError(f"Unable to unpack MAVLink header: {emsg}") from None
            mapkey = msgId
        if (incompat_flags & MAVLINK_IFLAG_SIGNED) != 0:
            signature_len = MAVLINK_SIGNATURE_BLOCK_LEN
        else:
            signature_len = 0

        if ord(magic) != PROTOCOL_MARKER_V1 and ord(magic) != PROTOCOL_MARKER_V2:
            raise MAVError(f"invalid MAVLink prefix '0x{magic.hex()}'")
        actual_msg_len = len(msgbuf) - (headerlen + 2 + signature_len)
        if mlen != actual_msg_len:
            raise MAVError(
                f"invalid MAVLink message length. Got {actual_msg_len} expected {mlen}, msgId={msgId} headerlen={headerlen}"
            )

        if mapkey not in mavlink_map:
            return MAVLinkUnknownMessage(msgId, msgbuf)

        # decode the payload
        message_type: Type[MAVLinkMessage] = mavlink_map[mapkey]
        fmt = message_type.format
        order_map = message_type.orders
        len_map = message_type.lengths
        crc_extra = message_type.crc_extra

        # decode the checksum
        try:
            (crc,) = cast(
                Tuple[int],
                self.mav_csum_unpacker.unpack(msgbuf[-(2 + signature_len) :][:2]),
            )
        except struct.error as emsg:
            raise MAVError(f"Unable to unpack MAVLink CRC: {emsg}") from None
        crcbuf = bytearray(msgbuf[1 : -(2 + signature_len)])
        if True:  # using CRC extra
            crcbuf.append(crc_extra)
        crc2 = x25crc(crcbuf)
        if crc != crc2.crc:
            raise MAVError(
                f"invalid MAVLink CRC in msgID {msgId} 0x{crc:04x} should be 0x{crc2.crc:04x}"
            )

        sig_ok = False
        if signature_len == MAVLINK_SIGNATURE_BLOCK_LEN:
            self.signing.sig_count += 1
        if self.signing.secret_key is not None:
            accept_signature = False
            if signature_len == MAVLINK_SIGNATURE_BLOCK_LEN:
                sig_ok = self.check_signature(msgbuf, source_system, source_component)
                accept_signature = sig_ok
                if sig_ok:
                    self.signing.goodsig_count += 1
                else:
                    self.signing.badsig_count += 1
                if (
                    not accept_signature
                    and self.signing.allow_unsigned_callback is not None
                ):
                    accept_signature = self.signing.allow_unsigned_callback(self, msgId)
                    if accept_signature:
                        self.signing.unsigned_count += 1
                    else:
                        self.signing.reject_count += 1
            elif self.signing.allow_unsigned_callback is not None:
                accept_signature = self.signing.allow_unsigned_callback(self, msgId)
                if accept_signature:
                    self.signing.unsigned_count += 1
                else:
                    self.signing.reject_count += 1
            if not accept_signature:
                raise MAVError("Invalid signature")

        csize = message_type.unpacker.size
        mbuf = bytearray(msgbuf[headerlen : -(2 + signature_len)])
        if len(mbuf) < csize:
            # zero pad to give right size
            mbuf.extend([0] * (csize - len(mbuf)))
        if len(mbuf) < csize:
            raise MAVError(
                f"Bad message of type {message_type} length {len(mbuf)} needs {csize}"
            )
        mbuf = mbuf[:csize]
        try:
            t = cast(
                Tuple[Union[bytes, int, float], ...],
                message_type.unpacker.unpack(mbuf),
            )
        except struct.error as emsg:
            raise MAVError(
                f"Unable to unpack MAVLink payload type={message_type} fmt={fmt} payloadLength={len(mbuf)}: {emsg}"
            ) from None

        tlist: List[Union[bytes, int, float, List[int], List[float]]] = list(t)
        # handle sorted fields
        if True:
            tlist_saved = list(t)
            if sum(len_map) == len(len_map):
                # message has no arrays in it
                for i in range(0, len(tlist)):
                    tlist[i] = tlist_saved[order_map[i]]
            else:
                # message has some arrays
                tlist = []
                for i in range(0, len(order_map)):
                    order = order_map[i]
                    L = len_map[order]
                    tip = sum(len_map[:order])
                    field = tlist_saved[tip]
                    if L == 1 or isinstance(field, bytes):
                        tlist.append(field)
                    else:
                        tlist.append(
                            cast(
                                Union[List[float], List[int]],
                                tlist_saved[tip : (tip + L)],
                            )
                        )

        # terminate any strings
        tlist_str: List[Union[str, int, float, List[int], List[float]]] = []
        for i in range(0, len(tlist)):
            if message_type.fieldtypes[i] == "char":
                tlist_str.append(nulstr_to_str(to_string(cast(bytes, tlist[i]))))
            else:
                tlist_str.append(
                    cast(Union[int, float, List[float], List[int]], tlist[i]),
                )
        msg_tuple = tuple(tlist_str)
        try:
            # Note that initializers don't follow the Liskov Substitution Principle
            # therefore it can't be typechecked by mypy
            m = message_type(*msg_tuple)  # type: ignore
        except Exception as emsg:
            raise MAVError(
                f"Unable to instantiate MAVLink message of type {message_type}: {emsg}"
            ) from None
        m._signed = sig_ok
        if m._signed:
            m._link_id = msgbuf[-13]
        m._msgbuf = msgbuf
        m._payload = msgbuf[6 : -(2 + signature_len)]
        m._crc = crc
        m._header = MAVLinkHeader(
            msgId,
            incompat_flags,
            compat_flags,
            mlen,
            seq,
            source_system,
            source_component,
        )
        return m
