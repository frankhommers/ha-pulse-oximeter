#!/usr/bin/env python3
"""
Connect to the pulse oximeter and stream live SpO2/pulse/PI values.
Subscribes to both PLX Continuous (0x2A5F) and PLX Spot-Check (0x2A5E).
Also tries raw hex dump so we can see what's really going on.

Usage: python3 read_live.py [address]
"""

import asyncio
import struct
import sys
from datetime import datetime
from bleak import BleakClient, BleakScanner

# UUIDs
PLX_CONTINUOUS = "00002a5f-0000-1000-8000-00805f9b34fb"
PLX_SPOT_CHECK = "00002a5e-0000-1000-8000-00805f9b34fb"
PLX_FEATURES = "00002a60-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"


def parse_plx_continuous(data: bytes):
    """Try to parse PLX Continuous Measurement per Bluetooth SIG spec.

    Format (PLXS v1.0.1):
      Byte 0: Flags
        bit 0: SpO2PR-Fast field present
        bit 1: SpO2PR-Slow field present
        bit 2: Measurement Status field present
        bit 3: Device and Sensor Status field present
        bit 4: Pulse Amplitude Index field present
      Bytes 1-2: SpO2 (SFLOAT, %)
      Bytes 3-4: Pulse Rate (SFLOAT, bpm)
      Then optional fields based on flags.
    """
    if len(data) < 5:
        return None

    flags = data[0]
    spo2_raw = struct.unpack_from("<H", data, 1)[0]
    pr_raw = struct.unpack_from("<H", data, 3)[0]

    spo2 = sfloat_to_float(spo2_raw)
    pr = sfloat_to_float(pr_raw)

    result = {"spo2": spo2, "pulse_rate": pr, "flags": flags}

    offset = 5

    # SpO2PR-Fast
    if flags & 0x01 and offset + 4 <= len(data):
        result["spo2_fast"] = sfloat_to_float(struct.unpack_from("<H", data, offset)[0])
        result["pr_fast"] = sfloat_to_float(
            struct.unpack_from("<H", data, offset + 2)[0]
        )
        offset += 4

    # SpO2PR-Slow
    if flags & 0x02 and offset + 4 <= len(data):
        result["spo2_slow"] = sfloat_to_float(struct.unpack_from("<H", data, offset)[0])
        result["pr_slow"] = sfloat_to_float(
            struct.unpack_from("<H", data, offset + 2)[0]
        )
        offset += 4

    # Measurement Status
    if flags & 0x04 and offset + 2 <= len(data):
        result["measurement_status"] = struct.unpack_from("<H", data, offset)[0]
        offset += 2

    # Device and Sensor Status
    if flags & 0x08 and offset + 3 <= len(data):
        result["device_sensor_status"] = int.from_bytes(
            data[offset : offset + 3], "little"
        )
        offset += 3

    # Pulse Amplitude Index (PI)
    if flags & 0x10 and offset + 2 <= len(data):
        result["pi"] = sfloat_to_float(struct.unpack_from("<H", data, offset)[0])
        offset += 2

    return result


def parse_plx_spot_check(data: bytes):
    """Try to parse PLX Spot-Check Measurement per Bluetooth SIG spec.

    Format:
      Byte 0: Flags
        bit 0: Timestamp field present
        bit 1: Measurement Status field present
        bit 2: Device and Sensor Status field present
        bit 3: Pulse Amplitude Index field present
        bit 4: Device Clock is Not Set
      Bytes 1-2: SpO2 (SFLOAT, %)
      Bytes 3-4: Pulse Rate (SFLOAT, bpm)
      Then optional fields based on flags.
    """
    if len(data) < 5:
        return None

    flags = data[0]
    spo2_raw = struct.unpack_from("<H", data, 1)[0]
    pr_raw = struct.unpack_from("<H", data, 3)[0]

    spo2 = sfloat_to_float(spo2_raw)
    pr = sfloat_to_float(pr_raw)

    result = {"spo2": spo2, "pulse_rate": pr, "flags": flags}

    offset = 5

    # Timestamp (7 bytes: year(2) month day hours minutes seconds)
    if flags & 0x01 and offset + 7 <= len(data):
        year = struct.unpack_from("<H", data, offset)[0]
        month, day, hour, minute, sec = data[offset + 2 : offset + 7]
        result["timestamp"] = (
            f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{sec:02d}"
        )
        offset += 7

    # Measurement Status
    if flags & 0x02 and offset + 2 <= len(data):
        result["measurement_status"] = struct.unpack_from("<H", data, offset)[0]
        offset += 2

    # Device and Sensor Status
    if flags & 0x04 and offset + 3 <= len(data):
        result["device_sensor_status"] = int.from_bytes(
            data[offset : offset + 3], "little"
        )
        offset += 3

    # Pulse Amplitude Index (PI)
    if flags & 0x08 and offset + 2 <= len(data):
        result["pi"] = sfloat_to_float(struct.unpack_from("<H", data, offset)[0])
        offset += 2

    return result


