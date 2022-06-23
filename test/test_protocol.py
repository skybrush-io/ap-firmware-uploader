from pytest import fixture, raises

from ap_uploader.protocol import (
    NEED_MORE_DATA,
    ConnectionClosed,
    ExcessData,
    NeedMoreData,
    OpCode,
    Protocol,
    LocalProtocolError,
    Command,
    Response,
)


@fixture
def protocol() -> Protocol:
    return Protocol()


@fixture
def synced_protocol(protocol: Protocol) -> Protocol:
    protocol.send(Command.get_sync())
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.OK]))
    protocol.next_event()
    return protocol


def test_cannot_send_command_before_sync(protocol: Protocol):
    with raises(LocalProtocolError):
        protocol.send(Command.get_chip_type())


def test_sending_invalid_event(synced_protocol: Protocol):
    protocol = synced_protocol

    with raises(LocalProtocolError, match="event cannot be sent"):
        protocol.send(NeedMoreData())


def test_getting_sync(protocol: Protocol):
    assert protocol.next_event() is None
    protocol.send(Command.get_sync())
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.OK]))
    assert protocol.next_event() == Response.success()


def test_invalid_command(protocol: Protocol):
    assert protocol.next_event() is None
    protocol.send(Command.get_sync())
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.INVALID]))
    assert protocol.next_event() == Response.invalid()


def test_getting_response(protocol: Protocol):
    assert protocol.next_event() is None
    protocol.send(Command.get_sync())
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([OpCode.IN_SYNC]))
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([OpCode.OK]))
    assert protocol.next_event() == Response.success()
    protocol.send(Command.get_chip_type())
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([3, 1, 0]))
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([2, OpCode.IN_SYNC]))
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([OpCode.OK]))
    assert protocol.next_event() == Response.success(b"\x03\x01\x00\x02")


def test_getting_variable_length_response(synced_protocol: Protocol):
    protocol = synced_protocol

    protocol.send(Command.get_chip_description())
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(b"\x0e\x00\x00\x00spam spam")
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(b" spa")
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(b"m")
    assert protocol.next_event() is NEED_MORE_DATA
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.OK]))
    assert protocol.next_event() == Response.success(b"spam spam spam")


def test_next_event_when_closed(protocol: Protocol):
    assert protocol.next_event() is None
    protocol.send(Command.get_sync())
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.INVALID]))
    assert protocol.next_event() == Response.invalid()
    protocol.feed(b"")
    assert protocol.next_event() == ConnectionClosed()


def test_receive_when_closed(synced_protocol: Protocol):
    protocol = synced_protocol

    protocol.feed(b"")
    with raises(RuntimeError, match="closed"):
        assert protocol.feed(b"extra stuff")


def test_garbage_after_response(synced_protocol: Protocol):
    protocol = synced_protocol

    protocol.send(Command.get_chip_type())
    protocol.feed(bytes([3, 1, 0, 2]))
    protocol.feed(bytes([255, 255, 255, 255]))
    assert protocol.next_event() == ExcessData(b"\x03\x01\x00\x02\xff\xff\xff\xff")

    # Check that we need to re-sync
    with raises(LocalProtocolError):
        protocol.send(Command.get_chip_type())

    protocol.send(Command.get_sync())
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.OK]))
    assert protocol.next_event() == Response.success()


def test_garbage_after_sync_byte(synced_protocol: Protocol):
    protocol = synced_protocol

    protocol.send(Command.get_chip_type())
    protocol.feed(bytes([3, 1, 0, 2]))
    protocol.feed(bytes([OpCode.IN_SYNC, 255, 255, 255]))
    assert protocol.next_event() == ExcessData(b"\x03\x01\x00\x02\x12\xff")
    assert protocol.next_event() == ExcessData(b"\xff\xff")

    # Check that we need to re-sync
    with raises(LocalProtocolError):
        protocol.send(Command.get_chip_type())

    protocol.send(Command.get_sync())
    protocol.feed(bytes([OpCode.IN_SYNC, OpCode.OK]))
    assert protocol.next_event() == Response.success()
