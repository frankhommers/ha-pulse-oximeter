"""Sensor platform for Pulse Oximeter (BLE) integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OxiCoordinator, OxiData


SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(
        key="spo2",
        name="SpO2",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
    ),
    SensorEntityDescription(
        key="pulse_rate",
        name="Pulse Rate",
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="perfusion_index",
        name="Perfusion Index",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wave",
    ),
    SensorEntityDescription(
        key="battery",
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up pulse oximeter sensors from a config entry."""
    coordinator: OxiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        OxiSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class OxiSensor(CoordinatorEntity[OxiCoordinator], SensorEntity):
    """A sensor entity for pulse oximeter data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OxiCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            name=self.coordinator.device_name,
            manufacturer=data.manufacturer,
            model=data.model,
            sw_version=data.firmware,
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the sensor value - keeps last known value after disconnect."""
        data: OxiData = self.coordinator.data
        return getattr(data, self.entity_description.key, None)

    @property
    def available(self) -> bool:
        """Sensor is available if we ever got a value."""
        value = getattr(self.coordinator.data, self.entity_description.key, None)
        return value is not None

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return extra attributes."""
        data: OxiData = self.coordinator.data
        attrs = {}
        if data.last_measured:
            attrs["last_measured"] = data.last_measured.isoformat()
        attrs["connected"] = data.connected
        return attrs
