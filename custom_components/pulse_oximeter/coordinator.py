"""Pulse Oximeter BLE data coordinator."""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    BATTERY_LEVEL_UUID,
    DOMAIN,
    FIRMWARE_REV_UUID,
    MANUFACTURER_NAME_UUID,
    MODEL_NUMBER_UUID,
    PLX_CONTINUOUS_UUID,
    PLX_SPOT_CHECK_UUID,
)

_LOGGER = logging.getLogger(__name__)

DISCONNECT_DELAY = 3.0
RECONNECT_INTERVAL = 30.0


@dataclass
class OxiData:
    """Parsed pulse oximeter data."""

    spo2: float | None = None
    pulse_rate: float | None = None
    perfusion_index: float | None = None
    battery: int | None = None
    connected: bool = False
    last_measured: datetime | None = None
    manufacturer: str | None = None
    model: str | None = None
    firmware: str | None = None


def sfloat_to_float(raw: int) -> float | None:
    """Convert Bluetooth SFLOAT (16-bit) to Python float.

    SFLOAT: 4-bit signed exponent + 12-bit signed mantissa.
    """
    if raw in (0x07FF, 0x0800, 0x07FE, 0x0802):
        return None

    exponent = raw >> 12
    mantissa = raw & 0x0FFF

    if exponent >= 8:
        exponent -= 16
    if mantissa >= 0x0800:
        mantissa -= 0x1000

    return mantissa * (10.0**exponent)


def parse_plx_continuous(data: bytes) -> dict | None:
    """Parse PLX Continuous Measurement characteristic."""
    if len(data) < 5:
        return None

    flags = data[0]
    spo2 = sfloat_to_float(struct.unpack_from("<H", data, 1)[0])
    pr = sfloat_to_float(struct.unpack_from("<H", data, 3)[0])

    result: dict = {"spo2": spo2, "pulse_rate": pr}
    offset = 5

    # SpO2PR-Fast
    if flags & 0x01 and offset + 4 <= len(data):
        offset += 4

    # SpO2PR-Slow
    if flags & 0x02 and offset + 4 <= len(data):
        offset += 4

    # Measurement Status
    if flags & 0x04 and offset + 2 <= len(data):
        offset += 2

    # Device and Sensor Status
    if flags & 0x08 and offset + 3 <= len(data):
        offset += 3

    # Pulse Amplitude Index (PI)
    if flags & 0x10 and offset + 2 <= len(data):
        result["pi"] = sfloat_to_float(struct.unpack_from("<H", data, offset)[0])

    return result


def parse_plx_spot_check(data: bytes) -> dict | None:
    """Parse PLX Spot-Check Measurement characteristic."""
    if len(data) < 5:
        return None

    flags = data[0]
    spo2 = sfloat_to_float(struct.unpack_from("<H", data, 1)[0])
    pr = sfloat_to_float(struct.unpack_from("<H", data, 3)[0])

    result: dict = {"spo2": spo2, "pulse_rate": pr}
    offset = 5

    # Timestamp
    if flags & 0x01 and offset + 7 <= len(data):
        offset += 7

    # Measurement Status
    if flags & 0x02 and offset + 2 <= len(data):
        offset += 2

    # Device and Sensor Status
    if flags & 0x04 and offset + 3 <= len(data):
        offset += 3

    # Pulse Amplitude Index (PI)
    if flags & 0x08 and offset + 2 <= len(data):
        result["pi"] = sfloat_to_float(struct.unpack_from("<H", data, offset)[0])

    return result


