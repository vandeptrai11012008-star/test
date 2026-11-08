"""SDR <-> FSK hopping link <-> UDP bridge: the drone's radio for the wristband and the dog, on the
RFM69 / SX1276 FSK link (firmware/include/config.h, sdr/fsk_link.py). Use sdr_bridge.py for LoRa.

RESPONSIBLE USE: an SDR is a radio transmitter. Only transmit where your radio regulations allow,
with any license they require, at the lowest power that works. Never jam, spoof or interfere.
This project does not support unlicensed, unlawful or unethical use. See RESPONSIBLE_USE.md.

    python3 sdr/sdr_fsk_bridge.py             # real ADALM-PLUTO
    python3 sdr/sdr_fsk_bridge.py --loopback  # no radio: what we send, we hear

Needs numpy; the real radio also needs pyadi-iio and libiio (sdr/README.md). The console talks to it
with rescue.radio.SdrRadio over localhost UDP, exactly as with sdr_bridge.py:
    console -> bridge  udp 127.0.0.1:47701   one 32-byte packet = send it
    bridge -> console  udp 127.0.0.1:47702   one 32-byte packet = received off the air
                                             b"#..." text lines = status

What the bridge does, mirroring firmware/src/dongle: the channel comes from the keyed hop schedule and
the laptop clock (so the drone side needs no clock to set). A packet from the console goes out on the
current slot's channel, then the next, then the previous, listening for a reply on each; while idle
the bridge follows the schedule so it hears the dog's verdict and the wristband's ALIVE. The bridge
never checks packets; the console does.
"""
import argparse
import queue
import socket
import threading
import time

import numpy as np

import fsk_link as F

TX_PORT, RX_PORT = 47701, 47702
MIN_TX_ATTEN_DB = 10          # keeps the output at about -3 dBm or less
IDLE_CAPTURE_S = 0.03
TAIL_S = 0.01                 # kept from the previous idle capture, so a packet split across two is not lost


# ---- radio hardware interface ----------------------------------------------------------------------
class Radio:
    """tune(hz); transmit(iq) blocks until sent; capture(seconds) returns samples from now."""
    fs = 2_000_000.0

    def tune(self, hz):
        raise NotImplementedError

    def transmit(self, iq):
        raise NotImplementedError

    def capture(self, seconds):
        raise NotImplementedError

    def exchange(self, iq, seconds):
        """Transmit iq and return what was received over the next `seconds`, starting no later than the
        transmission (the band answers ~30 ms after it ends). Default: one after the other."""
        self.transmit(iq)
        return self.capture(seconds)


class LoopbackRadio(Radio):
    """No hardware: whatever is transmitted is received on the next capture."""

    def __init__(self, fs=2_000_000.0):
        self.fs = fs
        self.pending = None
        self.rng = np.random.default_rng(1)

    def tune(self, hz):
        pass

    def transmit(self, iq):
        self.pending = iq

    def capture(self, seconds):
        n = int(self.fs * seconds)
        buf = (self.rng.normal(size=n) + 1j * self.rng.normal(size=n)).astype(np.complex64) * 0.01
        if self.pending is not None:
            m = min(len(self.pending), n - 1000)
            buf[500:500 + m] += self.pending[:m]
            self.pending = None
        return buf


