"""Connection loss, reconnect and disconnect (ha-canton issue #3)."""

from __future__ import annotations

import asyncio

import pytest
from ucapi import media_player

from device import CantonDevice

from .common import (
    assert_disconnects_quickly,
    assert_luci_alive,
    assert_no_lingering_tasks,
    wait_until,
)
from .fake_device import FakeDevice

OUTAGES = ["go_unreachable", "reboot_away", "hang"]


@pytest.mark.parametrize("outage", OUTAGES)
async def test_reconnects_after_outage(
    canton: CantonDevice, fake: FakeDevice, outage: str
) -> None:
    """The device becomes unavailable during an outage and recovers afterwards."""
    assert canton.state != media_player.States.UNAVAILABLE

    await getattr(fake, outage)()
    assert await wait_until(
        lambda: canton.state == media_player.States.UNAVAILABLE
    ), "outage not detected"

    await fake.come_back()
    assert await wait_until(lambda: canton.is_connected), (
        "no reconnect after the device came back"
    )
    await assert_luci_alive(canton)
    assert canton.tunnel_mode == "exclusive"

    # The next outage is detected as well
    await fake.reboot_away()
    assert await wait_until(lambda: not canton.is_connected)
    await fake.come_back()
    assert await wait_until(lambda: canton.is_connected)
    await assert_luci_alive(canton)


@pytest.mark.parametrize("outage", OUTAGES)
async def test_disconnect_during_outage(
    canton: CantonDevice, fake: FakeDevice, outage: str
) -> None:
    """Disconnecting while the device is gone completes, and a later connect is real."""
    await getattr(fake, outage)()
    await asyncio.sleep(1.5)  # keepalives notice the loss

    await assert_disconnects_quickly(canton)
    await assert_no_lingering_tasks()

    await fake.come_back()
    await canton.connect()
    assert canton.is_connected
    await assert_luci_alive(canton)


async def test_disconnect_leaves_nothing_running(canton: CantonDevice) -> None:
    """A regular disconnect stops every background task."""
    await assert_disconnects_quickly(canton)
    await assert_no_lingering_tasks()


async def test_tunnel_shared_with_other_controller(
    canton: CantonDevice, fake: FakeDevice
) -> None:
    """When another controller takes the tunnel, the device stays usable."""
    assert canton.tunnel_mode == "exclusive"

    # Canton app takes the single tunnel slot
    _, app = await asyncio.open_connection("127.0.0.1", fake.tunnel_port)
    assert await wait_until(lambda: canton.tunnel_mode == "shared", 3)
    await asyncio.sleep(1.0)  # a few shared polls
    assert canton.is_connected
    assert canton.state != media_player.States.UNAVAILABLE

    app.close()
