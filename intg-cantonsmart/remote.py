"""
Remote entity for Canton Smart Sound devices.

Exposes the same command surface as the media player entity, but as a remote with
physical button mappings and UI pages so everything is usable inside activities.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import ucapi
from ucapi import EntityTypes, remote
from ucapi.remote import Attributes, States
from ucapi.ui import (
    Buttons,
    DeviceButtonMapping,
    Size,
    UiPage,
    create_btn_mapping,
    create_ui_text,
)
from ucapi_framework import RemoteEntity, create_entity_id

from commands import handle_simple_command
from const import (
    MENU_IDS_BY_MODEL,
    MENU_SLEEP_TIMER,
    PLAY_STATUS_PLAYING,
    PRESET_COUNT,
    DeviceConfig,
    INPUT_COMMANDS,
    MODE_COMMANDS,
    TUNNEL_PHYSICAL_SOURCES,
    simple_commands_for_model,
)
from device import CantonDevice

_LOG = logging.getLogger(__name__)

FEATURES = [
    remote.Features.ON_OFF,
    remote.Features.TOGGLE,
    remote.Features.SEND_CMD,
]

# Media-player style commands the remote exposes in addition to the simple commands
MEDIA_COMMANDS = [
    "ON",
    "OFF",
    "TOGGLE",
    "VOLUME_UP",
    "VOLUME_DOWN",
    "MUTE",
    "UNMUTE",
    "MUTE_TOGGLE",
    "PLAY_PAUSE",
    "STOP",
    "NEXT",
    "PREVIOUS",
]


def _button_mapping() -> list[DeviceButtonMapping]:
    """Map the physical remote buttons to Canton commands."""
    return [
        create_btn_mapping(Buttons.POWER, "TOGGLE"),
        create_btn_mapping(Buttons.VOLUME_UP, "VOLUME_UP"),
        create_btn_mapping(Buttons.VOLUME_DOWN, "VOLUME_DOWN"),
        create_btn_mapping(Buttons.MUTE, "MUTE_TOGGLE"),
        create_btn_mapping(Buttons.PLAY, "PLAY_PAUSE"),
        create_btn_mapping(Buttons.NEXT, "NEXT"),
        create_btn_mapping(Buttons.PREV, "PREVIOUS"),
        create_btn_mapping(Buttons.STOP, "STOP"),
    ]


def _grid_page(page_id: str, name: str, commands: list[tuple[str, str]]) -> UiPage:
    """
    Build a 4x6 page of text buttons from (label, command) pairs.

    :param page_id: Page identifier
    :param name: Page title
    :param commands: Label/command pairs, laid out left to right, top to bottom
    :return: The UI page
    """
    page = UiPage(page_id, name, grid=Size(4, 6))
    for index, (label, command) in enumerate(commands[:24]):
        page.add(create_ui_text(label, index % 4, index // 4, cmd=command))
    return page


def _ui_pages(device_config: DeviceConfig) -> list[UiPage]:
    """
    Build the UI pages for the given device model.

    :param device_config: Device configuration (the model decides which menu pages exist)
    :return: List of UI pages
    """
    supported = set(simple_commands_for_model(device_config.model))

    pages: list[UiPage] = [
        _grid_page(
            "canton_sources",
            "Sources",
            [
                (TUNNEL_PHYSICAL_SOURCES[source_id], command)
                for command, source_id in INPUT_COMMANDS.items()
            ],
        ),
        _grid_page(
            "canton_modes",
            "Sound Modes",
            [(name, command) for command, name in MODE_COMMANDS.items()],
        ),
        _grid_page(
            "canton_presets",
            "Presets",
            [(f"Preset {i}", f"PRESET_{i}") for i in range(1, PRESET_COUNT + 1)],
        ),
    ]

    # EQ page: volume and EQ steppers with icons
    eq_page = UiPage("canton_eq", "EQ & Volume", grid=Size(4, 6))
    eq_page.add(create_ui_text("Bass", 0, 0))
    eq_page.add(create_ui_text("−", 1, 0, cmd="EQ_BASS_DOWN"))
    eq_page.add(create_ui_text("+", 2, 0, cmd="EQ_BASS_UP"))
    eq_page.add(create_ui_text("Mid", 0, 1))
    eq_page.add(create_ui_text("−", 1, 1, cmd="EQ_MID_DOWN"))
    eq_page.add(create_ui_text("+", 2, 1, cmd="EQ_MID_UP"))
    eq_page.add(create_ui_text("Treble", 0, 2))
    eq_page.add(create_ui_text("−", 1, 2, cmd="EQ_TREBLE_DOWN"))
    eq_page.add(create_ui_text("+", 2, 2, cmd="EQ_TREBLE_UP"))
    eq_page.add(create_ui_text("Reset", 3, 2, cmd="EQ_RESET"))

    row = 3
    if "SUBWOOFER_UP" in supported:
        eq_page.add(create_ui_text("Sub", 0, row))
        eq_page.add(create_ui_text("−", 1, row, cmd="SUBWOOFER_DOWN"))
        eq_page.add(create_ui_text("+", 2, row, cmd="SUBWOOFER_UP"))
        row += 1
    if "LIP_SYNC_UP" in supported:
        eq_page.add(create_ui_text("Lip Sync", 0, row))
        eq_page.add(create_ui_text("−", 1, row, cmd="LIP_SYNC_DOWN"))
        eq_page.add(create_ui_text("+", 2, row, cmd="LIP_SYNC_UP"))
        row += 1
    if "MAX_VOLUME_UP" in supported and row < 6:
        eq_page.add(create_ui_text("Max Vol", 0, row))
        eq_page.add(create_ui_text("−", 1, row, cmd="MAX_VOLUME_DOWN"))
        eq_page.add(create_ui_text("+", 2, row, cmd="MAX_VOLUME_UP"))
    pages.append(eq_page)

    # Settings page: only commands the model supports
    settings: list[tuple[str, str]] = []
    for label, command in (
        ("CEC On", "CEC_ON"),
        ("CEC Off", "CEC_OFF"),
        ("DRC On", "DRC_ON"),
        ("DRC Off", "DRC_OFF"),
        ("Voice On", "VOICE_CLARITY_ON"),
        ("Voice Off", "VOICE_CLARITY_OFF"),
        ("Touch On", "TOUCH_PANEL_ON"),
        ("Touch Off", "TOUCH_PANEL_OFF"),
        ("LED On", "LED_FLASHING_ON"),
        ("LED Off", "LED_FLASHING_OFF"),
        ("BT Pair", "BLUETOOTH_PAIR"),
    ):
        if command in supported:
            settings.append((label, command))
    if settings:
        pages.append(_grid_page("canton_settings", "Settings", settings))

    # Sleep timer / standby page
    timers: list[tuple[str, str]] = []
    if MENU_IDS_BY_MODEL[MENU_SLEEP_TIMER].get(device_config.model) is not None:
        timers += [
            ("15 Min", "SLEEP_TIMER_15"),
            ("30 Min", "SLEEP_TIMER_30"),
            ("45 Min", "SLEEP_TIMER_45"),
            ("60 Min", "SLEEP_TIMER_60"),
            ("Timer Off", "SLEEP_TIMER_OFF"),
        ]
    for label, command in (
        ("Standby ECO", "STANDBY_ECO"),
        ("Standby Net", "STANDBY_NETWORK"),
        ("Standby Sig", "STANDBY_SIGNAL"),
        ("Standby Man", "STANDBY_MANUAL"),
    ):
        if command in supported:
            timers.append((label, command))
    if timers:
        pages.append(_grid_page("canton_timer", "Timer & Standby", timers))

    return pages


class CantonRemote(RemoteEntity):
    """Remote entity representing a Canton device."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        """
        Create the remote entity.

        :param config_device: Device configuration
        :param device: Device instance to control
        """
        self._device = device
        self._device_id = config_device.identifier
        entity_id = create_entity_id(EntityTypes.REMOTE, config_device.identifier)

        super().__init__(
            entity_id,
            config_device.name,
            FEATURES,
            attributes={Attributes.STATE: self._remote_state()},
            simple_commands=simple_commands_for_model(config_device.model)
            + MEDIA_COMMANDS,
            button_mapping=_button_mapping(),
            ui_pages=_ui_pages(config_device),
            cmd_handler=self.handle_command,
        )

        self.subscribe_to_device(device)

    def _remote_state(self) -> States:
        """Map the device state to the remote entity state."""
        if not self._device.is_connected:
            return States.UNAVAILABLE
        return States.ON if self._device.data.power_on else States.OFF

    async def sync_state(self) -> None:
        """Push the current device state to the Remote."""
        self.update({Attributes.STATE: self._remote_state()})

    async def handle_command(
        self,
        _entity: RemoteEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """
        Handle a remote command.

        :param _entity: Entity receiving the command (unused)
        :param cmd_id: Command identifier
        :param params: Optional command parameters
        :return: Status code
        """
        if not self._device.is_connected:
            _LOG.warning("Command %s received but device is not connected", cmd_id)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        params = params or {}
        _LOG.info("[%s] Remote command: %s %s", self._device.log_id, cmd_id, params)

        try:
            match cmd_id:
                case remote.Commands.ON:
                    await self._device.set_power(True)
                case remote.Commands.OFF:
                    await self._device.set_power(False)
                case remote.Commands.TOGGLE:
                    await self._device.set_power(not self._device.data.power_on)
                case remote.Commands.SEND_CMD:
                    return await self._send_command(
                        params.get("command", ""), int(params.get("repeat", 1))
                    )
                case remote.Commands.SEND_CMD_SEQUENCE:
                    for command in params.get("sequence", []):
                        status = await self._send_command(command, 1)
                        if status != ucapi.StatusCodes.OK:
                            return status
                case _:
                    return await self._send_command(cmd_id, 1)

            return ucapi.StatusCodes.OK

        except (OSError, RuntimeError, ValueError) as ex:
            _LOG.error("Error executing command %s: %s", cmd_id, ex)
            return ucapi.StatusCodes.BAD_REQUEST

    async def _send_command(self, command: str, repeat: int) -> ucapi.StatusCodes:
        """
        Execute a simple or media command, optionally repeated.

        :param command: Command name
        :param repeat: Repeat count
        :return: Status code
        """
        if not command:
            return ucapi.StatusCodes.BAD_REQUEST

        for _ in range(max(1, repeat)):
            if await self._handle_media_command(command):
                continue
            if not await handle_simple_command(self._device, command):
                return ucapi.StatusCodes.NOT_IMPLEMENTED
        return ucapi.StatusCodes.OK

    async def _handle_media_command(  # pylint: disable=too-many-branches
        self, command: str
    ) -> bool:
        """
        Handle the media-player style commands the remote also exposes.

        :param command: Command name
        :return: True if the command was handled
        """
        match command:
            case "TOGGLE":
                await self._device.set_power(not self._device.data.power_on)
            case "ON":
                await self._device.set_power(True)
            case "OFF":
                await self._device.set_power(False)
            case "VOLUME_UP":
                await self._device.volume_step(1)
            case "VOLUME_DOWN":
                await self._device.volume_step(-1)
            case "MUTE_TOGGLE":
                await self._device.set_mute(not self._device.data.is_muted)
            case "MUTE":
                await self._device.set_mute(True)
            case "UNMUTE":
                await self._device.set_mute(False)
            case "PLAY_PAUSE":
                playing = self._device.data.play_status == PLAY_STATUS_PLAYING
                await self._device.play_control("PAUSE" if playing else "RESUME")
            case "STOP":
                await self._device.play_control("STOP")
            case "NEXT":
                await self._device.play_control("NEXT")
            case "PREVIOUS":
                await self._device.play_control("PREV")
            case _:
                return False
        return True
