"""Bluetooth (BLE) link to the MechDog's FACTORY program: no files copied to the dog.

Same interface as dogctl.Dog (``send(line) -> reply``), so dogctl.py --ble and
rescue.console --dog ble work unchanged. Needs:  pip install bleak

The factory program allows one Bluetooth connection: close the Hiwonder app first.

Safety: the factory program has no "stop if the laptop goes quiet" rule, so this link
adds one on the laptop side: if no move arrives for 1 s, it sends stop. If Bluetooth
drops entirely, what the dog does depends on the factory program.
"""
import asyncio
import queue
import threading
import time

import factory_protocol as fp

NAME_HINTS = ("mechdog", "hiwonder", "hw_", "wonder")
# Common "serial over BLE" services, tried first when picking the channel.
UART_HINTS = ("6e400001", "0000ffe0", "0000fff0", "49535343")


def looks_like_dog(name):
    return bool(name) and any(h in name.lower() for h in NAME_HINTS)


def pick_channel(services):
    """From discovered services, choose (write_char_uuid, notify_char_uuid).
    ``services``: list of (service_uuid, [(char_uuid, [properties])])."""
    def score(svc):
        return 0 if any(svc[0].lower().startswith(h) for h in UART_HINTS) else 1
    write = notify = None
    for svc_uuid, chars in sorted(services, key=score):
        for uuid, props in chars:
            if write is None and ("write" in props or "write-without-response" in props):
                write = uuid
            if notify is None and ("notify" in props or "indicate" in props):
                notify = uuid
        if write and notify:
            break
    return write, notify


def scan_devices(timeout=5.0):
    """Nearby Bluetooth devices as dicts {name, address, rssi, likely}, likely MechDogs first.
    Raises ImportError if bleak is missing."""
    from bleak import BleakScanner  # noqa: F401  (ImportError tells the caller what to install)

    async def go():
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        rows = []
        for dev, adv in found.values():
            name = dev.name or adv.local_name or ""
            rows.append({"name": name, "address": dev.address, "rssi": adv.rssi, "likely": looks_like_dog(name)})
        rows.sort(key=lambda r: (not r["likely"], not r["name"], -(r["rssi"] or -999)))
        return rows

    box = {}

    def run():                                # own thread + loop: safe from any caller
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


class BleakTransport:
    """Owns an asyncio loop on a background thread; exposes blocking connect/write."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.client = None
        self.on_data = None
        self.write_uuid = self.notify_uuid = None
        self.name = self.address = None

    def _run(self, coro, timeout=20):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def connect(self, target=None):
        """``target``: device name or address; None = first device that looks like a MechDog."""
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as e:
            raise SystemExit("Bluetooth needs bleak: pip install bleak") from e

        async def go():
            devices = await BleakScanner.discover(timeout=6.0)
            dev = None
            for d in devices:
                if target and (d.address.lower() == target.lower() or (d.name or "").lower() == target.lower()):
                    dev = d
                elif not target and looks_like_dog(d.name):
                    dev = d
                if dev:
                    break
            if dev is None:
                names = ", ".join(sorted({d.name for d in devices if d.name})) or "none"
                raise ConnectionError("no MechDog found over Bluetooth (seen: %s). Is the Hiwonder app "
                                      "closed and the dog on? Try dog/ble_scan.py" % names)
            client = BleakClient(dev)
            await client.connect()
            services = [(s.uuid, [(c.uuid, list(c.properties)) for c in s.characteristics])
                        for s in client.services]
            w, n = pick_channel(services)
            if not w:
                raise ConnectionError("dog has no writable Bluetooth channel; run dog/ble_scan.py")
            if n:
                await client.start_notify(n, lambda _c, data: self.on_data and self.on_data(bytes(data)))
            self.client, self.write_uuid, self.notify_uuid = client, w, n
            self.name, self.address = dev.name, dev.address

        self._run(go(), timeout=30)

    def write(self, data):
        props = None
        for s in self.client.services:
            for c in s.characteristics:
                if c.uuid == self.write_uuid:
                    props = c.properties
        response = props is not None and "write" in props and "write-without-response" not in props
        self._run(self.client.write_gatt_char(self.write_uuid, data, response=response), timeout=5)

    def close(self):
        if self.client:
            try:
                self._run(self.client.disconnect(), timeout=5)
            except Exception:
                pass


class BleDog:
    """dogctl.Dog look-alike over Bluetooth, speaking the factory protocol."""

    DEADMAN_S = 1.0

    def __init__(self, target=None, transport=None, timeout=1.0):
        self.timeout = timeout
        self.replies = queue.Queue()
        self._buf = ""
        self.t = transport or BleakTransport()
        self.t.on_data = self._on_data
        self.t.connect(target)
        self.host = "ble:%s" % (getattr(self.t, "name", None) or target or "MechDog")
        self.moving = False
        self._last_move = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _on_data(self, data):
        frames, self._buf = fp.parse_frames(self._buf + data.decode(errors="replace"))
        for f in frames:
            self.replies.put(f)

    def _watchdog(self):
        while True:
            time.sleep(0.2)
            with self._lock:
                if self.moving and time.time() - self._last_move > self.DEADMAN_S:
                    self.moving = False
                    self.t.write(fp.STOP.encode())

    def send(self, line, retries=0, timeout=None):
        if line.strip() == "ping":
            return "ok pong mechdog (bluetooth, factory program)"
        if line.split()[0].lower() == "code":
            return self._code(line.split()[1:])
        frame, want = fp.translate(line)
        if frame.startswith("err"):
            return frame
        cmd = line.split()[0].lower()
        with self._lock:
            if cmd == "move":
                self.moving = frame != fp.STOP
                self._last_move = time.time()
            elif cmd in ("stop", "stand", "action"):
                if cmd == "action":               # factory ignores actions while walking
                    self.t.write(fp.STOP.encode())
                    time.sleep(0.15)
                self.moving = False
            while not self.replies.empty():       # drop stale replies
                self.replies.get_nowait()
            self.t.write(frame.encode())
        if want is None:
            return "ok %s (sent %s)" % (line.strip(), frame)
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                fields = self.replies.get(timeout=max(0.01, deadline - time.time()))
            except queue.Empty:
                break
            if fields and fields[0] == str(want):
                return fp.reply_text(cmd, fields)
        raise TimeoutError("no %s reply from the dog over Bluetooth" % cmd)

    def _code(self, a):
        """Countersign: flash a 3-pulse code on the eyes with the band's timing (LONG 450 ms,
        SHORT 150 ms, 250 ms gaps), timed here because the factory program has no such command."""
        bits = a[0] if a else ""
        if len(bits) != 3 or any(c not in "01" for c in bits):
            return "err code needs 3 bits like 101"
        r, g, b = [max(0, min(255, int(v))) for v in a[1:4]] if len(a) >= 4 else (0, 255, 0)
        with self._lock:
            self.moving = False
            self.t.write(fp.STOP.encode())
        for bit in bits:
            self.t.write(("CMD|4|3|%d|%d|%d|$" % (r, g, b)).encode())
            time.sleep(0.45 if bit == "1" else 0.15)
            self.t.write(b"CMD|4|3|0|0|0|$")
            time.sleep(0.25)
        return "ok code " + bits

    def move(self, step, turn=0):
        return self.send("move %d %d" % (step, turn))

    def stop(self):
        return self.send("stop")

    def close(self):
        self.t.close()
