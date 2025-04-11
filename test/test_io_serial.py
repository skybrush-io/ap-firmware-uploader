from anyio import BusyResourceError, ClosedResourceError, EndOfStream
from pytest import fixture, raises
from random import randint

from ap_uploader.io.serial import SerialPortByteStream, SerialPortTransport


@fixture
def loopback():
    return SerialPortByteStream.from_url("loop://")


@fixture
def transport():
    return SerialPortTransport.from_url("loop://")


async def test_send_receive(loopback: SerialPortByteStream):
    async with loopback:
        await loopback.send(b"spam ham bacon eggs")
        data = await loopback.receive_exactly(8)
        assert data == b"spam ham"

        data = await loopback.receive_exactly(4)
        assert data == b" bac"

        data = await loopback.receive_until(b" ", max_bytes=16)
        assert data == b"on"

        await loopback.send(b" and spam")
        data = await loopback.receive()
        assert data == b"eggs"
        data = await loopback.receive()
        assert data == b" "
        data = await loopback.receive()
        assert data == b"and spam"


async def test_baud_rate(loopback: SerialPortByteStream):
    loopback.baudrate = 921600
    assert loopback.baudrate == 921600

    loopback.baudrate = 57600
    assert loopback.baudrate == 57600


async def test_send_receive_when_closed(loopback: SerialPortByteStream):
    with raises(ClosedResourceError):
        await loopback.send(b"test")
    with raises(ClosedResourceError):
        await loopback.receive()
    with raises(ClosedResourceError):
        await loopback.receive_exactly(16)
    with raises(ClosedResourceError):
        await loopback.receive_until(b" ", max_bytes=16)


async def test_open_twice(loopback: SerialPortByteStream):
    async with loopback:
        with raises(BusyResourceError):
            async with loopback:
                pass


async def test_open_before_entering_context(loopback: SerialPortByteStream):
    loopback.wrapped_port.open()
    async with loopback:
        pass


async def test_serial_port_transport(nursery, transport: SerialPortTransport):
    async def producer():
        data = b"spam "
        to_send = 1000
        while to_send > 0:
            to_send_now = min(randint(1, 10), to_send)
            to_send -= to_send_now
            await transport.send(data * to_send_now)
        # await sleep(0.5)
        await transport.aclose()

    async def consumer():
        read: list[bytes] = []
        while True:
            try:
                read.append(await transport.receive())
            except EndOfStream:
                break
        assert b"".join(read) == b"spam " * 1000

    async with transport:
        nursery.start_soon(consumer)
        await producer()
