#!/usr/bin/env python3
"""Show ALL BLE devices with full detail, sorted by signal strength."""

import asyncio
from bleak import BleakScanner


async def main():
    print("Scanning ALL BLE devices (10s)...\n")

    devices = await BleakScanner.discover(timeout=10, return_adv=True)

    sorted_devs = sorted(
        devices.values(),
        key=lambda x: x[1].rssi,
        reverse=True,
    )

    for i, (device, adv) in enumerate(sorted_devs, 1):
        name = adv.local_name or device.name or "(no name)"
        print(f"[{i:2d}] {device.address}")
        print(f"     Name: {name}")
        print(f"     RSSI: {adv.rssi}")
        if adv.service_uuids:
            for u in adv.service_uuids:
                print(f"     Service UUID: {u}")
        if adv.manufacturer_data:
            for mid, mdata in adv.manufacturer_data.items():
                print(f"     Manufacturer 0x{mid:04X}: {mdata.hex()}")
        if adv.service_data:
            for suuid, sdata in adv.service_data.items():
                print(f"     Service data [{suuid}]: {sdata.hex()}")
        print()

    print(f"Total: {len(sorted_devs)} devices")


if __name__ == "__main__":
    asyncio.run(main())
