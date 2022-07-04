from anyio import create_udp_socket
from anyio.abc import SocketAttribute
from contextlib import aclosing
from socket import SOL_SOCKET, SO_BROADCAST
from typing import AsyncIterable, Set

from ap_uploader.network import IPAddressAndPort, SharedUDPSocket

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
        yielded: Set[IPAddressAndPort] = set()

        self._socket.allow_broadcasts()

        async with self._socket.unmatched_handler() as queue:
            async for payload, sender in queue:
                if not payload:
                    continue

                if sender in yielded:
                    continue

                # TODO(ntamas): handle multiple MAVLink packets stuffed into
                # a single UDP packet!

                found = False

                if payload[0] == 0xFE and len(payload) >= 8:
                    # Looks like a MAVLink 1 packet
                    if payload[4] == 1 and payload[5] == 0:
                        # Heartbeat packet from an autopilot
                        found = True

                elif payload[0] == 0xFD and len(payload) >= 12:
                    # Looks like a MAVLink 2 packet
                    if payload[6] == 1 and payload[7:10] == b"\x00\x00\x00":
                        # Heartbeat packet from an autopilot
                        found = True

                if found:
                    yielded.add(sender)
                    host, port = sender
                    yield f"{host}:{port}"
