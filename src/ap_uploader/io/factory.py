from ipaddress import ip_address

from .base import Transport

__all__ = ("create_transport",)


def create_transport(spec: str) -> Transport:
    """Creates a transport from a specification string.

    When the specification string is a valid IPv4 or IPv6 address, the returned
    transport will be an instance of UDPTransport_; otherwise it is assumed
    to be a serial port and the returned transport will be an instance of
    SerialTransport_.
    """
    from .serial import SerialPortTransport
    from .udp import UDPTransport

    try:
        ip_address(spec)
    except ValueError:
        return SerialPortTransport.from_url(spec)
    else:
        return UDPTransport(spec, 14555)
