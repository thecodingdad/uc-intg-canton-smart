"""
Optional settings entities.

These switch, select and sensor entities mirror the Home Assistant integration's entity set
and give direct state feedback for the OSD menu settings. They are only created when
``settings_entities`` is enabled during setup — by default the media player and the remote
entity already cover every function via simple commands.

Only entities the device model actually supports are created (see ``MENU_IDS_BY_MODEL``).

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import ucapi
from ucapi import EntityTypes, select, sensor, switch
from ucapi_framework import (
    SelectEntity,
    SensorEntity,
    SwitchEntity,
    create_entity_id,
)

from const import (
    DeviceConfig,
    INPUT_SELECTION_OPTIONS,
    INPUT_SELECTION_REVERSE,
    MENU_CEC,
    MENU_DRC,
    MENU_INPUT_SELECTION,
    MENU_INPUT_STREAM_DISPLAY,
    MENU_LED_FLASHING,
    MENU_RF_CHANNEL,
    MENU_RF_POWER,
    MENU_SLAVE_DISPLAY,
    MENU_SLEEP_TIMER,
    MENU_STANDBY_MODE,
    MENU_TOUCH_PANEL,
    MENU_VOICE_CLARITY,
    PRESET_COUNT,
    RF_CHANNEL_OPTIONS,
    RF_CHANNEL_REVERSE,
    RF_POWER_OPTIONS,
    RF_POWER_REVERSE,
    SLEEP_TIMER_OPTIONS,
    SLEEP_TIMER_REVERSE,
    STANDBY_MODE_OPTIONS,
    STANDBY_MODE_REVERSE,
    TUNNEL_PLAY_MODES,
)
from device import CantonDevice

_LOG = logging.getLogger(__name__)

# (menu setting, entity suffix, label, inverted)
# Touch Panel is inverted on the device: 0 = enabled, 1 = disabled.
SWITCH_DEFINITIONS: list[tuple[str, str, str, bool]] = [
    (MENU_CEC, "cec", "HDMI CEC", False),
    (MENU_DRC, "drc", "Dynamic Range Compression", False),
    (MENU_VOICE_CLARITY, "voice_clarity", "Voice Clarity", False),
    (MENU_TOUCH_PANEL, "touch_panel", "Touch Panel", True),
    (MENU_LED_FLASHING, "led_flashing", "LED Flashing", False),
    (MENU_INPUT_STREAM_DISPLAY, "input_stream_display", "Input Stream Display", False),
    (MENU_SLAVE_DISPLAY, "slave_display", "Slave Speaker Display", False),
]

# (menu setting, entity suffix, label, value->label map, label->value map)
MENU_SELECT_DEFINITIONS: list[tuple[str, str, str, dict[int, str], dict[str, int]]] = [
    (
        MENU_SLEEP_TIMER,
        "sleep_timer",
        "Sleep Timer",
        SLEEP_TIMER_OPTIONS,
        SLEEP_TIMER_REVERSE,
    ),
    (
        MENU_STANDBY_MODE,
        "standby_mode",
        "Standby Mode",
        STANDBY_MODE_OPTIONS,
        STANDBY_MODE_REVERSE,
    ),
    (
        MENU_INPUT_SELECTION,
        "input_selection",
        "Input Selection",
        INPUT_SELECTION_OPTIONS,
        INPUT_SELECTION_REVERSE,
    ),
    (MENU_RF_POWER, "rf_power", "RF Power", RF_POWER_OPTIONS, RF_POWER_REVERSE),
    (
        MENU_RF_CHANNEL,
        "rf_channel",
        "RF Channel",
        RF_CHANNEL_OPTIONS,
        RF_CHANNEL_REVERSE,
    ),
]


def create_settings_entities(
    config_device: DeviceConfig, device: CantonDevice
) -> list[Any]:
    """
    Create the optional settings entities for a device.

    :param config_device: Device configuration
    :param device: Device instance
    :return: List of entities — empty if the option is disabled
    """
    if not config_device.settings_entities:
        return []

    entities: list[Any] = [
        CantonInputSelect(config_device, device),
        CantonPlayModeSelect(config_device, device),
        CantonPresetSelect(config_device, device),
        CantonMuteSwitch(config_device, device),
        CantonSourceSensor(config_device, device),
    ]

    entities += [
        CantonMenuSwitch(config_device, device, setting, suffix, label, inverted)
        for setting, suffix, label, inverted in SWITCH_DEFINITIONS
        if device.menu_id(setting) is not None
    ]
    entities += [
        CantonMenuSelect(config_device, device, setting, suffix, label, options, reverse)
        for setting, suffix, label, options, reverse in MENU_SELECT_DEFINITIONS
        if device.menu_id(setting) is not None
    ]

    _LOG.debug(
        "[%s] Created %s settings entities", device.log_id, len(entities)
    )
    return entities


class _CantonSelect(SelectEntity):
    """Base class for Canton select entities."""

    def __init__(
        self,
        config_device: DeviceConfig,
        device: CantonDevice,
        suffix: str,
        label: str,
        options: list[str],
    ) -> None:
        self._device = device
        super().__init__(
            create_entity_id(EntityTypes.SELECT, config_device.identifier, suffix),
            f"{config_device.name} {label}",
            attributes={
                select.Attributes.STATE: select.States.ON,
                select.Attributes.CURRENT_OPTION: "",
                select.Attributes.OPTIONS: options,
            },
            cmd_handler=self.handle_command,
        )
        self.subscribe_to_device(device)

    async def select_option(self, option: str) -> None:
        """Apply the selected option on the device."""
        raise NotImplementedError

    async def handle_command(
        self,
        _entity: SelectEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """Handle a select command."""
        if not self._device.is_connected:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        options = self.select_options or []
        current = self.current_option
        index = options.index(current) if current in options else -1

        match cmd_id:
            case select.Commands.SELECT_OPTION:
                option = (params or {}).get("option", "")
            case select.Commands.SELECT_FIRST:
                option = options[0] if options else ""
            case select.Commands.SELECT_LAST:
                option = options[-1] if options else ""
            case select.Commands.SELECT_NEXT:
                option = options[(index + 1) % len(options)] if options else ""
            case select.Commands.SELECT_PREVIOUS:
                option = options[(index - 1) % len(options)] if options else ""
            case _:
                return ucapi.StatusCodes.NOT_IMPLEMENTED

        if not option:
            return ucapi.StatusCodes.BAD_REQUEST

        await self.select_option(option)
        return ucapi.StatusCodes.OK


class CantonInputSelect(_CantonSelect):
    """Input source selector."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        super().__init__(
            config_device, device, "input", "Input", device.source_list
        )

    async def select_option(self, option: str) -> None:
        """Select the given input."""
        await self._device.set_input(option)

    async def sync_state(self) -> None:
        """Push the current input and the device's input list to the Remote."""
        self.update(
            {
                select.Attributes.STATE: select.States.ON,
                select.Attributes.OPTIONS: self._device.source_list,
                select.Attributes.CURRENT_OPTION: self._device.data.input_name or "",
            }
        )


