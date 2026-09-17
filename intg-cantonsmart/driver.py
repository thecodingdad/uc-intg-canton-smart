"""
Canton Smart integration driver for the Unfolded Circle Remote Two/3.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os

from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path

from const import DISCOVERY_TIMEOUT, DeviceConfig
from device import CantonDevice
from discover import CantonDiscovery
from media_player import CantonMediaPlayer
from remote import CantonRemote
from settings_entities import create_settings_entities
from setup import CantonSetupFlow


async def main():
    """Start the integration driver."""
    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    for logger in ("driver", "device", "protocol", "media_player", "remote", "setup"):
        logging.getLogger(logger).setLevel(level)

    driver = BaseIntegrationDriver(
        device_class=CantonDevice,
        entity_classes=[
            CantonMediaPlayer,
            CantonRemote,
            create_settings_entities,
        ],  # type: ignore[arg-type]
        driver_id="canton_smart",
    )

    driver.config_manager = BaseConfigManager(
        get_config_path(driver.api.config_dir_path),
        driver.on_device_added,
        driver.on_device_removed,
        config_class=DeviceConfig,
    )

    await driver.register_all_configured_devices()

    discovery = CantonDiscovery(timeout=DISCOVERY_TIMEOUT)
    setup_handler = CantonSetupFlow.create_handler(driver, discovery=discovery)

    await driver.api.init("driver.json", setup_handler)

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
