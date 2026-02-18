from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterable, Callable, Literal, Sequence, TypeAlias

from anyio import create_task_group, to_thread
from serial.tools.list_ports import comports

from ap_uploader.utils import (
    is_macos,
    looks_like_flight_controller_vid_pid_pair,
    periodic,
)

from .base import Scanner, UploadTarget

if TYPE_CHECKING:
    from serial.tools.list_ports_common import ListPortInfo

__all__ = ("SerialPortScanner",)


SerialPortState: TypeAlias = Literal[
    "to_yield", "yielded", "ignored", "inspecting", "new"
]


class SerialPortScanner(Scanner):
    """Scanner that scans for serial ports regularly and attempts to detect MAVLink
    heartbeats on them. Yields upload targets for each heartbeat that comes from an
    autopilot.
    """

    async def run(self) -> AsyncIterable[UploadTarget]:
        seen_ports: dict[str, SerialPortState] = {}

        def when_done(name: str, result: bool):
            if result:
                # port was chosen, yield it to the user
                seen_ports[name] = "to_yield"
            else:
                # port was chosen to be ignored
                seen_ports[name] = "ignored"

        async with create_task_group() as tg:
            async for _ in periodic(1):
                ports: Sequence[ListPortInfo] = await to_thread.run_sync(comports)
                names: set[str] = set()

                # Iterate over the current list of ports, decide what to do with them
                for port in ports:
                    name = port.name
                    names.add(name)
                    state = seen_ports.get(name, "new")
                    match state:
                        case "to_yield":
                            # port was chosen to be yielded to the user
                            seen_ports[port.name] = "yielded"
                            yield port.device

                        case "yielded":
                            # port was yielded already, nothing to do
                            pass

                        case "ignored":
                            # we decided to ignore the port already, nothing to do
                            pass

                        case "inspecting":
                            # port is already being inspected, nothing to do
                            pass

                        case "new":
                            # decide what to do with the port
                            seen_ports[port.name] = "inspecting"
                            tg.start_soon(self._inspect_port, name, port, when_done)

                        case _:
                            # unknown state, ignore
                            del seen_ports[name]

                # Remove any ports that are not seen any more
                to_delete = set(seen_ports.keys()) - names
                for name in to_delete:
                    del seen_ports[name]

            # Cancel all tasks in the task group; no more ports to yield
            tg.cancel_scope.cancel()

    async def _inspect_port(
        self, name: str, port: ListPortInfo, when_done: Callable[[str, bool], None]
    ):
        try:
            result = await self._inspect_port_inner(name, port)
        except Exception as ex:
            print(repr(ex))
            result = False
        when_done(name, result)

    async def _inspect_port_inner(self, name: str, port: ListPortInfo) -> bool:
        if not self._should_inspect_port(port):
            return False

        # TODO(ntamas): finish this
        return True

    @staticmethod
    def _should_inspect_port(port: ListPortInfo) -> bool:
        if is_macos():
            # On macOS, we can simply check whether the port name starts with
            # "cu.usbserial" or "cu.usbmodem"
            return port.name.startswith("cu.usbserial") or port.name.startswith(
                "cu.usbmodem"
            )

        # On all other platforms, we should check whether the port has a VID/PID
        # corresponding to a known autopilot or USB-to-serial adapter.
        vid, pid = port.vid, port.pid
        return looks_like_flight_controller_vid_pid_pair(vid, pid)