class CantonPlayModeSelect(_CantonSelect):
    """Sound mode selector."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        super().__init__(
            config_device,
            device,
            "play_mode",
            "Play Mode",
            list(dict.fromkeys(TUNNEL_PLAY_MODES.values())),
        )

    async def select_option(self, option: str) -> None:
        """Select the given sound mode."""
        await self._device.set_play_mode(option)

    async def sync_state(self) -> None:
        """Push the current sound mode to the Remote."""
        self.update(
            {
                select.Attributes.STATE: select.States.ON,
                select.Attributes.CURRENT_OPTION: self._device.data.play_mode or "",
            }
        )


class CantonPresetSelect(_CantonSelect):
    """Preset selector."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        super().__init__(
            config_device,
            device,
            "preset",
            "Preset",
            [f"Preset {i}" for i in range(1, PRESET_COUNT + 1)],
        )

    async def select_option(self, option: str) -> None:
        """Recall the selected preset."""
        try:
            await self._device.recall_preset(int(option.split()[-1]))
        except (ValueError, IndexError):
            _LOG.warning("Invalid preset option: %s", option)

    async def sync_state(self) -> None:
        """Push the active preset and the configured preset list to the Remote."""
        presets = self._device.data.configured_presets or list(
            range(1, PRESET_COUNT + 1)
        )
        active = self._device.data.active_preset
        self.update(
            {
                select.Attributes.STATE: select.States.ON,
                select.Attributes.OPTIONS: [f"Preset {i}" for i in presets],
                select.Attributes.CURRENT_OPTION: (
                    f"Preset {active}" if active > 0 else ""
                ),
            }
        )


