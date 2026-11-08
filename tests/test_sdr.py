"""SDR path: the drone's radio is an SDR (via sdr/sdr_bridge.py) and the dog's
Feather runs the near check itself, answering a signed DOG_CHECK with a signed DOG_VERDICT.
FakeAir stands in for the bridge, the air, the Feather and the band."""
import collections
import time

import pytest

from rescue import protocol as P
import rescue.console as console_mod
from rescue.band import Band
from rescue.console import Console
from test_console import KEY, SLOT, land_all


class FakeAir:
    """SdrRadio look-alike. CHALLENGE goes to a real Band; DOG_CHECK goes to a simulated
    Feather that challenges that band and reports per ``dog`` ('good', 'slow', 'far', 'off', 'forged')."""

    hello, port = "#HELLO sdr", "sdr (fake)"

    def __init__(self, dog="good"):
        self.dog = dog
        self.band = Band(KEY, lambda: SLOT)
        self.packets = collections.deque()
        self.sent = []

    def send(self, raw):
        self.sent.append(raw)
        if raw[0] == P.CHALLENGE:                      # far check: the band answers over the air
            reply = self.band.on_packet(raw)
            if reply:
                self.packets.append(reply)
            return
        order = P.parse_dog_check(KEY, raw)
        if not order or self.dog == "off":
            return
        nonce, slot = order
        ch = P.make(KEY, P.CHALLENGE, P.new_nonce(), slot).encode()   # the Feather's own challenge
        tag_ok = self.band.on_packet(ch) is not None
        direct = 2 * console_mod.AIRTIME_MS + console_mod.BAND_PROC_MS + 1
        rssi, rtt = {"good": (-41, direct), "slow": (-44, 2 * direct), "far": (-96, direct),
                     "forged": (-40, direct)}[self.dog]
        key = bytes(16) if self.dog == "forged" else KEY
        ok = tag_ok and rssi >= console_mod.RSSI_MIN_DBM and rtt <= console_mod.RTT_BUDGET_MS
        self.packets.append(P.make_dog_verdict(key, nonce, slot, ok, tag_ok, rssi, 8, rtt))

    def recv(self, timeout=0.0):
        return self.packets.popleft() if self.packets else None

    def wait_line(self, prefix, timeout):
        return None

    def clear(self):
        pass

    def command(self, text):
        pass

    def set_clock(self, t):
        pass


@pytest.fixture(autouse=True)
def fast_sim(monkeypatch):
    monkeypatch.setattr(console_mod, "COUNTERSIGN_S", 0.0)
    monkeypatch.setattr(console_mod, "SIM_CLOSE_MM_S", 1e6)


def linkup(dog):
    air = FakeAir(dog)
    c = Console(KEY, clock=lambda: SLOT, link=air)
    c.act("linkup_start", "normal")
    v = land_all(c, c.act("linkup_far"))
    assert v["linkup"]["stage"] == "approach_near" and v["hw"]["mode"] == "SDR (%s)" % console_mod.LINK["short"]
    v = land_all(c, c.act("linkup_near"))
    assert any(p[0] == P.DOG_CHECK for p in air.sent), "the dog's Feather got a signed order"
    return v


def test_dog_packets_round_trip_and_reject_tampering():
    n = bytes([1, 2, 3, 4])
    assert P.parse_dog_check(KEY, P.make_dog_check(KEY, n, SLOT)) == (n, SLOT)
    v = P.make_dog_verdict(KEY, n, SLOT, True, True, -48, 9, 151)
    assert P.parse_dog_verdict(KEY, v, n, SLOT) == {"ok": True, "tag_ok": True, "rssi": -48, "snr": 9, "rtt": 151}
    bad = bytearray(v); bad[18] ^= 1                        # someone edits the signal strength
    assert P.parse_dog_verdict(KEY, bytes(bad), n, SLOT) is None
    assert P.parse_dog_verdict(KEY, v, bytes(4), SLOT) is None   # verdict for another order
    with pytest.raises(ValueError):                         # band and drone ignore dog packets
        P.Packet.decode(v)


def test_real_dog_verdict_good_moves_to_countersign():
    v = linkup("good")
    assert v["linkup"]["stage"] == "countersign"
    assert v["linkup"]["near"]["rssi"] == -41
    assert v["linkup"]["near"]["rtt"] == 2 * console_mod.AIRTIME_MS + console_mod.BAND_PROC_MS + 1
    assert any("Dog's Feather" in e["text"] for e in v["log"])


def test_real_dog_slow_round_trip_aborts_as_relay():
    v = linkup("slow")
    assert v["linkup"]["stage"] == "abort" and v["linkup"]["near"]["relay"]


def test_real_dog_weak_signal_holds():
    assert linkup("far")["linkup"]["stage"] == "hold"


@pytest.mark.parametrize("dog", ["off", "forged"])
def test_no_or_forged_verdict_never_passes(dog, monkeypatch):
    orig = Console._real_near
    monkeypatch.setattr(Console, "_real_near", lambda self: orig(self, wait=0.05))
    v = linkup(dog)
    assert v["linkup"]["stage"] == "hold" and not v["linkup"]["near"]["ok"]