class PlutoRadio(Radio):
    """ADALM-PLUTO through pyadi-iio 0.0.19 with libiio 0.25 (sdr/README.md).

    Found on real hardware: with libiio 1.x and pyadi-iio 0.0.21 a transmit stream cannot be restarted
    ("resource busy") and a one-shot buffer times out; libiio 0.25 + pyadi-iio 0.0.19 send a frame per
    call reliably. A frame is pushed with tx(), then rx() returns the samples captured around it, which
    include the band's reply (~30 ms after the frame)."""

    FRAME_S = 0.03       # every TX buffer is the same length: the library cannot change it on the fly

    def __init__(self, uri, tx_atten, rx_gain, fs=2_000_000.0):
        self.fs, self.uri, self.tx_atten, self.rx_gain = fs, uri, tx_atten, rx_gain
        self.sdr = None
        self._open()

    def _open(self):
        try:
            import adi
        except ImportError as e:
            raise SystemExit("the real radio needs pyadi-iio and libiio: see sdr/README.md") from e
        uri = self.uri
        if not uri:                                   # macOS: the Pluto shows up as a USB context
            try:
                import iio
                ctx = iio.scan_contexts()
                found = [u for u in ctx if "0456:b673" in ctx[u]]
                uri = found[0] if found else ""
            except Exception:                         # noqa: BLE001
                uri = ""
        self.sdr = None
        self.sdr = adi.Pluto(uri or "ip:192.168.2.1")
        self.sdr.sample_rate = int(self.fs)
        self.sdr.rx_rf_bandwidth = 400_000
        self.sdr.tx_rf_bandwidth = 400_000
        self.sdr.gain_control_mode_chan0 = "manual"
        self.sdr.rx_hardwaregain_chan0 = self.rx_gain
        self.sdr.tx_hardwaregain_chan0 = -abs(self.tx_atten)
        self.sdr.tx_cyclic_buffer = False
        self._lo = None

    def tune(self, hz):
        if self._lo != int(hz):
            self.sdr.tx_lo = int(hz)
            self.sdr.rx_lo = int(hz)
            self._lo = int(hz)

    def _frame(self, iq):
        buf = np.zeros(int(self.fs * self.FRAME_S), np.complex64)
        buf[:min(len(iq), len(buf))] = iq[:len(buf)]
        return buf * (2 ** 14 * 0.7)

    def _retry(self, fn):
        try:
            return fn()
        except Exception:                             # noqa: BLE001  a libiio hiccup: reopen once and retry
            lo = self._lo
            time.sleep(0.5)
            self._open()
            if lo:
                self.tune(lo)
            return fn()

    def transmit(self, iq):
        self._retry(lambda: self.sdr.tx(self._frame(iq)))

    def capture(self, seconds):
        def go():
            self.sdr.rx_buffer_size = max(1024, int(self.fs * seconds))
            return np.asarray(self.sdr.rx(), dtype=np.complex64) / 2 ** 11
        return self._retry(go)

    def exchange(self, iq, seconds):
        def go():
            self.sdr.rx_buffer_size = max(1024, int(self.fs * seconds))
            self.sdr.tx(self._frame(iq))
            return np.asarray(self.sdr.rx(), dtype=np.complex64) / 2 ** 11
        return self._retry(go)


# ---- the exchange logic (mirrors firmware/src/dongle) -------------------------------------------------
class Engine:
    OFFSETS = (0, 1, -1)

    def __init__(self, radio, link=None, schedule=None, on_packet=None, on_line=None, clock=time.time,
                 hear_self=False):
        self.radio = radio
        self.link = link or F.load_link()
        self.schedule = schedule or F.HopSchedule(F.DEMO_KEY, self.link)
        self.modem = F.FskModem(self.link, radio.fs)
        self.on_packet = on_packet or (lambda p: None)
        self.on_line = on_line or (lambda t: None)
        self.clock = clock
        self.hear_self = hear_self
        self.cur_ch = None
        self.last_tx = None
        self.tail = None
        self.recent = {}

    def _slot(self):
        return self.schedule.slot(self.clock() * 1000.0)

    def _tune(self, ch):
        self.cur_ch = ch
        self.radio.tune(self.link.freq_hz(ch))

    def _deliver(self, payloads):
        n = 0
        for p in payloads:
            if len(p) != F.PACKET_LEN:
                continue
            if not self.hear_self and p == self.last_tx:
                continue                                  # our own transmission leaking into our receiver
            if self.clock() - self.recent.get(p, -1.0) < 0.05:
                continue                                  # the same packet seen twice across captures
            self.recent[p] = self.clock()
            self.on_packet(p)
            n += 1
        return n

    def exchange(self, payload: bytes) -> bool:
        """Send on the current slot's channel, then the next and the previous; stop at the first reply."""
        slot = self._slot()
        self.last_tx = payload
        iq = self.modem.modulate(payload)
        for off in self.OFFSETS:
            self._tune(self.schedule.channel(slot + off))
            cap = self.radio.exchange(iq, self.link.reply_wait_ms / 1000.0 + 0.04)
            if self._deliver(self.modem.decode(cap)):
                return True
        self.on_line("#NOREPLY")
        return False

    def idle(self):
        """Follow the schedule and listen, so the dog's verdict and the band's ALIVE are heard."""
        ch = self.schedule.channel(self._slot())
        if ch != self.cur_ch:
            self._tune(ch)
            self.tail = None
        cap = self.radio.capture(IDLE_CAPTURE_S)
        keep = int(self.radio.fs * TAIL_S)
        both = cap if self.tail is None else np.concatenate([self.tail, cap])
        self.tail = cap[-keep:]
        self._deliver(self.modem.decode(both))


