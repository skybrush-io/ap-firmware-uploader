from anyio import sleep
from pytest import fixture

from ap_uploader.io.memory import InMemoryTransport
from ap_uploader.mock import MockBootloader
from ap_uploader.protocol import Command, DeviceInfoItem, OpCode


@fixture
def bl(nursery) -> InMemoryTransport:
    transport = InMemoryTransport()
    bootloader = MockBootloader(transport)
    nursery.start_soon(bootloader.run)
    transport = transport.peer
    object.__setattr__(transport, "mock", bootloader)
    return transport


async def operation_was_ok(bl: InMemoryTransport) -> bool:
    return (await bl.receive()) == bytes([OpCode.IN_SYNC, OpCode.OK])


async def test_mock_get_sync_command(bl: InMemoryTransport):
    await bl.send(Command.get_sync().to_bytes())
    assert await operation_was_ok(bl)


async def test_mock_get_device_info(bl: InMemoryTransport):
    await bl.send(Command.get_device_info(DeviceInfoItem.BL_REV).to_bytes())
    assert (await bl.receive()) == bytes([5, 0, 0, 0, OpCode.IN_SYNC, OpCode.OK])

    await bl.send(Command.get_device_info(DeviceInfoItem.BOARD_ID).to_bytes())
    assert (await bl.receive()) == bytes([9, 0, 0, 0, OpCode.IN_SYNC, OpCode.OK])

    await bl.send(Command.get_device_info(DeviceInfoItem.BOARD_REV).to_bytes())
    assert (await bl.receive()) == bytes([0, 0, 0, 0, OpCode.IN_SYNC, OpCode.OK])

    await bl.send(Command.get_device_info(DeviceInfoItem.FLASH_SIZE).to_bytes())
    flash_size = 2080768
    assert (await bl.receive()) == flash_size.to_bytes(4, byteorder="little") + bytes(
        [OpCode.IN_SYNC, OpCode.OK]
    )


async def test_mock_get_serial_number(bl: InMemoryTransport):
    await bl.send(Command.read_word_from_serial_number_area(0).to_bytes())
    assert (await bl.receive()) == bytes(
        [0x00, 0xEE, 0xFF, 0xC0, OpCode.IN_SYNC, OpCode.OK]
    )


async def test_mock_get_otp(bl: InMemoryTransport):
    await bl.send(Command.read_word_from_otp_area(0).to_bytes())
    assert (await bl.receive()) == bytes(
        [0xFF, 0xFF, 0xFF, 0xFF, OpCode.IN_SYNC, OpCode.OK]
    )


async def test_mock_prog_multi_and_chip_erase(bl):
    await bl.send(Command.program_bytes(b"\xde\xad\xbe\xef").to_bytes())
    assert await operation_was_ok(bl)
    assert bl.mock.flash_memory[0:4] == bytearray(b"\xde\xad\xbe\xef")

    await bl.send(Command.program_bytes(b"\xc0\xff\xee").to_bytes())
    assert await operation_was_ok(bl)
    assert bl.mock.flash_memory[0:7] == bytearray(b"\xde\xad\xbe\xef\xc0\xff\xee")

    del bl.mock.flash_memory[16:]
    await bl.send(Command.get_crc().to_bytes())
    crc = b"\xfe\xc1\x20\x8a"
    assert (await bl.receive()) == crc + bytes([OpCode.IN_SYNC, OpCode.OK])

    await bl.send(Command.erase_flash_memory().to_bytes())
    assert await operation_was_ok(bl)
    assert min(bl.mock.flash_memory) == 255


async def test_mock_prog_multi_zero_length(bl):
    await bl.send(Command.program_bytes(b"").to_bytes())
    assert await operation_was_ok(bl)
    assert min(bl.mock.flash_memory) == 255


async def test_mock_prog_multi_wrap_around(bl):
    bl.mock._flash_memory_write_ptr = len(bl.mock.flash_memory) - 2
    await bl.send(Command.program_bytes(b"\xde\xad\xbe\xef").to_bytes())
    assert await operation_was_ok(bl)
    assert bl.mock.flash_memory[:2] == bytearray(b"\xbe\xef")
    assert bl.mock.flash_memory[-2:] == bytearray(b"\xde\xad")


async def test_mock_reboot(bl):
    await bl.send(Command.reboot().to_bytes())
    assert await operation_was_ok(bl)
    assert bl.mock._should_exit


async def test_mock_invalid_opcodes(bl):
    await bl.send(b"\xfe\x87\x61\x11\x21\x20")
    assert (await bl.receive()) == bytes([OpCode.IN_SYNC, OpCode.OK])


async def test_mock_unhandled_opcode(bl):
    await bl.send(b"\x31\x20")
    assert (await bl.receive()) == bytes([OpCode.IN_SYNC, OpCode.INVALID])


async def test_mock_command_timeout(bl, autojump_clock):
    await bl.send(b"\x21")
    await sleep(5)
    assert (await bl.receive()) == bytes([OpCode.IN_SYNC, OpCode.INVALID])

    await bl.send(Command.get_chip_description().to_bytes()[:-1])
    await sleep(5)
    assert (await bl.receive()) == bytes([OpCode.IN_SYNC, OpCode.INVALID])

    await bl.send(Command.get_chip_description().to_bytes()[:-1] + b"\xff")
    assert (await bl.receive()) == bytes([OpCode.IN_SYNC, OpCode.INVALID])
