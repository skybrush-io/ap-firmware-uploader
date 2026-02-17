from typing import AsyncIterable

from ap_uploader.network import IPAddressAndPort, SharedUDPSocket
from ap_uploader.utils import looks_like_mavlink_heartbeat_from_autopilot

from .base import Scanner, UploadTarget

__all__ = ("UDPMAVLinkHeartbeatScanner",)


class UDPMAVLinkHeartbeatScanner(Scanner):
    """Scanner that listens for MAVLink heartbeat packets over a shared UDP
    socket and yields upload targets for each heartbeat that comes from an
    autopilot if we have not seen it yet.
    """

    _socket: SharedUDPSocket

    def __init__(self, socket: SharedUDPSocket):
        self._socket = socket

    async def run(self) -> AsyncIterable[UploadTarget]:
        yielded: set[IPAddressAndPort] = set()

        self._socket.allow_broadcasts()

        async with self._socket.unmatched_handler() as queue:
            async for payload, sender in queue:
                if (
                    sender not in yielded
                    and looks_like_mavlink_heartbeat_from_autopilot(payload)
                ):
                    yielded.add(sender)
                    host, port = sender
                    yield f"{host}:{port}"
