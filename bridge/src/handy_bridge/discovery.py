"""Advertise the bridge on the LAN so the device finds it without a configured IP."""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

SERVICE_TYPE = "_handybridge._tcp.local."
log = logging.getLogger(__name__)


def _primary_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # no packet is sent; picks the default route
        return sock.getsockname()[0]
    finally:
        sock.close()


def advertise(port: int, name: str = "handy-bridge") -> tuple[Zeroconf, ServiceInfo]:
    address = _primary_ipv4()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(address)],
        port=port,
        properties={"version": "1", "path": "/v1/notes"},
        server=f"{name}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    log.info("advertising %s at %s:%d", SERVICE_TYPE, address, port)
    return zc, info
