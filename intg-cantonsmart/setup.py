"""
Setup flow for the Canton Smart integration.

Devices are found via LSSDP or entered manually. After the device is identified, an options
screen asks for the device model (menu IDs differ per model), the source list mode and
whether the optional settings entities should be created.

The configuration-mode screens of ``BaseSetupFlow`` are deliberately not overridden — they
provide the backup/restore actions the Integration Manager drives remotely.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from ucapi import (
    IntegrationSetupError,
    RequestUserInput,
    SetupError,
    UserDataResponse,
)
from ucapi_framework import BaseSetupFlow, DiscoveredDevice

from const import (
    DEFAULT_LUCI_PORT,
    DEFAULT_TUNNEL_PORT,
    DeviceConfig,
    MODEL_AMP_51,
    MODEL_CONNECT_51,
    MODEL_SOUNDBAR_9,
    MODEL_SOUNDBAR_10,
    MODEL_SOUNDBOX_3,
    MODEL_SOUNDDECK_100,
    SOURCE_MODE_INPUTS,
    SOURCE_MODE_PRESETS,
)
from protocol import discover_devices, validate_connection

_LOG = logging.getLogger(__name__)

KNOWN_MODELS = [
    MODEL_SOUNDBAR_10,
    MODEL_SOUNDBAR_9,
    MODEL_SOUNDDECK_100,
    MODEL_SOUNDBOX_3,
    MODEL_CONNECT_51,
    MODEL_AMP_51,
]


class CantonSetupFlow(BaseSetupFlow[DeviceConfig]):
    """Setup flow for Canton Smart Sound devices."""

    def format_discovered_device_label(self, device: DiscoveredDevice) -> str:
        """
        Show name, model and address in the discovery dropdown.

        :param device: Discovered device
        :return: Label for the dropdown
        """
        model = (device.extra_data or {}).get("model") or "Canton"
        return f"{device.name} - {model} ({device.address})"

    def get_manual_entry_form(self) -> RequestUserInput:
        """
        Return the manual entry form.

        :return: Form asking for name, address and the two TCP ports
        """
        return RequestUserInput(
            {"en": "Canton device", "de": "Canton Gerät"},
            [
                {
                    "id": "info",
                    "label": {"en": "Manual setup", "de": "Manuelle Einrichtung"},
                    "field": {
                        "label": {
                            "value": {
                                "en": (
                                    "Enter the IP address of your Canton device. "
                                    "The default ports work for all known models."
                                ),
                                "de": (
                                    "IP-Adresse des Canton Geräts eingeben. "
                                    "Die Standard-Ports passen für alle bekannten Modelle."
                                ),
                            }
                        }
                    },
                },
                {
                    "id": "name",
                    "label": {"en": "Device name", "de": "Gerätename"},
                    "field": {"text": {"value": ""}},
                },
                {
                    "id": "address",
                    "label": {"en": "IP address", "de": "IP-Adresse"},
                    "field": {"text": {"value": ""}},
                },
                {
                    "id": "port",
                    "label": {"en": "LUCI port", "de": "LUCI-Port"},
                    "field": {
                        "number": {
                            "value": DEFAULT_LUCI_PORT,
                            "min": 1,
                            "max": 65535,
                            "steps": 1,
                        }
                    },
                },
                {
                    "id": "tunnel_port",
                    "label": {"en": "Tunnel port", "de": "Tunnel-Port"},
                    "field": {
                        "number": {
                            "value": DEFAULT_TUNNEL_PORT,
                            "min": 1,
                            "max": 65535,
                            "steps": 1,
                        }
                    },
                },
            ],
        )

    async def prepare_input_from_discovery(
        self, discovered: DiscoveredDevice, additional_input: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Convert a discovered device into the manual entry input format.

        :param discovered: Device selected by the user
        :param additional_input: Additional input from the discovery screen
        :return: Input values for query_device()
        """
        extra = discovered.extra_data or {}
        return {
            "identifier": discovered.identifier,
            "name": discovered.name,
            "address": discovered.address,
            "port": extra.get("port", DEFAULT_LUCI_PORT),
            "tunnel_port": extra.get("tunnel_port", DEFAULT_TUNNEL_PORT),
            "model": extra.get("model", ""),
            "fw_version": extra.get("fw_version", ""),
            "wifi_band": extra.get("wifi_band", ""),
        }

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> DeviceConfig | SetupError | RequestUserInput:
        """
        Validate the connection and build the device configuration.

        For manually entered devices an LSSDP query fills in identifier, model and
        firmware version; if the device does not answer, the IP address is used as
        identifier and the model is asked for on the options screen.

        :param input_values: User input (manual entry or mapped discovery data)
        :return: Device configuration, or a setup error
        """
        address = str(input_values.get("address", "")).strip()
        if not address:
            _LOG.warning("No address given, re-displaying manual entry form")
            return self.get_manual_entry_form()

        name = str(input_values.get("name", "")).strip()
        port = int(input_values.get("port") or DEFAULT_LUCI_PORT)
        tunnel_port = int(input_values.get("tunnel_port") or DEFAULT_TUNNEL_PORT)
        identifier = str(input_values.get("identifier", "")).strip()
        model = str(input_values.get("model", "")).strip()
        fw_version = str(input_values.get("fw_version", "")).strip()
        wifi_band = str(input_values.get("wifi_band", "")).strip()

        try:
            device_info = await validate_connection(address, port)
            if device_info is None:
                _LOG.error("Cannot connect to Canton device at %s:%s", address, port)
                return SetupError(IntegrationSetupError.CONNECTION_REFUSED)

            if not name:
                name = device_info.get("device_name") or f"Canton ({address})"

            # Fill in the missing metadata via LSSDP for manually added devices
            if not identifier or not model:
                discovered = await discover_devices(timeout=3)
                match = next((d for d in discovered if d["host"] == address), None)
                if match:
                    identifier = identifier or match["usn"]
                    model = model or match.get("model", "")
                    fw_version = fw_version or match.get("fw_version", "")
                    wifi_band = wifi_band or match.get("wifi_band", "")

            if not identifier:
                # No LSSDP answer — fall back to the address as unique identifier
                identifier = address

            return DeviceConfig(
                identifier=identifier,
                name=name,
                address=address,
                port=port,
                tunnel_port=tunnel_port,
                model=model,
                fw_version=fw_version,
                wifi_band=wifi_band,
            )

        except TimeoutError as ex:
            _LOG.error("Connection timeout to %s: %s", address, ex)
            return SetupError(IntegrationSetupError.TIMEOUT)
        except (OSError, ValueError) as ex:
            _LOG.error("Failed to connect to %s: %s", address, ex)
            return SetupError(IntegrationSetupError.CONNECTION_REFUSED)

    async def get_additional_configuration_screen(
        self, device_config: DeviceConfig, previous_input: dict[str, Any]
    ) -> RequestUserInput | None:
        """
        Ask for the model, the source list mode and the settings entities option.

        All field IDs match ``DeviceConfig`` attributes, so the framework populates the
        pending configuration automatically. Shown for new devices and when updating an
        existing one, pre-filled with the stored values.

        :param device_config: Pending device configuration
        :param previous_input: Input values of the previous screen
        :return: The options screen
        """
        existing = self.selected_config_entry
        model = device_config.model or (existing.model if existing else "")
        source_list_mode = (
            existing.source_list_mode if existing else device_config.source_list_mode
        )
        settings_entities = (
            existing.settings_entities if existing else device_config.settings_entities
        )

        model_items = [{"id": m, "label": {"en": m}} for m in KNOWN_MODELS]
        if model and model not in KNOWN_MODELS:
            model_items.insert(0, {"id": model, "label": {"en": model}})

        return RequestUserInput(
            {"en": "Device options", "de": "Geräteoptionen"},
            [
                {
                    "id": "model",
                    "label": {"en": "Device model", "de": "Gerätemodell"},
                    "field": {
                        "dropdown": {
                            "value": model or MODEL_SOUNDBAR_10,
                            "items": model_items,
                        }
                    },
                },
                {
                    "id": "source_list_mode",
                    "label": {"en": "Source list", "de": "Quellenliste"},
                    "field": {
                        "dropdown": {
                            "value": source_list_mode,
                            "items": [
                                {
                                    "id": SOURCE_MODE_INPUTS,
                                    "label": {"en": "Inputs", "de": "Eingänge"},
                                },
                                {
                                    "id": SOURCE_MODE_PRESETS,
                                    "label": {"en": "Presets", "de": "Presets"},
                                },
                            ],
                        }
                    },
                },
                {
                    "id": "settings_entities",
                    "label": {
                        "en": "Create additional settings entities",
                        "de": "Zusätzliche Einstellungs-Entities anlegen",
                    },
                    "field": {"checkbox": {"value": settings_entities}},
                },
            ],
        )

    async def handle_additional_configuration_response(
        self, msg: UserDataResponse
    ) -> SetupError | None:
        """
        Normalise the checkbox value after auto-population.

        The Remote sends checkbox values as strings, so ``settings_entities`` would end up
        as the truthy string "false" without this conversion.

        :param msg: User data response of the options screen
        :return: None to save the configuration, or a setup error
        """
        if self._pending_device_config is None:
            return SetupError(IntegrationSetupError.OTHER)

        value = msg.input_values.get("settings_entities", False)
        self._pending_device_config.settings_entities = str(value).strip().lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        return None
