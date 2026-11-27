"""Shared fixtures: a fake Canton device on localhost and fast protocol timing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import ucapi_framework.device as framework_device

import device as device_module
import protocol
import tunnel_manager
from const import DeviceConfig
from device import CantonDevice

from .common import assert_disconnects_quickly
from .fake_device import FakeDevice


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink keepalive, timeouts and backoff so outages show up in milliseconds."""
    monkeypatch.setattr(protocol, "KEEPALIVE_INTERVAL", 0.3)
    monkeypatch.setattr(protocol, "COMMAND_TIMEOUT", 0.3)
    monkeypatch.setattr(protocol, "CLOSE_TIMEOUT", 0.5)
    monkeypatch.setattr(tunnel_manager, "SHARED_POLL_INTERVAL", 0.3)
    monkeypatch.setattr(tunnel_manager, "CLOSE_TIMEOUT", 0.5)
    monkeypatch.setattr(framework_device, "BACKOFF_SEC", 0.2)
    monkeypatch.setattr(device_module, "INITIAL_CONNECT_TIMEOUT", 3)


@pytest.fixture
async def fake() -> AsyncIterator[FakeDevice]:
    """A reachable fake device."""
    dev = FakeDevice()
    await dev.start()
    yield dev
    await dev.stop()


@pytest.fixture
async def canton(fake: FakeDevice) -> AsyncIterator[CantonDevice]:
    """The integration's device, connected to the fake device."""
    dev = CantonDevice(
        DeviceConfig(
            identifier="CC9093120230",
            name="Smart Connect",
            address="127.0.0.1",
            port=fake.luci_port,
            tunnel_port=fake.tunnel_port,
        ),
        loop=asyncio.get_running_loop(),
    )
    dev._backoff_current = 0.2  # pylint: disable=protected-access
    dev._backoff_max = 0.5  # pylint: disable=protected-access
    await dev.connect()
    assert dev.is_connected
    yield dev
    await assert_disconnects_quickly(dev)