class OxiCoordinator(DataUpdateCoordinator[OxiData]):
    """Coordinator for pulse oximeter BLE device."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{address}",
        )
        self.address = address
        self.device_name = name
        self.data = OxiData()
        self._client: BleakClient | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._expected_disconnect = False

    async def async_start(self) -> None:
        """Start the coordinator - connect to device."""
        await self._async_connect()

    async def async_stop(self) -> None:
        """Stop the coordinator - disconnect from device."""
        self._expected_disconnect = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._async_disconnect()

    async def _async_connect(self) -> None:
        """Connect to the BLE device and subscribe to notifications."""
        try:
            ble_device = async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not ble_device:
                _LOGGER.debug("Device %s not available", self.address)
                self._schedule_reconnect()
                return

            self._client = await establish_connection(
                BleakClient, ble_device, self.address
            )
            self._client.set_disconnected_callback(self._on_disconnect)

            _LOGGER.info("Connected to %s", self.address)
            self.data.connected = True

            # Read device info
            await self._async_read_device_info()

            # Read battery
            await self._async_read_battery()

            # Subscribe to PLX notifications
            await self._async_subscribe()

            self.async_set_updated_data(self.data)

        except (BleakError, TimeoutError, OSError) as err:
            _LOGGER.warning("Failed to connect to %s: %s", self.address, err)
            self.data.connected = False
            self.async_set_updated_data(self.data)
            self._schedule_reconnect()

    async def _async_disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except BleakError:
                pass
        self._client = None

    @callback
    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected disconnection."""
        _LOGGER.info("Disconnected from %s", self.address)
        self._client = None
        self.data.connected = False
        self.async_set_updated_data(self.data)

        if not self._expected_disconnect:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _reconnect() -> None:
            await asyncio.sleep(RECONNECT_INTERVAL)
            await self._async_connect()

        self._reconnect_task = self.hass.async_create_task(_reconnect())

    async def _async_read_device_info(self) -> None:
        """Read device information characteristics."""
        if not self._client or not self._client.is_connected:
            return
        try:
            raw = await self._client.read_gatt_char(MANUFACTURER_NAME_UUID)
            self.data.manufacturer = raw.decode("utf-8").strip()
        except (BleakError, UnicodeDecodeError, Exception):
            pass
        try:
            raw = await self._client.read_gatt_char(MODEL_NUMBER_UUID)
            self.data.model = raw.decode("utf-8").strip()
        except (BleakError, UnicodeDecodeError, Exception):
            pass
        try:
            raw = await self._client.read_gatt_char(FIRMWARE_REV_UUID)
            self.data.firmware = raw.decode("utf-8").strip()
        except (BleakError, UnicodeDecodeError, Exception):
            pass

    async def _async_read_battery(self) -> None:
        """Read battery level."""
        if not self._client or not self._client.is_connected:
            return
        try:
            raw = await self._client.read_gatt_char(BATTERY_LEVEL_UUID)
            self.data.battery = raw[0]
        except (BleakError, Exception):
            pass

    async def _async_subscribe(self) -> None:
        """Subscribe to PLX continuous and spot-check notifications."""
        if not self._client or not self._client.is_connected:
            return

        for service in self._client.services:
            for char in service.characteristics:
                uuid = char.uuid.lower()
                if uuid == PLX_CONTINUOUS_UUID:
                    if "notify" in char.properties:
                        await self._client.start_notify(
                            PLX_CONTINUOUS_UUID, self._on_continuous
                        )
                        _LOGGER.debug("Subscribed to PLX Continuous")
                if uuid == PLX_SPOT_CHECK_UUID:
                    if "indicate" in char.properties or "notify" in char.properties:
                        await self._client.start_notify(
                            PLX_SPOT_CHECK_UUID, self._on_spot_check
                        )
                        _LOGGER.debug("Subscribed to PLX Spot-Check")

    @callback
    def _on_continuous(self, sender: int, data: bytearray) -> None:
        """Handle PLX Continuous Measurement notification."""
        parsed = parse_plx_continuous(bytes(data))
        if not parsed:
            return
        self._update_from_measurement(parsed)

    @callback
    def _on_spot_check(self, sender: int, data: bytearray) -> None:
        """Handle PLX Spot-Check Measurement indication."""
        parsed = parse_plx_spot_check(bytes(data))
        if not parsed:
            return
        self._update_from_measurement(parsed)

    @callback
    def _update_from_measurement(self, parsed: dict) -> None:
        """Update data from a parsed measurement, ignoring zero values."""
        spo2 = parsed.get("spo2")
        pr = parsed.get("pulse_rate")
        pi = parsed.get("pi")

        # Filter out zero readings (finger not placed properly)
        if spo2 is not None and spo2 > 0:
            self.data.spo2 = spo2
        if pr is not None and pr > 0:
            self.data.pulse_rate = pr
        if pi is not None and pi > 0:
            self.data.perfusion_index = pi

        # Update timestamp if we got any valid reading
        if (spo2 is not None and spo2 > 0) or (pr is not None and pr > 0):
            self.data.last_measured = datetime.now(timezone.utc)

        self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> OxiData:
        """Fallback polling - not used for push-based data."""
        return self.data