def sfloat_to_float(raw: int) -> float:
    """Convert Bluetooth SFLOAT (16-bit) to Python float.

    SFLOAT: 4-bit exponent (signed) + 12-bit mantissa (signed).
    Special values: NaN, NRes, +INF, -INF, Reserved.
    """
    # Special values
    if raw == 0x07FF:
        return float("nan")  # NaN
    if raw == 0x0800:
        return float("nan")  # NRes
    if raw == 0x07FE:
        return float("inf")  # +INF
    if raw == 0x0802:
        return float("-inf")  # -INF

    # Extract exponent (4 MSBs, signed) and mantissa (12 LSBs, signed)
    exponent = raw >> 12
    mantissa = raw & 0x0FFF

    # Sign-extend exponent (4-bit signed)
    if exponent >= 8:
        exponent -= 16

    # Sign-extend mantissa (12-bit signed)
    if mantissa >= 0x0800:
        mantissa -= 0x1000

    return mantissa * (10.0**exponent)


def parse_plx_features(data: bytes):
    """Decode PLX Features characteristic."""
    if len(data) < 2:
        return {}

    supported = struct.unpack_from("<H", data, 0)[0]
    features = []
    if supported & 0x01:
        features.append("Measurement Status support")
    if supported & 0x02:
        features.append("Device and Sensor Status support")
    if supported & 0x04:
        features.append("Stored data (spot-check) support")
    if supported & 0x08:
        features.append("SpO2PR-Fast support")
    if supported & 0x10:
        features.append("SpO2PR-Slow support")
    if supported & 0x20:
        features.append("Pulse Amplitude Index support")
    if supported & 0x40:
        features.append("Multiple Bonds support")

    result = {"supported_features": supported, "features": features}

    offset = 2
    if supported & 0x01 and offset + 2 <= len(data):
        result["measurement_status_bits"] = struct.unpack_from("<H", data, offset)[0]
        offset += 2
    if supported & 0x02 and offset + 3 <= len(data):
        result["device_sensor_status_bits"] = int.from_bytes(
            data[offset : offset + 3], "little"
        )
        offset += 3

    return result


async def find_device():
    """Scan and return the first PLX device found."""
    print("Scanning for pulse oximeter (10s)...")
    devices = await BleakScanner.discover(timeout=10, return_adv=True)

    for device, adv in devices.values():
        name = (adv.local_name or device.name or "").lower()
        plx_uuid = "00001822-0000-1000-8000-00805f9b34fb"
        has_plx = plx_uuid in [str(u).lower() for u in adv.service_uuids]
        if has_plx or "choicemmed" in name or "medisana" in name or "pm 100" in name:
            print(f"Found: {device.address} ({adv.local_name or device.name})")
            return device

    return None


