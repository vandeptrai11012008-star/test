"""Find the MechDog over Bluetooth and list its channels. READ-ONLY: nothing is sent to the
dog (with --listen it only subscribes to notifications, the way the app does).

    pip install bleak
    python3 dog/ble_scan.py                       # list nearby devices, dog-like names marked
    python3 dog/ble_scan.py --connect MechDog     # connect by name or address, list channels
    python3 dog/ble_scan.py --connect MechDog --listen 15   # also print anything it sends

Close the Hiwonder phone app first: the dog accepts one Bluetooth connection at a time.
Paste the output back so the channel and protocol can be confirmed.
"""
import argparse
import asyncio
import sys

from ble_link import looks_like_dog, pick_channel


async def scan():
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    rows = sorted(found.values(), key=lambda da: -(da[1].rssi or -999))
    print("%-4s %-28s %-40s %s" % ("", "name", "address", "rssi"))
    for dev, adv in rows:
        mark = "DOG?" if looks_like_dog(dev.name or adv.local_name) else ""
        print("%-4s %-28s %-40s %s" % (mark, (dev.name or adv.local_name or "-")[:28], dev.address, adv.rssi))
    if not any(looks_like_dog(d.name or a.local_name) for d, a in rows):
        print("\nNo dog-like name seen. Is it on, and the Hiwonder app closed? Its name may differ:\n"
              "try --connect <address> on the strongest unknown device near you.")


async def inspect(target, listen):
    from bleak import BleakClient, BleakScanner
    dev = None
    for d in await BleakScanner.discover(timeout=8.0):
        if d.address.lower() == target.lower() or (d.name or "").lower() == target.lower():
            dev = d
            break
    if dev is None:
        sys.exit("device %r not found; run without --connect to list devices" % target)
    async with BleakClient(dev) as client:
        print("connected to %s (%s)\n" % (dev.name, dev.address))
        services = []
        for s in client.services:
            print("service %s  %s" % (s.uuid, s.description))
            chars = []
            for c in s.characteristics:
                print("   char %s  [%s]  %s" % (c.uuid, ", ".join(c.properties), c.description))
                chars.append((c.uuid, list(c.properties)))
            services.append((s.uuid, chars))
        w, n = pick_channel(services)
        print("\nchosen for commands: write=%s notify=%s" % (w, n))
        if listen and n:
            print("listening %d s for anything the dog sends (move it with its buttons, or wait)..." % listen)
            await client.start_notify(n, lambda _c, data: print("  <-", bytes(data)))
            await asyncio.sleep(listen)


def main():
    ap = argparse.ArgumentParser(description="Read-only Bluetooth scan for the MechDog")
    ap.add_argument("--connect", metavar="NAME_OR_ADDRESS")
    ap.add_argument("--listen", type=int, default=0, metavar="SECONDS")
    a = ap.parse_args()
    try:
        import bleak  # noqa: F401
    except ImportError:
        sys.exit("needs bleak: pip install bleak")
    asyncio.run(inspect(a.connect, a.listen) if a.connect else scan())


if __name__ == "__main__":
    main()
