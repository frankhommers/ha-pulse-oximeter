# Pulse Oximeter (BLE) for Home Assistant

Custom integration that connects to any BLE pulse oximeter implementing the [Bluetooth SIG Pulse Oximeter Service (PLX, UUID 0x1822)](https://www.bluetooth.com/specifications/specs/pulse-oximeter-service-1-0-1/) standard.

Tested with **Medisana PM 100 Connect** (ChoiceMMed MD300C208S). Should work with any PLX-compliant device.

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| SpO2 | % | Oxygen saturation |
| Pulse Rate | bpm | Heart rate |
| Perfusion Index | % | PI value (if supported by device) |
| Battery | % | Battery level |

All sensors retain their **last measured value** after the device disconnects. Extra attributes:

- `last_measured` — timestamp of the most recent valid reading
- `connected` — whether the device is currently connected

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right corner, select **Custom repositories**
3. Add `https://github.com/frankhommers/ha-pulse-oximeter` as an **Integration**
4. Search for "Pulse Oximeter" in HACS and install it
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/pulse_oximeter` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Put the pulse oximeter on your finger and turn it on
2. Home Assistant will automatically discover the device via Bluetooth
3. A notification will appear — click **Configure** and confirm

> The device must be within Bluetooth range of your HA instance (or an ESPHome Bluetooth proxy).

## How it works

The integration connects to the pulse oximeter via BLE and subscribes to real-time PLX Continuous Measurement notifications (~1 reading/second). When the device disconnects (finger removed), the sensors keep their last valid values. The integration automatically reconnects when the device becomes available again.

Zero readings (finger not properly placed) are filtered out.

## Tested devices

| Device | Manufacturer | Model | Status |
|--------|-------------|-------|--------|
| Medisana PM 100 Connect | Choicemmed | MD300C208S | Working |

If you test this with another PLX-compatible pulse oximeter, please open an issue or PR to add it to the list.

## Development tools

The repo includes standalone BLE exploration scripts (requires `bleak`):

- `scan_discover.py` — scan for pulse oximeters
- `scan_continuous.py` — continuous scan until a device is found
- `scan_all.py` — dump all nearby BLE devices
- `gatt_explore.py` — enumerate all GATT services/characteristics
- `read_live.py` — stream live SpO2/pulse/PI values to the terminal

## License

MIT
