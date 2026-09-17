"""
Canton device communication.

Port of the Home Assistant integration's ``CantonHub`` (https://github.com/thecodingdad/ha-canton)
on top of the framework's ``PersistentConnectionDevice``: LUCI (TCP 7777) carries registration,
streaming volume, playback control and metadata, the tunnel (TCP 50006) carries power, input,
sound mode, EQ, presets and all OSD menu settings.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import json
import logging
from asyncio import AbstractEventLoop
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ucapi import media_player
from ucapi_framework import BaseConfigManager, PersistentConnectionDevice
from ucapi_framework.helpers import MediaPlayerAttributes

from const import (
    CMD_GET,
    CMD_SET,
    DeviceConfig,
    EQ_MAX,
    EQ_MIN,
    MENU_IDS_BY_MODEL,
    MENU_EXIT_COUNT,
    MENU_EXIT_COUNT_DEFAULT,
    MENU_RANGES,
    MID_CURRENT_PLAY_STATUS,
    MID_GET_UI,
    MID_PLAY_CONTROL,
    MID_PLAY_ELAPSED,
    MID_VOLUME,
    PLAY_STATUS_BUFFERING,
    PLAY_STATUS_CONNECTING,
    PLAY_STATUS_PAUSED,
    PLAY_STATUS_PLAYING,
    PLAY_STATUS_RECEIVING,
    INPUT_NAME_UNASSIGNED,
    PRESET_COUNT,
    RECONNECT_BACKOFF_MAX,
    SELECTABLE_INPUT_NAMES,
    SOURCE_MODE_PRESETS,
    TCMD_BT_PAIR,
    TCMD_EQ_GET,
    TCMD_EQ_SET,
    TCMD_MENU_EXIT,
    TCMD_MENU_GET,
    TCMD_MENU_SET,
    TCMD_MUTE_GET,
    TCMD_MUTE_SET,
    TCMD_PRESET_GET,
    TCMD_PRESET_RECALL,
    TCMD_SOURCE_GET,
    TCMD_SOURCE_INFO_GET,
    TCMD_SOURCE_SET,
    TCMD_STANDBY_GET,
    TCMD_STANDBY_SET,
    TCMD_VOLUME_GET,
    TCMD_VOLUME_SET,
    TUNNEL_INPUT_NAMES,
    TUNNEL_INPUT_NAMES_REVERSE,
    TUNNEL_PHYSICAL_SOURCES,
    TUNNEL_PLAY_MODES,
    TUNNEL_PLAY_MODES_REVERSE,
)
from protocol import LuciMessage, LuciProtocol, TunnelProtocol
from tunnel_manager import TunnelManager, TunnelMode

_LOG = logging.getLogger(__name__)

# Sources that are served by LUCI instead of the tunnel (volume, transport, metadata)
NETWORK_SOURCES = ("NET", "BT")


@dataclass
class CantonState:
    """Current state of a Canton device."""

    power_on: bool = True
    volume: int = 0
    volume_max: int = 70
    is_muted: bool = False
    play_status: int | None = None  # LUCI play status for NET/BT
    media_title: str | None = None
    media_artist: str | None = None
    media_album: str | None = None
    media_image_url: str | None = None
    media_duration: int | None = None
    media_position: int | None = None
    shuffle: bool = False
    repeat: int = 0  # 0=Off, 1=One, 2=All
    input_name_id: int = 0
    input_name: str = ""
    source_id: int = 0
    source_name: str = ""
    play_mode_id: int = 1
    play_mode: str = "Stereo"
    eq_treble: int = 0
    eq_mid: int = 0
    eq_bass: int = 0
    eq_range: int = 10
    input_map: dict[int, tuple[int, int]] = field(default_factory=dict)
    menu_values: dict[int, int] = field(default_factory=dict)
    active_preset: int = 0
    configured_presets: list[int] = field(default_factory=list)


class CantonDevice(PersistentConnectionDevice):
    """Canton Smart Sound device: LUCI + tunnel connection with push updates."""

    def __init__(
        self,
        device_config: DeviceConfig,
        loop: AbstractEventLoop | None = None,
        config_manager: BaseConfigManager | None = None,
        driver=None,
    ) -> None:
        """
        Create the device.

        :param device_config: Configuration for this device
        :param loop: Event loop for async operations
        :param config_manager: Configuration manager instance
        :param driver: Integration driver reference
        """
        super().__init__(
            device_config=device_config,
            loop=loop,
            backoff_max=RECONNECT_BACKOFF_MAX,
            config_manager=config_manager,
            driver=driver,
        )
        self._device_config: DeviceConfig = device_config
        self._luci = LuciProtocol(device_config.address, device_config.port)
        self._tunnel: TunnelManager | None = None
        self.data = CantonState()
        self._connection_lost = asyncio.Event()

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def identifier(self) -> str:
        """Return the device identifier."""
        return self._device_config.identifier

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._device_config.name

    @property
    def address(self) -> str | None:
        """Return the device address."""
        return self._device_config.address

    @property
    def log_id(self) -> str:
        """Return a log identifier."""
        return self.name if self.name else self.identifier

    @property
    def config(self) -> DeviceConfig:
        """Return the device configuration."""
        return self._device_config

    @property
    def is_connected(self) -> bool:
        """Return True if the device is usable.

        In shared mode no permanent tunnel exists, so the device counts as connected
        while LUCI is up and the last tunnel session succeeded.
        """
        return (
            self._luci.is_connected
            and self._tunnel is not None
            and self._tunnel.available
        )

    @property
    def tunnel_mode(self) -> TunnelMode:
        """Return whether the tunnel is held exclusively or shared."""
        return self._tunnel.mode if self._tunnel else TunnelMode.EXCLUSIVE

    @property
    def state(self) -> media_player.States:
        """Return the current media player state."""
        if not self.is_connected:
            return media_player.States.UNAVAILABLE
        if not self.data.power_on:
            return media_player.States.OFF
        if self.data.input_name in NETWORK_SOURCES:
            status = self.data.play_status
            if status == PLAY_STATUS_PLAYING:
                return media_player.States.PLAYING
            if status == PLAY_STATUS_PAUSED:
                return media_player.States.PAUSED
            if status in (
                PLAY_STATUS_CONNECTING,
                PLAY_STATUS_RECEIVING,
                PLAY_STATUS_BUFFERING,
            ):
                return media_player.States.BUFFERING
        return media_player.States.ON

    @property
    def volume_percent(self) -> int:
        """Return the volume scaled to the 0..100 range used by the Remote."""
        if self.data.volume_max <= 0:
            return 0
        return round(self.data.volume / self.data.volume_max * 100)

    @property
    def source_list(self) -> list[str]:
        """
        Return inputs or presets, depending on the configured source list mode.

        The input list follows the device: every physical input carries a name assigned
        under "System Setup -> Input Setup -> Input Name", reported by SOURCE_INFO.
        Inputs left unnamed ("---") are skipped. Falls back to all selectable names
        while the mapping is unknown.
        """
        if self._device_config.source_list_mode == SOURCE_MODE_PRESETS:
            presets = self.data.configured_presets or list(range(1, PRESET_COUNT + 1))
            return [f"Preset {i}" for i in presets]

        names = [
            TUNNEL_INPUT_NAMES[name_id]
            # Sort by physical source so the order matches the device
            for name_id, (source_id, _) in sorted(
                self.data.input_map.items(), key=lambda item: item[1][0]
            )
            if name_id != INPUT_NAME_UNASSIGNED and name_id in TUNNEL_INPUT_NAMES
        ]
        return names or SELECTABLE_INPUT_NAMES

    @property
    def source(self) -> str | None:
        """Return the current input or preset, depending on the source list mode."""
        if self._device_config.source_list_mode == SOURCE_MODE_PRESETS:
            preset = self.data.active_preset
            return f"Preset {preset}" if preset > 0 else None
        return self.data.input_name or None

    def get_media_player_attributes(self, device_id: str) -> MediaPlayerAttributes | None:
        """
        Return the media player attributes for the given device.

        :param device_id: Device identifier
        :return: Current attributes, or None for an unknown device
        """
        if device_id != self.identifier:
            return None

        is_network = self.data.input_name in NETWORK_SOURCES
        return MediaPlayerAttributes(
            STATE=self.state,
            VOLUME=self.volume_percent,
            MUTED=self.data.is_muted,
            SOURCE=self.source or "",
            SOURCE_LIST=self.source_list,
            SOUND_MODE=self.data.play_mode or None,
            SOUND_MODE_LIST=list(dict.fromkeys(TUNNEL_PLAY_MODES.values())),
            MEDIA_TITLE=self.data.media_title if is_network else "",
            MEDIA_ARTIST=self.data.media_artist if is_network else "",
            MEDIA_ALBUM=self.data.media_album if is_network else "",
            MEDIA_IMAGE_URL=self.data.media_image_url if is_network else "",
            MEDIA_TYPE=(
                media_player.MediaContentType.MUSIC
                if is_network and self.data.media_title
                else ""
            ),
            MEDIA_DURATION=(
                round(self.data.media_duration / 1000)
                if is_network and self.data.media_duration
                else 0
            ),
            MEDIA_POSITION=(
                round(self.data.media_position / 1000)
                if is_network and self.data.media_position
                else 0
            ),
            MEDIA_POSITION_UPDATED_AT=(
                datetime.now(timezone.utc).isoformat()
                if is_network and self.data.media_position is not None
                else None
            ),
            REPEAT=(
                {
                    0: media_player.RepeatMode.OFF,
                    1: media_player.RepeatMode.ONE,
                    2: media_player.RepeatMode.ALL,
                }.get(self.data.repeat, media_player.RepeatMode.OFF)
                if is_network
                else media_player.RepeatMode.OFF
            ),
            SHUFFLE=self.data.shuffle if is_network else False,
        )

    # =========================================================================
    # Connection management
    # =========================================================================

    async def establish_connection(self) -> Any:
        """
        Connect to the device: LUCI first, then the tunnel, then read the initial state.

        LUCI stays connected permanently. Tunnel access goes through the
        ``TunnelManager``, which holds the tunnel exclusively while it is free and
        falls back to short shared sessions when another controller takes it.

        :return: The LUCI connection
        :raises ConnectionError: If the device cannot be reached
        """
        self._connection_lost.clear()
        self._luci.on_message = self._on_luci_message
        self._luci.on_connection_change = self._on_luci_connection_change

        if not await self._luci.async_connect():
            raise ConnectionError(
                f"Cannot connect to Canton device at {self.address}:{self._device_config.port}"
            )

        self._tunnel = TunnelManager(
            host=self._device_config.address,
            port=self._device_config.tunnel_port,
            luci=self._luci,
            on_message=self._on_tunnel_message,
            poll=self._poll_state,
            log_id=self.log_id,
            loop=self._loop,
        )
        await self._tunnel.start()

        await self._fetch_initial_state()
        if not self._tunnel.available:
            await self._tunnel.stop()
            await self._luci.async_disconnect()
            raise ConnectionError(
                f"Cannot reach Canton tunnel at "
                f"{self.address}:{self._device_config.tunnel_port}"
            )

        return self._luci

    async def maintain_connection(self) -> None:
        """Push the initial state and block until the LUCI connection is lost."""
        self.push_update()
        await self._connection_lost.wait()
        await self.close_connection()
        raise ConnectionError(f"Connection to {self.log_id} lost")

    async def close_connection(self) -> None:
        """Close the tunnel and LUCI connections."""
        if self._tunnel:
            await self._tunnel.stop()
            self._tunnel = None
        await self._luci.async_disconnect()

    def _on_luci_connection_change(self, connected: bool) -> None:
        """Handle LUCI connection loss — this is a real device disconnect."""
        if not connected:
            self._loop.call_soon_threadsafe(self._connection_lost.set)

    @asynccontextmanager
    async def _tunnel_session(self) -> AsyncIterator[TunnelProtocol | None]:
        """Provide a connected tunnel, or None if there is none."""
        if self._tunnel is None:
            yield None
            return
        async with self._tunnel.session() as session:
            yield session

    async def _poll_state(self) -> None:
        """Read the volatile part of the device state in one short session."""
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            resp = await tunnel.async_send(*TCMD_STANDBY_GET)
            if resp is not None and len(resp) >= 1:
                self.data.power_on = resp[0] == 1
            resp = await tunnel.async_send(*TCMD_SOURCE_GET)
            if resp is not None and len(resp) >= 3:
                self._parse_source(resp)
            resp = await tunnel.async_send(*TCMD_VOLUME_GET)
            if resp is not None and len(resp) >= 1:
                self._parse_volume(resp)
            resp = await tunnel.async_send(*TCMD_MUTE_GET)
            if resp is not None and len(resp) >= 1:
                self.data.is_muted = resp[0] == 1
            resp = await tunnel.async_send(*TCMD_EQ_GET)
            if resp is not None and len(resp) >= 3:
                self._parse_eq(resp)
        self.push_update()

    # =========================================================================
    # State handling
    # =========================================================================

    async def _fetch_initial_state(self) -> None:
        """Query the full device state, including all menu settings, in one session."""
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return

            # Standby state (1=on, 0=standby)
            resp = await tunnel.async_send(*TCMD_STANDBY_GET)
            if resp is not None and len(resp) >= 1:
                self.data.power_on = resp[0] == 1

            # Input mapping (SOURCE_INFO)
            resp = await tunnel.async_send(*TCMD_SOURCE_INFO_GET)
            if resp is not None and len(resp) >= 3:
                for i in range(0, len(resp) - 2, 3):
                    src_id, name_id, mode_id = resp[i], resp[i + 1], resp[i + 2]
                    self.data.input_map[name_id] = (src_id, mode_id)

            # Presets
            resp = await tunnel.async_send(*TCMD_PRESET_GET)
            if resp is not None and len(resp) >= PRESET_COUNT + 1:
                self.data.active_preset = resp[0]
                self.data.configured_presets = [
                    i + 1 for i in range(PRESET_COUNT) if resp[i + 1] == 2
                ]

            # Source / play mode
            resp = await tunnel.async_send(*TCMD_SOURCE_GET)
            if resp is not None and len(resp) >= 3:
                self._parse_source(resp)

            # EQ
            resp = await tunnel.async_send(*TCMD_EQ_GET)
            if resp is not None and len(resp) >= 3:
                self._parse_eq(resp)

            # Volume
            resp = await tunnel.async_send(*TCMD_VOLUME_GET)
            if resp is not None and len(resp) >= 1:
                self._parse_volume(resp)

            # Mute
            resp = await tunnel.async_send(*TCMD_MUTE_GET)
            if resp is not None and len(resp) >= 1:
                self.data.is_muted = resp[0] == 1

            # All menu settings supported by this model
            for name in MENU_IDS_BY_MODEL:
                menu_id = self.menu_id(name)
                if menu_id is None:
                    continue
                value = await self._async_menu_get_by_id(tunnel, menu_id)
                if value is not None:
                    self.data.menu_values[menu_id] = value

    def _parse_source(self, payload: bytes) -> None:
        """Parse SOURCE_PLAY_MODE response: [sourceId, nameId, playModeId]."""
        self.data.source_id = payload[0]
        self.data.input_name_id = payload[1]
        self.data.play_mode_id = payload[2]
        self.data.source_name = TUNNEL_PHYSICAL_SOURCES.get(
            payload[0], f"Source {payload[0]}"
        )
        self.data.input_name = TUNNEL_INPUT_NAMES.get(payload[1], f"Input {payload[1]}")
        self.data.play_mode = TUNNEL_PLAY_MODES.get(payload[2], f"Mode {payload[2]}")

        # NET/BT use LUCI volume (0-100), other inputs use tunnel volume (0-70)
        if self.data.input_name in NETWORK_SOURCES:
            self.data.volume_max = 100
            self._loop.create_task(self._refresh_luci_state())
        else:
            self.data.volume_max = 70
            self.data.play_status = None
            self.data.media_title = None
            self.data.media_artist = None
            self.data.media_album = None
            self.data.media_image_url = None
            self.data.media_duration = None
            self.data.media_position = None
            self.data.shuffle = False
            self.data.repeat = 0

    async def _refresh_luci_state(self) -> None:
        """Refresh volume, play status and metadata from LUCI (NET/BT sources)."""
        resp = await self._luci.async_send(MID_VOLUME, CMD_GET)
        if resp and resp.payload:
            try:
                self.data.volume = int(resp.payload)
            except ValueError:
                pass

        resp = await self._luci.async_send(MID_GET_UI, CMD_GET)
        if resp and resp.payload:
            self._parse_media_metadata(resp.payload)

        self.push_update()

    def _parse_eq(self, payload: bytes) -> None:
        """Parse EQ response: [treble, mid, bass, range]."""
        self.data.eq_treble = payload[0] if payload[0] < 128 else payload[0] - 256
        self.data.eq_mid = payload[1] if payload[1] < 128 else payload[1] - 256
        self.data.eq_bass = payload[2] if payload[2] < 128 else payload[2] - 256
        if len(payload) >= 4:
            self.data.eq_range = payload[3]

    def _parse_volume(self, payload: bytes) -> None:
        """Parse VOLUME response: [volume, max_volume]."""
        self.data.volume = payload[0]
        if len(payload) >= 2:
            self.data.volume_max = payload[1]

    def _on_luci_message(self, msg: LuciMessage) -> None:
        """Handle LUCI push messages (volume/playback/metadata for NET/BT)."""
        changed = False

        if msg.mid == MID_VOLUME and self._is_network_source():
            try:
                self.data.volume = int(msg.payload)
                changed = True
            except ValueError:
                pass
        elif msg.mid == MID_CURRENT_PLAY_STATUS:
            try:
                self.data.play_status = int(msg.payload)
                changed = True
            except ValueError:
                pass
        elif msg.mid == MID_PLAY_ELAPSED:
            try:
                elapsed = int(msg.payload)
                if elapsed >= 0:
                    self.data.media_position = elapsed
                    changed = True
            except ValueError:
                pass
        elif msg.mid == MID_GET_UI and msg.payload:
            self._parse_media_metadata(msg.payload)
            changed = True

        if changed:
            self.push_update()

    def _parse_media_metadata(self, payload: str) -> None:
        """Parse GetUI JSON for media metadata."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return

        contents = data.get("Window CONTENTS", {})
        if not contents:
            return

        title = contents.get("TrackName", "")
        self.data.media_title = title if title and title != "null" else None

        artist = contents.get("Artist", "")
        self.data.media_artist = artist if artist and artist != "null" else None

        album = contents.get("Album", "")
        self.data.media_album = album if album and album != "null" else None

        cover = contents.get("CoverArtUrl", "")
        if cover and cover != "null":
            if cover == "coverart.jpg":
                cover = f"http://{self.address}/{cover}"
            self.data.media_image_url = cover
        else:
            self.data.media_image_url = None

        play_state = contents.get("PlayState")
        if play_state is not None:
            self.data.play_status = int(play_state)

        total = contents.get("TotalTime")
        self.data.media_duration = int(total) if total and int(total) > 0 else None

        current = contents.get("Current_time")
        self.data.media_position = (
            int(current) if current and int(current) >= 0 else None
        )

        self.data.shuffle = bool(contents.get("Shuffle", 0))
        self.data.repeat = int(contents.get("Repeat", 0))

    def _on_tunnel_message(self, cmd: tuple[int, int], payload: bytes) -> None:
        """Handle incoming tunnel push messages."""
        changed = False

        if cmd == TCMD_SOURCE_SET and len(payload) >= 3:
            self._parse_source(payload)
            changed = True
        elif cmd == TCMD_EQ_SET and len(payload) >= 3:
            self._parse_eq(payload)
            changed = True
        elif cmd == TCMD_STANDBY_SET and len(payload) >= 1:
            self.data.power_on = payload[0] == 1
            changed = True
        elif cmd == TCMD_PRESET_RECALL and len(payload) >= 2:
            # Preset notification from hardware button: [preset, 1] = recall
            preset_num = payload[0]
            if payload[1] == 1 and preset_num > 0:
                self.data.active_preset = preset_num
                self._loop.create_task(self._refresh_after_preset())
            changed = True
        elif cmd == TCMD_MENU_SET and len(payload) >= 5:
            menu_id = int.from_bytes(payload[:4], "big")
            value = payload[4] if payload[4] < 128 else payload[4] - 256
            self.data.menu_values[menu_id] = value
            changed = True
        elif cmd == TCMD_MUTE_SET and len(payload) >= 1:
            self.data.is_muted = payload[0] == 1
            changed = True
        elif cmd == TCMD_VOLUME_SET and len(payload) >= 1:
            self._parse_volume(payload)
            changed = True

        if changed:
            self.push_update()

    async def _refresh_after_preset(self) -> None:
        """Re-read source, EQ and volume after the device applied a preset."""
        await asyncio.sleep(1)
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            resp = await tunnel.async_send(*TCMD_SOURCE_GET)
            if resp and len(resp) >= 3:
                self._parse_source(resp)
            resp = await tunnel.async_send(*TCMD_EQ_GET)
            if resp and len(resp) >= 3:
                self._parse_eq(resp)
            resp = await tunnel.async_send(*TCMD_VOLUME_GET)
            if resp and len(resp) >= 1:
                self._parse_volume(resp)

        self.push_update()

    def _is_network_source(self) -> bool:
        """Return True if the current input is served by LUCI (NET/BT)."""
        return self.data.input_name in NETWORK_SOURCES

    # =========================================================================
    # Menu settings
    # =========================================================================

    def menu_id(self, name: str) -> int | None:
        """
        Return the menu ID for a setting on this device model.

        :param name: Setting name (MENU_* constant)
        :return: Menu ID, or None if the model does not support the setting
        """
        return MENU_IDS_BY_MODEL.get(name, {}).get(self._device_config.model)

    def menu_value(self, name: str) -> int | None:
        """
        Return the cached value of a menu setting.

        :param name: Setting name (MENU_* constant)
        :return: Current value, or None if unknown/unsupported
        """
        menu_id = self.menu_id(name)
        if menu_id is None:
            return None
        return self.data.menu_values.get(menu_id)

    @staticmethod
    async def _async_menu_get_by_id(
        tunnel: TunnelProtocol, menu_id: int
    ) -> int | None:
        """
        Get a menu value by raw ID on an open tunnel session.

        :param tunnel: Connected tunnel
        :param menu_id: Raw menu ID
        :return: Current value, or None if the device did not answer
        """
        resp = await tunnel.async_send(*TCMD_MENU_GET, menu_id.to_bytes(4, "big"))
        if resp and len(resp) >= 5:
            value = resp[4]
            return value if value < 128 else value - 256
        return None

    async def menu_set(self, name: str, value: int) -> None:
        """
        Set a menu value and close the OSD menu again.

        The device auto-navigates into the menu hierarchy when MENU_SET is sent, so one
        EXIT only goes up one level — one EXIT per nesting level plus one is needed to
        fully close the OSD. The device needs ~200 ms between OSD commands.

        :param name: Setting name (MENU_* constant)
        :param value: New value
        """
        menu_id = self.menu_id(name)
        if menu_id is None:
            _LOG.debug("[%s] Menu setting %s not supported", self.log_id, name)
            return

        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(
                *TCMD_MENU_SET, menu_id.to_bytes(4, "big") + bytes([value & 0xFF])
            )
            for _ in range(MENU_EXIT_COUNT.get(name, MENU_EXIT_COUNT_DEFAULT)):
                await asyncio.sleep(0.2)
                await tunnel.async_send_fire(*TCMD_MENU_EXIT)
            # Short sessions get no push notification — remember what we set
            self.data.menu_values[menu_id] = value

        self.push_update()

    async def menu_step(self, name: str, direction: int) -> None:
        """
        Step a numeric menu setting up or down, clamped to its range.

        :param name: Setting name (MENU_* constant)
        :param direction: +1 to increase, -1 to decrease
        """
        if name not in MENU_RANGES:
            return
        minimum, maximum, step = MENU_RANGES[name]
        current = self.menu_value(name)
        if current is None:
            current = minimum
        value = max(minimum, min(maximum, current + direction * step))
        await self.menu_set(name, value)

    # =========================================================================
    # Commands
    # =========================================================================

    async def play_control(self, command: str) -> None:
        """
        Send a playback control command via LUCI (NET/BT sources).

        :param command: LUCI play control payload, e.g. "PAUSE" or "SEEK:12000"
        """
        await self._luci.async_send_fire(MID_PLAY_CONTROL, CMD_SET, command)

    async def set_volume(self, volume: int) -> None:
        """
        Set the volume in device units (0..volume_max).

        :param volume: Volume in device units
        """
        volume = max(0, min(self.data.volume_max, volume))
        if self._is_network_source():
            await self._luci.async_send_fire(MID_VOLUME, CMD_SET, str(volume))
            self.data.volume = volume
        else:
            async with self._tunnel_session() as tunnel:
                if tunnel is None:
                    return
                await tunnel.async_send_fire(*TCMD_VOLUME_SET, bytes([volume]))
                self.data.volume = volume
        self.push_update()

    async def set_volume_percent(self, percent: int) -> None:
        """
        Set the volume from the Remote's 0..100 range.

        :param percent: Volume percentage
        """
        percent = max(0, min(100, percent))
        await self.set_volume(round(percent / 100 * self.data.volume_max))

    async def volume_step(self, direction: int) -> None:
        """
        Change the volume by one device step.

        :param direction: +1 for louder, -1 for quieter
        """
        await self.set_volume(self.data.volume + direction)

    async def set_mute(self, mute: bool) -> None:
        """
        Mute or unmute the device.

        :param mute: True to mute
        """
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(*TCMD_MUTE_SET, bytes([1 if mute else 0]))
            self.data.is_muted = mute
        self.push_update()

    async def set_power(self, on: bool) -> None:
        """
        Switch the device on or to standby.

        :param on: True to switch on
        """
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(*TCMD_STANDBY_SET, bytes([1 if on else 0]))
            self.data.power_on = on
        self.push_update()

    async def set_input(self, input_name: str) -> None:
        """
        Select an input by name.

        :param input_name: Input name, e.g. "TV", "CD", "NET"
        """
        name_id = TUNNEL_INPUT_NAMES_REVERSE.get(input_name)
        if name_id is None:
            return

        mapping = self.data.input_map.get(name_id)
        if not mapping:
            _LOG.warning(
                "[%s] No source mapping for input %s (nameId=%s)",
                self.log_id,
                input_name,
                name_id,
            )
            return

        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(
                *TCMD_SOURCE_SET, bytes([mapping[0], name_id, self.data.play_mode_id])
            )
            await self._read_source(tunnel)
        self.push_update()

    async def _read_source(self, tunnel: TunnelProtocol) -> None:
        """Re-read source and volume after a change, for sessions without push."""
        await asyncio.sleep(0.3)
        resp = await tunnel.async_send(*TCMD_SOURCE_GET)
        if resp and len(resp) >= 3:
            self._parse_source(resp)
        resp = await tunnel.async_send(*TCMD_VOLUME_GET)
        if resp and len(resp) >= 1:
            self._parse_volume(resp)

    async def set_play_mode(self, mode: str) -> None:
        """
        Select a sound mode.

        :param mode: Play mode name, e.g. "Stereo", "Movie"
        """
        mode_id = TUNNEL_PLAY_MODES_REVERSE.get(mode)
        if mode_id is None:
            return
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(
                *TCMD_SOURCE_SET,
                bytes([self.data.source_id, self.data.input_name_id, mode_id]),
            )
            await self._read_source(tunnel)
        self.push_update()

    async def recall_preset(self, preset: int) -> None:
        """
        Recall a device preset.

        :param preset: Preset number (1..10)
        """
        if not 1 <= preset <= PRESET_COUNT:
            return
        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(*TCMD_PRESET_RECALL, bytes([preset, 1]))
        self.data.active_preset = preset
        self.push_update()
        self._loop.create_task(self._refresh_after_preset())

    async def set_eq(
        self,
        treble: int | None = None,
        mid: int | None = None,
        bass: int | None = None,
    ) -> None:
        """
        Set the EQ bands (-10..+10 dB). Omitted bands keep their current value.

        :param treble: Treble value
        :param mid: Mid value
        :param bass: Bass value
        """
        def clamp(value: int) -> int:
            return max(EQ_MIN, min(EQ_MAX, value))

        treble_value = clamp(treble if treble is not None else self.data.eq_treble)
        mid_value = clamp(mid if mid is not None else self.data.eq_mid)
        bass_value = clamp(bass if bass is not None else self.data.eq_bass)

        async with self._tunnel_session() as tunnel:
            if tunnel is None:
                return
            await tunnel.async_send_fire(
                *TCMD_EQ_SET,
                bytes(
                    [
                        treble_value & 0xFF,
                        mid_value & 0xFF,
                        bass_value & 0xFF,
                        self.data.eq_range,
                    ]
                ),
            )
            self.data.eq_treble = treble_value
            self.data.eq_mid = mid_value
            self.data.eq_bass = bass_value
        self.push_update()

    async def eq_step(self, band: str, delta: int) -> None:
        """
        Change one EQ band by the given delta, clamped to the -10..+10 dB range.

        :param band: "bass", "mid" or "treble"
        :param delta: Change in dB
        """
        current = {
            "bass": self.data.eq_bass,
            "mid": self.data.eq_mid,
            "treble": self.data.eq_treble,
        }.get(band)
        if current is None:
            return
        await self.set_eq(**{band: current + delta})

    async def eq_reset(self) -> None:
        """Reset all EQ bands to 0 dB."""
        await self.set_eq(treble=0, mid=0, bass=0)

    async def bluetooth_pair(self) -> None:
        """Put the device into Bluetooth pairing mode."""
        async with self._tunnel_session() as tunnel:
            if tunnel is not None:
                await tunnel.async_send_fire(*TCMD_BT_PAIR)
