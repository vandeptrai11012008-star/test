"""Bluetooth LE link to the wristband (firmware: BAND_BLE, advertises "Rescue-Band-xxxx").

The band offers a Nordic-UART-style service carrying exactly the USB framing
(0x20 + 32-byte packets, "#..." lines), so rescue.radio.SerialLoRaRadio runs unchanged on top:

    from rescue.ble_band import BleStream
    radio = SerialLoRaRadio(stream=BleStream("Rescue-Band-1A2B"))

Needs bleak (requirements.txt). Range is a room, not LoRa's kilometres: this is a demo and
bench link. The real check-in exchange is LoRa.
"""
import asyncio
import threading

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"     # we write
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"     # band notifies
NAME_PREFIX = "Rescue-Band"


def looks_like_band(name, uuids=()):
    return bool(name and name.startswith(NAME_PREFIX)) or NUS_SERVICE in [u.lower() for u in uuids or ()]


def scan_bands(timeout=5.0):
    """Nearby devices as dicts {name, address, rssi, likely}, wristbands first. ImportError without bleak."""
    from bleak import BleakScanner

    async def go():
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        rows = []
        for dev, adv in found.values():
            name = dev.name or adv.local_name or ""
            rows.append({"name": name, "address": dev.address, "rssi": adv.rssi,
                         "likely": looks_like_band(name, adv.service_uuids)})
        rows.sort(key=lambda r: (not r["likely"], not r["name"], -(r["rssi"] or -999)))
        return rows

    box = {}

    def run():
        try:
            box["rows"] = asyncio.run(go())
        except Exception as e:
            box["err"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout + 10)
    if "err" in box:
        raise box["err"]
    return box.get("rows", [])


class BleStream:
    """read(n) / write(b) over the band's Bluetooth service, for SerialLoRaRadio(stream=...)."""

    in_waiting = 0          # tells SerialLoRaRadio's reader that an empty read is not end-of-file
    CHUNK = 20              # bytes per write: safe at the smallest Bluetooth MTU

    def __init__(self, target=None, scan_s=6.0):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as e:
            raise SystemExit("Bluetooth needs bleak: .venv/bin/pip install -r requirements.txt") from e
        self._buf = bytearray()
        self._cv = threading.Condition()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.client = None
        self.name = self.address = None

        async def connect():
            found = await BleakScanner.discover(timeout=scan_s, return_adv=True)
            pick = None
            for dev, adv in found.values():
                name = dev.name or adv.local_name or ""
                if target and target.lower() in (name.lower(), dev.address.lower()):
                    pick = (dev, name)
                    break
                if not target and looks_like_band(name, adv.service_uuids):
                    pick = (dev, name)
            if pick is None:
                names = ", ".join(sorted({(d.name or a.local_name) for d, a in found.values()
                                          if (d.name or a.local_name)})) or "none"
                raise ConnectionError("no wristband %sfound over Bluetooth (seen: %s). Is it powered, "
                                      "with the Bluetooth firmware?" % ((target + " ") if target else "", names))
            client = BleakClient(pick[0])
            await client.connect()
            await client.start_notify(NUS_TX, lambda _c, data: self._on_data(bytes(data)))
            self.client, self.name, self.address = client, pick[1], pick[0].address

        asyncio.run_coroutine_threadsafe(connect(), self.loop).result(scan_s + 20)

    def _on_data(self, data):
        with self._cv:
            self._buf += data
            self._cv.notify_all()

    def read(self, n):
        with self._cv:
            if not self._buf:
                self._cv.wait(0.05)
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def write(self, data):
        async def send():
            for i in range(0, len(data), self.CHUNK):
                await self.client.write_gatt_char(NUS_RX, data[i:i + self.CHUNK], response=False)
        asyncio.run_coroutine_threadsafe(send(), self.loop).result(5)

    def flush(self):
        pass

    def close(self):
        if self.client:
            try:
                asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop).result(5)
            except Exception:
                pass
