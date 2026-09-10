"""Advertise the bridge on the LAN so the device finds it without a configured IP."""

from __future__ import annotations

import logging
import socket
import threading

from zeroconf import ServiceInfo, Zeroconf

SERVICE_TYPE = "_handybridge._tcp.local."
log = logging.getLogger(__name__)


def should_readvertise(announced: str | None, current: str | None) -> bool:
    """Whether the announcement still names the address the machine answers on.

    The laptop moves between houses, and mDNS is registered once at startup with
    whatever address the default route had then. Left alone, the bridge keeps
    telling the network to find it at the old router's address.

    An address that cannot be read right now is not a change: a blip must not
    tear down a working announcement.
    """
    if not current:
        return False
    return announced != current


def _primary_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # no packet is sent; picks the default route
        return sock.getsockname()[0]
    finally:
        sock.close()


def current_ipv4() -> str:
    """The address the default route uses, or empty when there is no network."""
    try:
        return _primary_ipv4()
    except OSError:
        return ""


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


class AddressWatcher:
    """Re-registers the mDNS service when the machine's address changes.

    Without this the bridge has to be restarted after moving between networks,
    which is exactly the moment nobody remembers to do it.
    """

    def __init__(self, port: int, name: str = "handy-bridge", interval_s: float = 20.0) -> None:
        self._port = port
        self._name = name
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._announced = ""

    def start(self) -> None:
        try:
            self._zc, self._info = advertise(self._port, self._name)
            self._announced = current_ipv4()
        except OSError:
            log.warning("mDNS advertisement failed; the device will need a configured address")
        self._thread = threading.Thread(target=self._run, name="mdns-watch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            address = current_ipv4()
            if not should_readvertise(self._announced, address):
                continue
            log.info("address changed from %r to %r; re-advertising", self._announced, address)
            self._close()
            try:
                self._zc, self._info = advertise(self._port, self._name)
                self._announced = address
            except OSError:
                # Try again on the next tick rather than giving up for the session.
                log.warning("re-advertisement failed at %s", address)

    def _close(self) -> None:
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:  # noqa: BLE001 - shutting down; nothing to recover
                pass
        self._zc = None
        self._info = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._close()