class CantonMenuSelect(_CantonSelect):
    """Select entity for an enum OSD menu setting."""

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        config_device: DeviceConfig,
        device: CantonDevice,
        setting: str,
        suffix: str,
        label: str,
        options_map: dict[int, str],
        reverse_map: dict[str, int],
    ) -> None:
        self._setting = setting
        self._options_map = options_map
        self._reverse_map = reverse_map
        super().__init__(
            config_device, device, suffix, label, list(options_map.values())
        )

    async def select_option(self, option: str) -> None:
        """Write the selected value to the device menu."""
        value = self._reverse_map.get(option)
        if value is not None:
            await self._device.menu_set(self._setting, value)

    async def sync_state(self) -> None:
        """Push the cached menu value to the Remote."""
        value = self._device.menu_value(self._setting)
        self.update(
            {
                select.Attributes.STATE: select.States.ON,
                select.Attributes.CURRENT_OPTION: self._options_map.get(value, "")
                if value is not None
                else "",
            }
        )


class _CantonSwitch(SwitchEntity):
    """Base class for Canton switch entities."""

    def __init__(
        self,
        config_device: DeviceConfig,
        device: CantonDevice,
        suffix: str,
        label: str,
    ) -> None:
        self._device = device
        super().__init__(
            create_entity_id(EntityTypes.SWITCH, config_device.identifier, suffix),
            f"{config_device.name} {label}",
            features=[switch.Features.ON_OFF, switch.Features.TOGGLE],
            attributes={switch.Attributes.STATE: switch.States.UNKNOWN},
            device_class=switch.DeviceClasses.SWITCH,
            options={switch.Options.READABLE: True},
            cmd_handler=self.handle_command,
        )
        self.subscribe_to_device(device)

    async def set_value(self, on: bool) -> None:
        """Apply the new switch value on the device."""
        raise NotImplementedError

    def current_value(self) -> bool | None:
        """Return the current switch value, or None if unknown."""
        raise NotImplementedError

    async def handle_command(
        self,
        _entity: SwitchEntity,
        cmd_id: str,
        _params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """Handle a switch command."""
        if not self._device.is_connected:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        match cmd_id:
            case switch.Commands.ON:
                await self.set_value(True)
            case switch.Commands.OFF:
                await self.set_value(False)
            case switch.Commands.TOGGLE:
                await self.set_value(not bool(self.current_value()))
            case _:
                return ucapi.StatusCodes.NOT_IMPLEMENTED
        return ucapi.StatusCodes.OK

    async def sync_state(self) -> None:
        """Push the current switch value to the Remote."""
        value = self.current_value()
        if value is None:
            self.update({switch.Attributes.STATE: switch.States.UNKNOWN})
            return
        self.update(
            {
                switch.Attributes.STATE: (
                    switch.States.ON if value else switch.States.OFF
                )
            }
        )


class CantonMuteSwitch(_CantonSwitch):
    """Mute switch."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        super().__init__(config_device, device, "mute", "Mute")

    def current_value(self) -> bool | None:
        """Return the current mute state."""
        return self._device.data.is_muted

    async def set_value(self, on: bool) -> None:
        """Mute or unmute the device."""
        await self._device.set_mute(on)


class CantonMenuSwitch(_CantonSwitch):
    """Switch entity for an on/off OSD menu setting."""

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        config_device: DeviceConfig,
        device: CantonDevice,
        setting: str,
        suffix: str,
        label: str,
        inverted: bool,
    ) -> None:
        self._setting = setting
        self._inverted = inverted
        super().__init__(config_device, device, suffix, label)

    def current_value(self) -> bool | None:
        """Return the current menu value as a boolean."""
        value = self._device.menu_value(self._setting)
        if value is None:
            return None
        return value == 0 if self._inverted else value == 1

    async def set_value(self, on: bool) -> None:
        """Write the new value to the device menu."""
        if self._inverted:
            await self._device.menu_set(self._setting, 0 if on else 1)
        else:
            await self._device.menu_set(self._setting, 1 if on else 0)


class CantonSourceSensor(SensorEntity):
    """Sensor showing the current physical source (e.g. OPT 1, HDMI TV)."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        self._device = device
        super().__init__(
            create_entity_id(
                EntityTypes.SENSOR, config_device.identifier, "physical_source"
            ),
            f"{config_device.name} Physical Source",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.ON,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Push the current physical source to the Remote."""
        self.update(
            {
                sensor.Attributes.STATE: sensor.States.ON,
                sensor.Attributes.VALUE: self._device.data.source_name or "",
            }
        )
