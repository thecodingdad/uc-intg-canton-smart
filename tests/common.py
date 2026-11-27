"""Test helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from const import MID_VOLUME
from device import CantonDevice


async def wait_until(predicate: Callable[[], bool], timeout: float = 5) -> bool:
    """Poll a condition until it holds or the timeout expires."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def assert_luci_alive(canton: CantonDevice) -> None:
    """LUCI really answers — not just a connection that is still marked connected."""
    # pylint: disable=protected-access
    assert await canton._luci.async_send(MID_VOLUME) is not None


async def assert_no_lingering_tasks() -> None:
    """Nothing of the connection keeps running after a disconnect."""
    await asyncio.sleep(0.1)
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    assert not pending, pending


async def assert_disconnects_quickly(dev: CantonDevice) -> None:
    """Disconnect must finish promptly.

    Timed instead of relying on wait_for alone: the framework's disconnect() swallows
    the CancelledError a wait_for timeout would raise.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(dev.disconnect(), timeout=10)
    assert loop.time() - start < 3, "disconnect() hung"
