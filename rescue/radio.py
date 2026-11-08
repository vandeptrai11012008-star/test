"""Radio abstraction. Swap LoopbackRadio for the real dongle without touching logic."""


class Radio:
    def send(self, raw: bytes) -> None: ...
    def recv(self, timeout: float):  # -> bytes | None
        ...


class LoopbackRadio(Radio):
    """Two linked ends in memory, used by tests and the CLI demo."""

    def __init__(self):
        self.peer, self.inbox = None, []

    @staticmethod
    def pair():
        a, b = LoopbackRadio(), LoopbackRadio()
        a.peer, b.peer = b, a
        return a, b

    def send(self, raw):
        self.peer.inbox.append(raw)

    def recv(self, timeout=0.0):
        return self.inbox.pop(0) if self.inbox else None


class SerialLoRaRadio(Radio):
    """USB serial link to an ESP32 node running firmware/ (the dongle, or a band board
    in USB test mode; both speak the same framing):

        0x20 + 32 bytes     one packet, either direction
        '#' ... '\\n'        a text line: host commands ("#T <unix>", "#P", "#?")
                            or device logs ("#BUZZ 1 0 1", "#SILENT", "#RX rssi=-60 ...")

    ``port`` is a serial device path (needs pyserial: pip install pyserial). Tests pass
    ``stream=`` instead: any object with ``read(n)`` and ``write(b)``."""

    FRAME = 0x20

    def __init__(self, port: str = None, baud: int = 115200, stream=None, sync_clock: bool = True,
                 boot_wait: float = 2.5):
        import collections
        import threading
        import time
        self._time = time
        if stream is None:
            try:
                import serial  # pyserial
            except ImportError as e:
                raise SystemExit("SerialLoRaRadio needs pyserial: pip install pyserial") from e
            stream = serial.Serial(port, baud, timeout=0.05)
        self.port = port or "stream"
        self.stream = stream
        self.packets = collections.deque()
        self.lines = collections.deque(maxlen=200)
        self.hello = None
        self._cv = threading.Condition()
        self._stop = False
        threading.Thread(target=self._reader, daemon=True).start()
        # Opening the port resets most ESP32 boards; wait for the boot banner.
        self.hello = self.wait_line("#HELLO", boot_wait)
        if not self.hello:                        # board did not reset on open: ask it
            self.command("#?")
            ok = self.wait_line("#OK", 1.0)
            self.hello = "#HELLO " + ok[4:] if ok else None
        if sync_clock:
            self.set_clock(time.time())

    # -- Radio interface --------------------------------------------------
    def send(self, raw: bytes) -> None:
        if len(raw) != 32:
            raise ValueError("packets are exactly 32 bytes")
        self.stream.write(bytes([self.FRAME]) + raw)
        self._flush()

    def recv(self, timeout: float = 0.0):
        deadline = self._time.time() + timeout
        with self._cv:
            while not self.packets:
                left = deadline - self._time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)
            return self.packets.popleft()

    # -- text channel -----------------------------------------------------
    def command(self, text: str) -> None:
        self.stream.write((text.rstrip("\n") + "\n").encode())
        self._flush()

    def set_clock(self, unix_seconds: float) -> None:
        self.command("#T %.3f" % unix_seconds)  # milliseconds: the FSK hop schedule needs them
        self.wait_line("#TIME", 1.0)

    def wait_line(self, prefix: str, timeout: float):
        """Wait for (and consume) the next line starting with ``prefix``."""
        deadline = self._time.time() + timeout
        with self._cv:
            while True:
                for i, line in enumerate(self.lines):
                    if line.startswith(prefix):
                        del self.lines[i]
                        return line
                left = deadline - self._time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)

    def clear(self) -> None:
        """Drop queued text lines (call before an exchange)."""
        with self._cv:
            self.lines.clear()

    def close(self) -> None:
        self._stop = True
        try:
            self.stream.close()
        except Exception:
            pass

    # -- internals ----------------------------------------------------------
    def _flush(self):
        flush = getattr(self.stream, "flush", None)
        if flush:
            flush()

    def _reader(self):
        state, buf = "idle", bytearray()
        while not self._stop:
            try:
                chunk = self.stream.read(64)
            except Exception:
                return
            if not chunk:
                if chunk == b"" and not hasattr(self.stream, "in_waiting"):
                    return  # EOF on a plain pipe
                continue
            for b in chunk:
                if state == "idle":
                    if b == self.FRAME:
                        state, buf = "packet", bytearray()
                    elif b == ord("#"):
                        state, buf = "line", bytearray(b"#")
                elif state == "packet":
                    buf.append(b)
                    if len(buf) == 32:
                        with self._cv:
                            self.packets.append(bytes(buf))
                            self._cv.notify_all()
                        state = "idle"
                else:
                    if b in (10, 13):
                        with self._cv:
                            self.lines.append(buf.decode(errors="replace"))
                            self._cv.notify_all()
                        state = "idle"
                    elif len(buf) < 200:
                        buf.append(b)


class SdrRadio(Radio):
    """An SDR as the drone's LoRa radio, through sdr/sdr_bridge.py (GNU Radio,
    run in its own environment). Localhost UDP: we send 32-byte packets to the bridge on
    ``tx_port`` and receive packets and "#..." status lines on ``rx_port``.

    Same interface the console uses on SerialLoRaRadio: send / recv / packets / wait_line /
    clear / command / set_clock / hello / port."""

    def __init__(self, tx_port: int = 47701, rx_port: int = 47702, host: str = "127.0.0.1", wait: float = 2.0):
        import collections
        import socket
        import threading
        import time
        self._time = time
        self.addr = (host, tx_port)
        self.port = "sdr udp:%d" % tx_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, rx_port))
        self.sock.settimeout(0.2)
        self.packets = collections.deque()
        self.lines = collections.deque(maxlen=200)
        self._cv = threading.Condition()
        self._stop = False
        threading.Thread(target=self._reader, daemon=True).start()
        self.command("#?")
        ok = self.wait_line("#OK", wait) or self.wait_line("#HELLO", 0)
        self.hello = "#HELLO sdr" if ok else None

    def send(self, raw: bytes) -> None:
        if len(raw) != 32:
            raise ValueError("packets are exactly 32 bytes")
        self.sock.sendto(raw, self.addr)

    def recv(self, timeout: float = 0.0):
        deadline = self._time.time() + timeout
        with self._cv:
            while not self.packets:
                left = deadline - self._time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)
            return self.packets.popleft()

    def command(self, text: str) -> None:
        self.sock.sendto(text.strip().encode(), self.addr)

    def set_clock(self, unix_seconds: float) -> None:
        pass                                      # the SDR has no band clock to set

    def wait_line(self, prefix: str, timeout: float):
        deadline = self._time.time() + timeout
        with self._cv:
            while True:
                for i, line in enumerate(self.lines):
                    if line.startswith(prefix):
                        del self.lines[i]
                        return line
                left = deadline - self._time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)

    def clear(self) -> None:
        with self._cv:
            self.lines.clear()

    def close(self) -> None:
        self._stop = True
        self.sock.close()

    def _reader(self):
        while not self._stop:
            try:
                data, _ = self.sock.recvfrom(512)
            except OSError:
                continue
            with self._cv:
                if len(data) == 32:
                    self.packets.append(data)
                elif data.startswith(b"#"):
                    self.lines.append(data.decode(errors="replace").strip())
                self._cv.notify_all()
