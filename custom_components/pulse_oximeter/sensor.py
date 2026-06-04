"""Sensor platform for Pulse Oximeter (BLE) integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .assignment import AssignmentManager
from .const import CONF_PARTICIPANTS, DOMAIN, SIGNAL_ASSIGNMENT_UPDATE
from .coordinator import OxiCoordinator, OxiData
from .data import RuntimeData, person_display_name


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


PERSON_SENSOR_DESCRIPTIONS = [
    description
    for description in SENSOR_DESCRIPTIONS
    if description.key in ("spo2", "pulse_rate", "perfusion_index")
]

PERSON_ATTR_KEYS = (
    "measured_at",
    "duration",
    "spo2_min",
    "spo2_max",
    "pulse_min",
    "pulse_max",
    "readings",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up pulse oximeter sensors from a config entry."""
    data: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.coordinator

    entities: list[SensorEntity] = [
        OxiSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    for person_entity_id in entry.options.get(CONF_PARTICIPANTS, []):
        person_name = person_display_name(hass, person_entity_id)
        entities.extend(
            PersonOxiSensor(
                manager=data.manager,
                entry=entry,
                person_entity_id=person_entity_id,
                person_name=person_name,
                device_name=coordinator.device_name,
                coordinator_address=coordinator.address,
                description=description,
            )
            for description in PERSON_SENSOR_DESCRIPTIONS
        )

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


class PersonOxiSensor(RestoreSensor):
    """Last claimed measurement value for one person."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        manager: AssignmentManager,
        entry: ConfigEntry,
        person_entity_id: str,
        person_name: str,
        device_name: str,
        coordinator_address: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the person sensor."""
        self.entity_description = description
        self._manager = manager
        self._entry = entry
        self._person_entity_id = person_entity_id
        self._person_name = person_name
        self._device_name = device_name
        self._coordinator_address = coordinator_address
        person_slug = slugify(person_entity_id.split(".", 1)[1])
        self._attr_unique_id = f"{entry.unique_id}_{person_slug}_{description.key}"
        self._person_slug = person_slug
        self._restored_value: float | None = None
        self._restored_attrs: dict = {}

    @property
    def device_info(self) -> DeviceInfo:
        """One virtual device per person, linked to the oximeter."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{self._person_slug}")},
            name=f"{self._device_name} {self._person_name}",
            via_device=(DOMAIN, self._coordinator_address),
        )

    async def async_added_to_hass(self) -> None:
        """Restore last values and subscribe to assignment updates."""
        await super().async_added_to_hass()
        if (data := await self.async_get_last_sensor_data()) is not None:
            self._restored_value = data.native_value
        if (state := await self.async_get_last_state()) is not None:
            self._restored_attrs = {
                key: value
                for key, value in state.attributes.items()
                if key in PERSON_ATTR_KEYS
            }
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ASSIGNMENT_UPDATE.format(entry_id=self._entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Claimed value, falling back to the restored value."""
        values = self._manager.person_values.get(self._person_entity_id)
        if values is not None:
            return getattr(values, self.entity_description.key)
        return self._restored_value

    @property
    def available(self) -> bool:
        """Available once the person ever claimed a measurement."""
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict | None:
        """Measurement details of the last claimed measurement."""
        values = self._manager.person_values.get(self._person_entity_id)
        if values is not None:
            return values.attributes
        return self._restored_attrs or None
