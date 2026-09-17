"""
Device discovery.

Canton devices answer to LSSDP (a UPnP/SSDP variant on UDP port 1800 with the
``DDMSServer`` search target), so the framework's SSDP helper cannot be used — the
discovery from the protocol module is wrapped instead.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging

from ucapi_framework import BaseDiscovery, DiscoveredDevice

from const import DEFAULT_LUCI_PORT, DEFAULT_TUNNEL_PORT, DISCOVERY_TIMEOUT
from protocol import discover_devices

_LOG = logging.getLogger(__name__)


class CantonDiscovery(BaseDiscovery):
    """Discover Canton Smart Sound devices via LSSDP."""

    def __init__(self, timeout: int = DISCOVERY_TIMEOUT) -> None:
        """
        Create the discovery.

        :param timeout: Discovery timeout in seconds
        """
        super().__init__(timeout)

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Search the local network for Canton devices.

        :return: Discovered devices, with the LSSDP metadata in ``extra_data``
        """
        devices = await discover_devices(timeout=self.timeout)
        _LOG.debug("LSSDP discovery found %s device(s)", len(devices))

        self._discovered_devices = [
            DiscoveredDevice(
                identifier=device["usn"],
                name=device.get("name") or "Canton Device",
                address=device["host"],
                extra_data={
                    "port": device.get("port", DEFAULT_LUCI_PORT),
                    "tunnel_port": device.get("tunnel_port", DEFAULT_TUNNEL_PORT),
                    "model": device.get("model", ""),
                    "fw_version": device.get("fw_version", ""),
                    "wifi_band": device.get("wifi_band", ""),
                },
            )
            for device in devices
        ]
        return self._discovered_devices