# ---- UDP front-end ----------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--uri", default="", help="SDR URI, e.g. usb:2.1.5 or ip:192.168.2.1 (default: find a USB Pluto, else ip:192.168.2.1)")
    ap.add_argument("--loopback", action="store_true", help="no radio: transmitted packets are received")
    ap.add_argument("--tx-atten", type=float, default=15.0,
                    help="TX attenuation dB (default 15, about -8 dBm; minimum 10, about -3 dBm)")
    ap.add_argument("--rx-gain", type=float, default=50.0)
    ap.add_argument("--key-hex", default=F.DEMO_KEY.hex(), help="16-byte group key (hex); the hop order comes from it")
    ap.add_argument("--hear-self", action="store_true",
                    help="self-test: keep our own transmissions heard by our receiver (normally dropped)")
    a = ap.parse_args()
    if a.tx_atten < MIN_TX_ATTEN_DB:
        ap.error("--tx-atten must be at least %d dB (keeps output at about -3 dBm or less)" % MIN_TX_ATTEN_DB)
    key = bytes.fromhex(a.key_hex)
    if len(key) != 16:
        ap.error("--key-hex must be 16 bytes (32 hex digits)")

    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    console = ("127.0.0.1", RX_PORT)

    def status(text):
        print(text, flush=True)
        out.sendto(text.encode(), console)

    def on_packet(p):
        out.sendto(p, console)
        print("RX", p.hex(), flush=True)

    link = F.load_link()
    if a.loopback:
        radio = LoopbackRadio()
    else:
        print("NOTICE: you are about to transmit around %.1f MHz. Make sure the frequencies and power are legal "
              "where you are and that you hold any license required. See RESPONSIBLE_USE.md."
              % (link.ch0_hz / 1e6), flush=True)
        radio = PlutoRadio(a.uri, a.tx_atten, a.rx_gain)
    engine = Engine(radio, link, F.HopSchedule(key, link), on_packet, status, hear_self=a.hear_self or a.loopback)
    status("#HELLO sdr fsk %s %.0f kbps %d channels%s" % ("loopback" if a.loopback else "radio", link.bitrate / 1e3,
                                                         link.channels, " hear-self" if engine.hear_self else ""))

    todo = queue.Queue()

    def listen():
        inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        inp.bind(("127.0.0.1", TX_PORT))
        while True:
            data, _ = inp.recvfrom(256)
            todo.put(data)

    threading.Thread(target=listen, daemon=True).start()
    try:
        while True:
            try:
                data = todo.get_nowait()
            except queue.Empty:
                engine.idle()
                continue
            if data == b"#?":
                status("#OK sdr fsk")
            elif data.startswith(b"#T"):
                pass                                       # the laptop's clock is the clock
            elif len(data) == F.PACKET_LEN:
                print("TX", data.hex(), flush=True)
                engine.exchange(data)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
