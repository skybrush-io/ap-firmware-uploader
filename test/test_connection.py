from anyio import EndOfStream
from pytest import raises
from trio import sleep

from ap_uploader.connection import BootloaderConnection
from ap_uploader.io.memory import InMemoryTransport
from ap_uploader.mock import MockBootloader


class DelayedInMemoryTransport(InMemoryTransport):
    rx_delay: float = 0
    log: bool = False

    async def receive(self) -> bytes:
        delay = self.rx_delay
        if delay > 0:
            self.rx_delay = 0
            await sleep(delay)

        result = await super().receive()
        if self.log:
            print(f"<-- {result!r}")

        return result

    async def send(self, data: bytes) -> None:
        await super().send(data)
        print(f"--> {data!r}")


async def test_uploader_bug_1(nursery, autojump_clock):
    transport = DelayedInMemoryTransport(buffer_size=4)
    bootloader = MockBootloader(transport.peer)
    transport.log = True

    async def run_bootloader(task_status):
        try:
            await bootloader.run(task_status=task_status)
        except EndOfStream:
            pass

    await nursery.start(run_bootloader)

    async with BootloaderConnection(transport) as conn:
        bootloader.set_write_pointer(0x732C)

        # Make sure that we are in sync with the bootloader
        write_ptr = await conn.get_write_pointer()
        assert conn._protocol.in_sync
        assert write_ptr == 0x732C

        # Simulate that the next program_bytes call expires in the uploader
        transport.rx_delay = 600
        with raises(TimeoutError):
            await conn.program_bytes(b"\x00" * 252)

        # Next, the uploader attempts to get the write pointer
        write_ptr = await conn.get_write_pointer()
        assert write_ptr == 0x732C + 0xFC
