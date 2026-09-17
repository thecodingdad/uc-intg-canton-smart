"""
Shared simple-command dispatch.

The media player and the remote entity expose an identical simple-command surface, so the
mapping from command name to device call lives here and is used by both.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging

from const import (
    BLUETOOTH_PAIR_COMMAND,
    EQ_RESET_COMMAND,
    EQ_STEP_COMMANDS,
    INPUT_COMMANDS,
    MENU_STEP_COMMANDS,
    MENU_VALUE_COMMANDS,
    MODE_COMMANDS,
    PRESET_COMMANDS,
)
from device import CantonDevice

_LOG = logging.getLogger(__name__)


async def handle_simple_command(  # pylint: disable=too-many-return-statements
    device: CantonDevice, command: str
) -> bool:
    """
    Execute a simple command on the device.

    :param device: The device to control
    :param command: Simple command name, e.g. "INPUT_TV" or "EQ_BASS_UP"
    :return: True if the command was known and executed, False otherwise
    """
    if command in INPUT_COMMANDS:
        await device.set_input_source(INPUT_COMMANDS[command])
        return True

    if command in MODE_COMMANDS:
        await device.set_play_mode(MODE_COMMANDS[command])
        return True

    if command in PRESET_COMMANDS:
        await device.recall_preset(PRESET_COMMANDS[command])
        return True

    if command in EQ_STEP_COMMANDS:
        band, delta = EQ_STEP_COMMANDS[command]
        await device.eq_step(band, delta)
        return True

    if command == EQ_RESET_COMMAND:
        await device.eq_reset()
        return True

    if command in MENU_STEP_COMMANDS:
        setting, direction = MENU_STEP_COMMANDS[command]
        await device.menu_step(setting, direction)
        return True

    if command in MENU_VALUE_COMMANDS:
        setting, value = MENU_VALUE_COMMANDS[command]
        await device.menu_set(setting, value)
        return True

    if command == BLUETOOTH_PAIR_COMMAND:
        await device.bluetooth_pair()
        return True

    _LOG.warning("[%s] Unknown simple command: %s", device.log_id, command)
    return False
