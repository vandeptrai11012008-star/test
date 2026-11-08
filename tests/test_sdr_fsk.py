"""The Pluto FSK bridge (sdr/): link settings, framing, the numpy GFSK modem, the hop schedule against
the firmware's C++, and the exchange logic against a simulated wristband. No radio needed.
What is NOT covered: a real Pluto, and whether a real RFM69 / SX1276 decodes these frames (see the
open-point constants at the top of sdr/fsk_link.py)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdr"))
import fsk_link as F          # noqa: E402
import sdr_fsk_bridge as B    # noqa: E402

CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
LINK = F.load_link()


def test_link_settings_come_from_config_h():
    assert LINK.bitrate == 50_000 and LINK.dev_hz == 25_000
    assert LINK.channels >= 50 and LINK.sync == bytes([0x2D, 0xD4])
    assert LINK.freq_hz(0) >= 903e6 and LINK.freq_hz(LINK.channels - 1) <= 927e6
    assert LINK.step_hz >= 250e3                          # channel spacing at least the signal bandwidth


def test_crc16_is_the_ccitt_variant_the_chips_use():
    assert F.crc16(b"123456789") == 0xE5CC                # CRC-16/AUG-CCITT, init 0x1D0F


def test_frame_layout():
    bits = F.build_bits(LINK, bytes(range(32)))
    assert len(bits) == LINK.preamble_bits + 16 + 8 + 32 * 8 + 16
    assert bits[:LINK.preamble_bits] == [1, 0] * (LINK.preamble_bits // 2)       # 0xAA preamble
    sync = bits[LINK.preamble_bits:LINK.preamble_bits + 16]
    assert F._bytes(sync) == LINK.sync
    assert F._bytes(bits[LINK.preamble_bits + 16:LINK.preamble_bits + 24]) == bytes([32])


def _channel(modem, payload, rng, snr_db, cfo, lead_s, tail_s):
    iq = modem.modulate(payload)
    x = np.concatenate([np.zeros(int(modem.fs * lead_s), np.complex64), iq, np.zeros(int(modem.fs * tail_s), np.complex64)])
    x = x * np.exp(2j * np.pi * cfo * np.arange(len(x)) / modem.fs)
    noise = (rng.normal(size=len(x)) + 1j * rng.normal(size=len(x))) / np.sqrt(2) * np.sqrt(1 / 10 ** (snr_db / 10))
    return x + noise.astype(np.complex64)


def test_modem_roundtrip_with_noise_carrier_offset_and_timing():
    modem, rng = F.FskModem(LINK), np.random.default_rng(7)
    for snr in (25, 14, 10):
        for _ in range(8):
            payload = bytes(rng.integers(0, 256, 32, dtype=np.uint8))
            rx = _channel(modem, payload, rng, snr, rng.uniform(-15e3, 15e3), rng.uniform(0.001, 0.02), rng.uniform(0.005, 0.03))
            assert modem.decode(rx) == [payload], "snr %d" % snr


def test_modem_ignores_noise_and_bad_crc_and_finds_two_frames():
    modem, rng = F.FskModem(LINK), np.random.default_rng(3)
    noise = (rng.normal(size=100_000) + 1j * rng.normal(size=100_000)).astype(np.complex64)
    assert modem.decode(noise) == []
    bits = F.build_bits(LINK, bytes(range(32)))
    bad = list(bits)
    bad[LINK.preamble_bits + 16 + 8 + 5] ^= 1             # flip one payload bit, keep the old CRC
    assert modem.decode(modem.modulate_bits(bad)) == []
    a, b = bytes(range(32)), bytes(range(100, 132))
    z = lambda n: np.zeros(n, np.complex64)
    both = np.concatenate([z(8000), modem.modulate(a), z(20000), modem.modulate(b), z(8000)])
    assert modem.decode(both) == [a, b]


@pytest.mark.skipif(CXX is None, reason="no C++ compiler")
def test_python_hop_schedule_equals_the_firmware(tmp_path):
    exe = tmp_path / "hop_dump"
    subprocess.run([CXX, "-std=c++17", "-I", str(ROOT / "firmware/include"), "-I", str(ROOT / "firmware/lib/rescue_core"),
                    str(ROOT / "firmware/lib/rescue_core/rescue_core.cpp"), str(ROOT / "firmware/test/host/hop_dump.cpp"),
                    "-o", str(exe)], check=True)
    first = 224_263_140                                   # spans a cycle boundary (multiple of 50 at ...150)
    c_out = subprocess.run([str(exe), str(first), "260"], capture_output=True, text=True, check=True).stdout.split()
    sched = F.HopSchedule(F.DEMO_KEY, LINK)
    assert [int(x) for x in c_out] == [sched.channel(first + i) for i in range(260)]


class FakeBand(B.Radio):
    """A simulated wristband: listens on one channel; a CHALLENGE heard there is answered ~20 ms later."""
    fs = 2_000_000.0

    def __init__(self, link, listen_ch, reply=b"R" * 32):
        self.link, self.listen_hz, self.reply = link, link.freq_hz(listen_ch), reply
        self.modem, self.tuned, self.pending, self.tuned_log = F.FskModem(link, self.fs), None, None, []
        self.rng = np.random.default_rng(5)

    def tune(self, hz):
        self.tuned = hz
        self.tuned_log.append(hz)

    def transmit(self, iq):
        pad = np.zeros(2000, np.complex64)
        if self.tuned == self.listen_hz and self.modem.decode(np.concatenate([pad, iq, pad])):
            self.pending = self.modem.modulate(self.reply)

    def capture(self, seconds):
        n = int(self.fs * seconds)
        buf = (self.rng.normal(size=n) + 1j * self.rng.normal(size=n)).astype(np.complex64) * 0.01
        if self.pending is not None:
            start = int(self.fs * 0.02)
            m = min(len(self.pending), n - start)
            buf[start:start + m] += self.pending[:m]
            self.pending = None
        return buf


NOW = 1_800_000_000.0     # fixed fake clock


def _engine(band, sched, **kw):
    got, lines = [], []
    e = B.Engine(band, LINK, sched, on_packet=got.append, on_line=lines.append, clock=lambda: NOW, **kw)
    return e, got, lines


@pytest.mark.parametrize("off", [0, 1, -1])
def test_exchange_finds_the_band_on_the_current_next_or_previous_slot(off):
    sched = F.HopSchedule(F.DEMO_KEY, LINK)
    slot = sched.slot(NOW * 1000)
    band = FakeBand(LINK, sched.channel(slot + off))
    e, got, lines = _engine(band, sched)
    assert e.exchange(b"C" * 32) is True and got == [b"R" * 32] and lines == []
    order = [sched.channel(slot + o) for o in (0, 1, -1)]
    assert band.tuned_log == [LINK.freq_hz(c) for c in order[:{0: 1, 1: 2, -1: 3}[off]]]   # tried in order, stopped at the reply


def test_exchange_reports_no_reply_and_drops_its_own_echo():
    sched = F.HopSchedule(F.DEMO_KEY, LINK)
    slot = sched.slot(NOW * 1000)
    far = (sched.channel(slot) + 7) % LINK.channels
    far = far if far not in [sched.channel(slot + o) for o in (0, 1, -1)] else (far + 11) % LINK.channels
    e, got, lines = _engine(FakeBand(LINK, far), sched)
    assert e.exchange(b"C" * 32) is False and got == [] and lines == ["#NOREPLY"]
    # our own transmission heard by our own receiver is dropped unless --hear-self
    e2, got2, _ = _engine(B.LoopbackRadio(), sched)
    e2.exchange(b"C" * 32)
    assert got2 == []
    e3, got3, _ = _engine(B.LoopbackRadio(), sched, hear_self=True)
    e3.exchange(b"C" * 32)
    assert got3 == [b"C" * 32]


def test_idle_hears_an_unsolicited_packet_on_the_current_channel():
    sched = F.HopSchedule(F.DEMO_KEY, LINK)
    slot = sched.slot(NOW * 1000)

    class Talker(FakeBand):
        def capture(self, seconds):                         # the wristband sends ALIVE during this window
            self.pending = self.modem.modulate(b"A" * 32)
            return super().capture(seconds)

    band = Talker(LINK, sched.channel(slot))
    e, got, _ = _engine(band, sched)
    e.idle()
    assert got == [b"A" * 32] and band.tuned_log == [LINK.freq_hz(sched.channel(slot))]
