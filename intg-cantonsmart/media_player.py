"""
Media player entity for Canton Smart Sound devices.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import ucapi
from ucapi import EntityTypes, media_player
from ucapi.media_player import Attributes, DeviceClasses
from ucapi_framework import MediaPlayerEntity, create_entity_id

from commands import handle_simple_command
from const import PLAY_STATUS_PLAYING, DeviceConfig, simple_commands_for_model
from device import CantonDevice

_LOG = logging.getLogger(__name__)

FEATURES = [
    media_player.Features.ON_OFF,
    media_player.Features.TOGGLE,
    media_player.Features.VOLUME,
    media_player.Features.VOLUME_UP_DOWN,
    media_player.Features.MUTE,
    media_player.Features.UNMUTE,
    media_player.Features.MUTE_TOGGLE,
    media_player.Features.SELECT_SOURCE,
    media_player.Features.SELECT_SOUND_MODE,
    media_player.Features.PLAY_PAUSE,
    media_player.Features.STOP,
    media_player.Features.NEXT,
    media_player.Features.PREVIOUS,
    media_player.Features.SEEK,
    media_player.Features.REPEAT,
    media_player.Features.SHUFFLE,
    media_player.Features.MEDIA_DURATION,
    media_player.Features.MEDIA_POSITION,
    media_player.Features.MEDIA_TITLE,
    media_player.Features.MEDIA_ARTIST,
    media_player.Features.MEDIA_ALBUM,
    media_player.Features.MEDIA_IMAGE_URL,
    media_player.Features.MEDIA_TYPE,
]

# LUCI play control payloads for the transport commands
_PLAY_CONTROL = {
    media_player.Commands.STOP: "STOP",
    media_player.Commands.NEXT: "NEXT",
    media_player.Commands.PREVIOUS: "PREV",
}

_REPEAT_PAYLOAD = {
    media_player.RepeatMode.OFF: "REPEAT:OFF",
    media_player.RepeatMode.ONE: "REPEAT:ONE",
    media_player.RepeatMode.ALL: "REPEAT:ALL",
}


class CantonMediaPlayer(MediaPlayerEntity):
    """Media player entity representing a Canton device."""

    def __init__(self, config_device: DeviceConfig, device: CantonDevice) -> None:
        """
        Create the media player entity.

        :param config_device: Device configuration
        :param device: Device instance to control
        """
        self._device = device
        self._device_id = config_device.identifier
        entity_id = create_entity_id(EntityTypes.MEDIA_PLAYER, config_device.identifier)

        super().__init__(
            entity_id,
            config_device.name,
            FEATURES,
            attributes={
                Attributes.STATE: device.state,
                Attributes.VOLUME: device.volume_percent,
                Attributes.MUTED: False,
                Attributes.SOURCE: device.source or "",
                Attributes.SOURCE_LIST: device.source_list,
                Attributes.SOUND_MODE: "",
                Attributes.SOUND_MODE_LIST: [],
            },
            device_class=DeviceClasses.SPEAKER,
            options={
                media_player.Options.SIMPLE_COMMANDS: simple_commands_for_model(
                    config_device.model
                )
            },
            cmd_handler=self.handle_command,
        )

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Push the current device state to the Remote."""
        attributes = self._device.get_media_player_attributes(self._device_id)
        if attributes is None:
            self.set_unavailable()
            return
        self.update(attributes)

    async def handle_command(  # pylint: disable=too-many-branches
        self,
        _entity: MediaPlayerEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """
        Handle a media player command from the Remote.

        :param _entity: Entity receiving the command (unused)
        :param cmd_id: Command identifier
        :param params: Optional command parameters
        :return: Status code
        """
        if not self._device.is_connected:
            _LOG.warning("Command %s received but device is not connected", cmd_id)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        params = params or {}
        _LOG.info("[%s] Command: %s %s", self._device.log_id, cmd_id, params)

        try:
            match cmd_id:
                case media_player.Commands.ON:
                    await self._device.set_power(True)
                case media_player.Commands.OFF:
                    await self._device.set_power(False)
                case media_player.Commands.TOGGLE:
                    await self._device.set_power(not self._device.data.power_on)

                case media_player.Commands.VOLUME:
                    await self._device.set_volume_percent(int(params.get("volume", 0)))
                case media_player.Commands.VOLUME_UP:
                    await self._device.volume_step(1)
                case media_player.Commands.VOLUME_DOWN:
                    await self._device.volume_step(-1)
                case media_player.Commands.MUTE:
                    await self._device.set_mute(True)
                case media_player.Commands.UNMUTE:
                    await self._device.set_mute(False)
                case media_player.Commands.MUTE_TOGGLE:
                    await self._device.set_mute(not self._device.data.is_muted)

                case media_player.Commands.SELECT_SOURCE:
                    await self._select_source(params.get("source", ""))
                case media_player.Commands.SELECT_SOUND_MODE:
                    await self._device.set_play_mode(params.get("mode", ""))

                case media_player.Commands.PLAY_PAUSE:
                    # The device has separate resume/pause commands
                    playing = self._device.data.play_status == PLAY_STATUS_PLAYING
                    await self._device.play_control("PAUSE" if playing else "RESUME")
                case (
                    media_player.Commands.STOP
                    | media_player.Commands.NEXT
                    | media_player.Commands.PREVIOUS
                ):
                    await self._device.play_control(_PLAY_CONTROL[cmd_id])
                case media_player.Commands.SEEK:
                    position = int(params.get("media_position", 0))
                    await self._device.play_control(f"SEEK:{position * 1000}")
                case media_player.Commands.SHUFFLE:
                    shuffle = bool(params.get("shuffle", False))
                    await self._device.play_control(
                        f"SHUFFLE:{'ON' if shuffle else 'OFF'}"
                    )
                case media_player.Commands.REPEAT:
                    mode = params.get("repeat", media_player.RepeatMode.OFF)
                    await self._device.play_control(
                        _REPEAT_PAYLOAD.get(mode, "REPEAT:OFF")
                    )

                case _:
                    if not await handle_simple_command(self._device, cmd_id):
                        return ucapi.StatusCodes.NOT_IMPLEMENTED

            return ucapi.StatusCodes.OK

        except (OSError, RuntimeError, ValueError) as ex:
            _LOG.error("Error executing command %s: %s", cmd_id, ex)
            return ucapi.StatusCodes.BAD_REQUEST

    async def _select_source(self, source: str) -> None:
        """
        Select an input or recall a preset, depending on the source list mode.

        :param source: Source name from the source list
        """
        if source.startswith("Preset "):
            try:
                await self._device.recall_preset(int(source.split()[-1]))
            except (ValueError, IndexError):
                _LOG.warning("Invalid preset source: %s", source)
            return
        await self._device.set_input(source)
