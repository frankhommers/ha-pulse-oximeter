#!/usr/bin/env python3
"""Scan for BLE devices and find the Medisana PM 100 Connect."""

import asyncio
from bleak import BleakScanner

KNOWN_NAMES = {"pm 100", "medisana", "pm100", "vitadock", "plx"}


async def main():
    print("Scanning for BLE devices (10 seconds)...")
    print("Look for your PM 100 Connect in the list below.\n")

    devices = await BleakScanner.discover(timeout=10, return_adv=True)

    # Sort by RSSI (strongest first)
    sorted_devs = sorted(
        devices.values(),
        key=lambda x: x[1].rssi,
        reverse=True,
    )

    interesting = []
    for device, adv in sorted_devs:
        name = (adv.local_name or device.name or "").lower()
        is_interesting = any(k in name for k in KNOWN_NAMES)

        # Also check for standard Pulse Oximeter Service UUID (0x1822)
        plx_uuid = "00001822-0000-1000-8000-00805f9b34fb"
        has_plx = plx_uuid in [str(u).lower() for u in adv.service_uuids]

        if is_interesting or has_plx:
            interesting.append((device, adv))

        # Print all devices with names
        if adv.local_name or device.name:
            print(
                f"  {device.address}  RSSI={adv.rssi:4d}  Name={adv.local_name or device.name}"
            )
            if adv.service_uuids:
                print(f"    Service UUIDs: {adv.service_uuids}")
            if adv.manufacturer_data:
                for mid, mdata in adv.manufacturer_data.items():
                    print(f"    Manufacturer 0x{mid:04X}: {mdata.hex()}")
            if adv.service_data:
                for suuid, sdata in adv.service_data.items():
                    print(f"    Service data [{suuid}]: {sdata.hex()}")

    print(f"\n{'=' * 60}")
    if interesting:
        print(f"Found {len(interesting)} potential pulse oximeter(s):\n")
        for device, adv in interesting:
            print(f"  >>> {device.address}  Name={adv.local_name or device.name}")
            print(f"      RSSI={adv.rssi}")
            print(f"      Service UUIDs: {adv.service_uuids}")
            if adv.manufacturer_data:
                for mid, mdata in adv.manufacturer_data.items():
                    print(f"      Manufacturer 0x{mid:04X}: {mdata.hex()}")
            if adv.service_data:
                for suuid, sdata in adv.service_data.items():
                    print(f"      Service data [{suuid}]: {sdata.hex()}")
    else:
        print("No pulse oximeter found. Make sure:")
        print("  1. The PM 100 Connect is turned on")
        print("  2. It is in pairing/discoverable mode")
        print("  3. It is not connected to another device (Vitadock+ app)")


if __name__ == "__main__":
    asyncio.run(main())