async def main():
    # Find device
    if len(sys.argv) > 1:
        address = sys.argv[1]
        print(f"Scanning for {address}...")
        devices = await BleakScanner.discover(timeout=10, return_adv=True)
        device = None
        for d, adv in devices.values():
            if d.address.lower() == address.lower():
                device = d
                break
        if not device:
            print(f"Device {address} not found!")
            sys.exit(1)
    else:
        device = await find_device()
        if not device:
            print("No pulse oximeter found! Put it on your finger and try again.")
            sys.exit(1)

    print(f"\nConnecting to {device.address}...")

    disconnected = asyncio.Event()

    def on_disconnect(client):
        print("\n--- Disconnected ---")
        disconnected.set()

    async with BleakClient(
        device, timeout=15, disconnected_callback=on_disconnect
    ) as client:
        print(f"Connected! MTU={client.mtu_size}")

        # Read features
        try:
            feat_data = await client.read_gatt_char(PLX_FEATURES)
            feat = parse_plx_features(feat_data)
            print(f"\nPLX Features: {feat_data.hex()}")
            print(f"  Supported: {', '.join(feat.get('features', ['unknown']))}")
        except Exception as e:
            print(f"Could not read PLX Features: {e}")

        # Read battery
        try:
            batt = await client.read_gatt_char(BATTERY_LEVEL)
            print(f"Battery: {batt[0]}%")
        except Exception:
            pass

        print(f"\n{'=' * 60}")
        print("Listening for measurements... (Ctrl+C to stop)")
        print(f"{'=' * 60}\n")

        # Subscribe to PLX Continuous
        has_continuous = False
        has_spot_check = False

        for service in client.services:
            for char in service.characteristics:
                if char.uuid.lower() == PLX_CONTINUOUS:
                    has_continuous = True
                if char.uuid.lower() == PLX_SPOT_CHECK:
                    has_spot_check = True

        if has_continuous:

            def on_continuous(sender, data: bytearray):
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                parsed = parse_plx_continuous(bytes(data))
                if parsed:
                    spo2 = parsed["spo2"]
                    pr = parsed["pulse_rate"]
                    line = f"[{ts}] CONTINUOUS  SpO2={spo2:5.1f}%  Pulse={pr:5.1f} bpm"
                    if "pi" in parsed:
                        line += f"  PI={parsed['pi']:.2f}%"
                    if "spo2_fast" in parsed:
                        line += f"  (fast: {parsed['spo2_fast']:.1f}%/{parsed['pr_fast']:.1f}bpm)"
                    if "spo2_slow" in parsed:
                        line += f"  (slow: {parsed['spo2_slow']:.1f}%/{parsed['pr_slow']:.1f}bpm)"
                    print(line)
                else:
                    print(f"[{ts}] CONTINUOUS  raw={data.hex()}")

            await client.start_notify(PLX_CONTINUOUS, on_continuous)
            print("Subscribed to PLX Continuous Measurement (notify)")

        if has_spot_check:

            def on_spot_check(sender, data: bytearray):
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                parsed = parse_plx_spot_check(bytes(data))
                if parsed:
                    spo2 = parsed["spo2"]
                    pr = parsed["pulse_rate"]
                    line = f"[{ts}] SPOT-CHECK  SpO2={spo2:5.1f}%  Pulse={pr:5.1f} bpm"
                    if "pi" in parsed:
                        line += f"  PI={parsed['pi']:.2f}%"
                    if "timestamp" in parsed:
                        line += f"  @{parsed['timestamp']}"
                    print(line)
                else:
                    print(f"[{ts}] SPOT-CHECK  raw={data.hex()}")

            await client.start_notify(PLX_SPOT_CHECK, on_spot_check)
            print("Subscribed to PLX Spot-Check Measurement (indicate)")

        if not has_continuous and not has_spot_check:
            # Fallback: subscribe to everything that notifies/indicates
            print(
                "No standard PLX characteristics found. Subscribing to all notify/indicate..."
            )
            for service in client.services:
                for char in service.characteristics:
                    if "notify" in char.properties or "indicate" in char.properties:

                        def make_handler(uuid):
                            def handler(sender, data: bytearray):
                                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                                print(f"[{ts}] {uuid}  raw={data.hex()}")

                            return handler

                        try:
                            await client.start_notify(
                                char.uuid, make_handler(char.uuid)
                            )
                            print(f"  Subscribed to {char.uuid}")
                        except Exception as e:
                            print(f"  Failed to subscribe to {char.uuid}: {e}")

        # Wait until disconnected or Ctrl+C
        try:
            await disconnected.wait()
        except asyncio.CancelledError:
            pass

    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
