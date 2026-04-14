#!/usr/bin/env python3
"""
Connect to the Medisana PM 100 Connect and enumerate all GATT services,
characteristics, and descriptors. Also attempts to read readable values.

Usage: python3 gatt_explore.py <MAC_ADDRESS>
"""

import asyncio
import sys
import struct
from bleak import BleakClient, BleakScanner
from bleak.uuids import normalize_uuid_16

# Well-known BLE UUIDs for pulse oximeters
KNOWN_UUIDS = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "0000180f-0000-1000-8000-00805f9b34fb": "Battery Service",
    "00001822-0000-1000-8000-00805f9b34fb": "Pulse Oximeter Service (PLX)",
    "00001809-0000-1000-8000-00805f9b34fb": "Health Thermometer",
    "0000181c-0000-1000-8000-00805f9b34fb": "User Data",
    "00001805-0000-1000-8000-00805f9b34fb": "Current Time Service",
    # Characteristics
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00002a04-0000-1000-8000-00805f9b34fb": "Peripheral Preferred Connection Parameters",
    "00002a19-0000-1000-8000-00805f9b34fb": "Battery Level",
    "00002a23-0000-1000-8000-00805f9b34fb": "System ID",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number String",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number String",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision String",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision String",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision String",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name String",
    "00002a2a-0000-1000-8000-00805f9b34fb": "IEEE Regulatory Certification",
    "00002a50-0000-1000-8000-00805f9b34fb": "PnP ID",
    "00002a5e-0000-1000-8000-00805f9b34fb": "PLX Spot-Check Measurement",
    "00002a5f-0000-1000-8000-00805f9b34fb": "PLX Continuous Measurement",
    "00002a60-0000-1000-8000-00805f9b34fb": "PLX Features",
    "00002a52-0000-1000-8000-00805f9b34fb": "Record Access Control Point",
    # Descriptors
    "00002902-0000-1000-8000-00805f9b34fb": "Client Characteristic Configuration (CCCD)",
    "00002901-0000-1000-8000-00805f9b34fb": "Characteristic User Description",
}


def uuid_name(uuid_str: str) -> str:
    u = uuid_str.lower()
    if u in KNOWN_UUIDS:
        return KNOWN_UUIDS[u]
    # Try to extract 16-bit UUID
    if u.endswith("-0000-1000-8000-00805f9b34fb"):
        short = u.split("-")[0].lstrip("0") or "0"
        return f"0x{short.upper()}"
    return "Custom/Proprietary"


def format_value(data: bytes) -> str:
    """Try to interpret raw bytes as string, then as hex."""
    if not data:
        return "(empty)"
    # Try UTF-8 string
    try:
        s = data.decode("utf-8")
        if s.isprintable():
            return f'"{s}"'
    except (UnicodeDecodeError, ValueError):
        pass
    # Show hex + decimal for small values
    if len(data) <= 4:
        val = int.from_bytes(data, "little")
        return f"{data.hex()} (decimal: {val})"
    return data.hex()


async def scan_and_connect(name_filter: str = None, address: str = None):
    """Scan for the device first, then connect using the BLEDevice object.
    This avoids the 'device not found' error on macOS."""
    print(f"Scanning for device...")
    target = None

    devices = await BleakScanner.discover(timeout=10, return_adv=True)
    for device, adv in devices.values():
        if address and device.address.lower() == address.lower():
            target = device
            break
        if name_filter:
            name = (adv.local_name or device.name or "").lower()
            if name_filter.lower() in name:
                target = device
                break
        # Also match on PLX service UUID
        plx_uuid = "00001822-0000-1000-8000-00805f9b34fb"
        if plx_uuid in [str(u).lower() for u in adv.service_uuids]:
            target = device
            break

    if not target:
        print("Device not found in scan! Make sure it's on and active.")
        return None
    print(f"Found: {target.address} ({target.name})")
    return target


async def explore(address: str):
    print(f"Looking for {address}...")

    device = await scan_and_connect(address=address)
    if not device:
        # Also try by name
        device = await scan_and_connect(name_filter="choicemmed")
    if not device:
        return

    print(f"Connecting to {device.address}...")
    async with BleakClient(device, timeout=15) as client:
        print(f"Connected: {client.is_connected}")
        print(f"MTU: {client.mtu_size}")
        print(f"\n{'=' * 70}")
        print(f"GATT Service Enumeration")
        print(f"{'=' * 70}\n")

        for service in client.services:
            sname = uuid_name(service.uuid)
            print(f"SERVICE: {service.uuid}")
            print(f"  Name: {sname}")
            print(f"  Handle: 0x{service.handle:04X}")

            for char in service.characteristics:
                cname = uuid_name(char.uuid)
                props = ", ".join(char.properties)
                print(f"\n  CHARACTERISTIC: {char.uuid}")
                print(f"    Name: {cname}")
                print(f"    Handle: 0x{char.handle:04X}")
                print(f"    Properties: {props}")

                # Try to read if readable
                if "read" in char.properties:
                    try:
                        value = await client.read_gatt_char(char)
                        print(f"    Value: {format_value(value)}")
                    except Exception as e:
                        print(f"    Value: (read error: {e})")

                # List descriptors
                for desc in char.descriptors:
                    dname = uuid_name(desc.uuid)
                    print(f"    DESCRIPTOR: {desc.uuid}")
                    print(f"      Name: {dname}")
                    try:
                        dval = await client.read_gatt_descriptor(desc.handle)
                        print(f"      Value: {format_value(dval)}")
                    except Exception as e:
                        print(f"      Value: (read error: {e})")

            print()

        # Summary
        print(f"\n{'=' * 70}")
        print("SUMMARY")
        print(f"{'=' * 70}")
        service_uuids = [s.uuid for s in client.services]
        plx_uuid = "00001822-0000-1000-8000-00805f9b34fb"
        if plx_uuid in service_uuids:
            print("  [OK] Standard Pulse Oximeter Service (PLX) detected!")
            print("  This device follows the Bluetooth SIG standard.")
        else:
            print("  [!!] No standard PLX service found.")
            print("  This device likely uses a proprietary protocol.")
            print("  Custom/unknown service UUIDs found:")
            for s in client.services:
                if uuid_name(s.uuid) == "Custom/Proprietary":
                    print(f"    - {s.uuid}")


async def main():
    if len(sys.argv) < 2:
        # No address given - scan for PLX devices
        print("No address given, scanning for pulse oximeters...")
        device = await scan_and_connect(name_filter="choicemmed")
        if not device:
            device = await scan_and_connect()  # try PLX UUID match
        if not device:
            print("No device found. Make sure the oximeter is on your finger.")
            sys.exit(1)
        await explore(device.address)
    else:
        address = sys.argv[1]
        await explore(address)


if __name__ == "__main__":
    asyncio.run(main())
