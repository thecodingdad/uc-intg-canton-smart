# Changelog

All notable changes to this project will be documented in this file.

## v1.0.0

Initial release — port of the [ha-canton](https://github.com/thecodingdad/ha-canton) Home Assistant
integration to the Unfolded Circle Remote Two/3.

- Local control over the LUCI (TCP 7777) and Tunnel (TCP 50006) protocols, no cloud required
- Automatic device discovery via LSSDP, plus manual setup with IP address
- Media player entity: power, volume, mute, input or preset selection, sound modes, transport
  controls and media metadata for NET/BT sources
- Remote entity: physical button mapping and UI pages for sources, sound modes, presets, EQ and
  device settings
- Simple commands for inputs, sound modes, presets 1-10, EQ and subwoofer steps, lip sync,
  max volume, sleep timer, standby mode, CEC, DRC, voice clarity, displays, RF settings and
  Bluetooth pairing — filtered per device model
- Optional switch, select and sensor entities for device settings
- Configuration backup and restore, compatible with the Unfolded Circle Integration Manager
- Inputs are addressed by physical source, so every input is selectable. The name assigned under
  "System Setup -> Input Setup -> Input Name" is shown in brackets, e.g. "HDMI 2 (PC)"
- Adaptive tunnel access: the device only accepts one tunnel connection, so the integration holds it
  permanently while it is free and falls back to short sessions plus polling when another controller
  (Canton app, Home Assistant) takes over
