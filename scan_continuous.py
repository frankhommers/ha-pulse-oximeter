#!/usr/bin/env python3
"""Continuous BLE scanner - keeps scanning until PM 100 is found or Ctrl+C."""

import asyncio
from bleak import BleakScanner

KNOWN_NAMES = {"pm 100", "medisana", "pm100", "vitadock", "plx", "pulseox"}


async def main():
    round_nr = 0
    found = False
    while not found:
        round_nr += 1
        print(f"\n--- Scan round {round_nr} (5s) --- Press Ctrl+C to stop ---")

        devices = await BleakScanner.discover(timeout=5, return_adv=True)

        sorted_devs = sorted(
            devices.values(),
            key=lambda x: x[1].rssi,
            reverse=True,
        )

        for device, adv in sorted_devs:
            name = (adv.local_name or device.name or "").lower()
            is_interesting = any(k in name for k in KNOWN_NAMES)

            plx_uuid = "00001822-0000-1000-8000-00805f9b34fb"
            has_plx = plx_uuid in [str(u).lower() for u in adv.service_uuids]

            if is_interesting or has_plx:
                found = True
                print(f"\n{'*' * 60}")
                print(f"  FOUND: {device.address}")
                print(f"  Name:  {adv.local_name or device.name}")
                print(f"  RSSI:  {adv.rssi}")
                if adv.service_uuids:
                    print(f"  Service UUIDs: {adv.service_uuids}")
                if adv.manufacturer_data:
                    for mid, mdata in adv.manufacturer_data.items():
                        print(f"  Manufacturer 0x{mid:04X}: {mdata.hex()}")
                if adv.service_data:
                    for suuid, sdata in adv.service_data.items():
                        print(f"  Service data [{suuid}]: {sdata.hex()}")
                print(f"{'*' * 60}")
                print(f"\nNow run:  python3 gatt_explore.py {device.address}")
                break

            # Show named devices for orientation
            if adv.local_name or device.name:
                label = adv.local_name or device.name
                # Only show if somewhat close
                if adv.rssi > -90:
                    print(f"  {device.address}  RSSI={adv.rssi:4d}  {label}")

        if not found:
            print(f"  ({len(devices)} devices seen, no oximeter yet)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScan stopped.")
