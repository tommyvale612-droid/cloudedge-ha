"""Camera platform for CloudEdge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CloudEdgeCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CloudEdge camera platform."""
    coordinator: CloudEdgeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Handle case where coordinator.data might be None
    if not coordinator.data:
        _LOGGER.warning("No device data available yet, camera entities will be added when data is available")
        
        # Add a listener to create entities when data becomes available
        async def _handle_coordinator_update():
            if coordinator.data and not getattr(coordinator, '_cameras_added', False):
                _LOGGER.info("Device data is now available, adding camera entities")
                cameras = []
                for serial_number, device_info in coordinator.data.items():
                    # Only add camera devices
                    if device_info.get("type_id") in [1, 2, 3, 4, 5]:  # Common camera type IDs
                        camera = CloudEdgeCamera(coordinator, serial_number, device_info)
                        cameras.append(camera)
                        _LOGGER.debug("Added camera: %s", device_info.get("name"))
                if cameras:
                    async_add_entities(cameras)
                    coordinator._cameras_added = True
        
        coordinator.async_add_listener(_handle_coordinator_update)
        async_add_entities([])
        return

    cameras = []
    for serial_number, device_info in coordinator.data.items():
        # Only add camera devices
        if device_info.get("type_id") in [1, 2, 3, 4, 5]:  # Common camera type IDs
            camera = CloudEdgeCamera(coordinator, serial_number, device_info)
            cameras.append(camera)
            _LOGGER.debug("Added camera: %s", device_info.get("name"))

    async_add_entities(cameras)


class CloudEdgeCamera(CoordinatorEntity[CloudEdgeCoordinator], Camera):
    """Representation of a CloudEdge camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.ON_OFF

    def __init__(
        self,
        coordinator: CloudEdgeCoordinator,
        serial_number: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)
        
        self._serial_number = serial_number
        self._device_info = device_info
        self._attr_unique_id = f"{DOMAIN}_{serial_number}_camera"
        self._attr_name = device_info.get("name", f"Camera {serial_number}")

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._serial_number)},
            "name": self._device_info.get("name", f"Camera {self._serial_number}"),
            "manufacturer": "CloudEdge",
            "model": self._device_info.get("type", "SmartEye Camera"),
            "serial_number": self._serial_number,
            "sw_version": self._device_info.get("firmware_version"),
        }

    @property
    def available(self) -> bool:
        """Return if camera is available."""
        # Always available if coordinator has data, don't check device online status
        return self.coordinator.last_update_success and bool(self.coordinator.data)

    @property
    def is_on(self) -> bool:
        """Return true if camera is on."""
        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            return False
            
        return device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            return {}

        attributes = {
            "serial_number": self._serial_number,
            "device_type": device_data.get("type"),
            "host_key": device_data.get("host_key"),
            "last_seen": device_data.get("last_seen"),
        }

        # Add configuration parameters if available
        if config := device_data.get("configuration"):
            # Add some key configuration parameters
            for param_name, param_info in config.items():
                if param_name in [
                    "DEVICE_RESOLUTION",
                    "WIFI_STRENGTH", 
                    "BATTERY_PERCENT",
                    "MOTION_DET_ENABLE",
                    "FRONT_LIGHT_SWITCH",
                    "LED_ENABLE",
                ]:
                    attributes[param_name.lower()] = param_info.get("formatted", param_info.get("value"))

        return attributes

    async def async_turn_on(self) -> None:
        """Turn on camera."""
        # For CloudEdge cameras, "turning on" might mean enabling motion detection
        # or turning on the front light, depending on the device capabilities
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_parameter,
                self._device_info.get("name"),
                "MOTION_DET_ENABLE",
                1,
            )
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to turn on camera %s: %s", self._attr_name, e)

    async def async_turn_off(self) -> None:
        """Turn off camera."""
        # For CloudEdge cameras, "turning off" might mean disabling motion detection
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_parameter,
                self._device_info.get("name"),
                "MOTION_DET_ENABLE",
                0,
            )
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to turn off camera %s: %s", self._attr_name, e)

    def _get_device_icon_url(self) -> str | None:
        """Return the product icon URL stored in coordinator data."""
        data = (
            self.coordinator.data.get(self._serial_number)
            if self.coordinator.data
            else None
        )
        info = data or self._device_info
        url = info.get("device_icon_url")
        return url if isinstance(url, str) and url.startswith("http") else None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return still image bytes served through the HA camera proxy."""
        url = self._get_device_icon_url()
        if not url:
            _LOGGER.debug("No device_icon_url for %s", self._attr_name)
            return None

        try:
            session = async_get_clientsession(self.hass)
            async with session.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0"
                    ),
                },
            ) as resp:
                resp.raise_for_status()
                return await resp.read()
        except Exception as err:
            _LOGGER.debug("Could not fetch device icon for %s: %s", self._attr_name, err)
            return None