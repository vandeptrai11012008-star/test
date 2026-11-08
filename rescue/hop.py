"""The FSK hopping link's settings and hop schedule, in plain Python (no numpy), so the console can show
the live hop channel and the SDR bridge can follow it. Settings come from firmware/include/config.h; the
schedule is the same algorithm as firmware/include/hop.h (tests/test_sdr_fsk.py compares them)."""
import hashlib
import hmac
import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_H = Path(__file__).resolve().parent.parent / "firmware" / "include" / "config.h"
DEMO_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")   # same public key as the tests


@dataclass(frozen=True)
class Link:
    bitrate: float        # bits per second
    dev_hz: float
    ch0_hz: float
    step_hz: float
    channels: int
    dwell_ms: int
    preamble_bits: int
    sync: bytes
    reply_wait_ms: int
    bt: float = 0.5       # Gaussian shaping (FSK_SHAPING = RADIOLIB_SHAPING_0_5)

    def freq_hz(self, ch: int) -> float:
        return self.ch0_hz + self.step_hz * ch


def load_link(path: Path = CONFIG_H) -> Link:
    text = Path(path).read_text()

    def num(name):
        m = re.search(r"#define\s+%s\s+(-?[0-9.]+)" % name, text)
        if not m:
            raise ValueError("%s not found in %s" % (name, path))
        return float(m.group(1))

    sync = re.search(r"#define\s+FSK_SYNC_WORD\s+\{([^}]*)\}", text)
    return Link(bitrate=num("FSK_BITRATE_KBPS") * 1e3, dev_hz=num("FSK_DEV_KHZ") * 1e3,
                ch0_hz=num("FSK_CH0_MHZ") * 1e6, step_hz=num("FSK_CH_STEP_MHZ") * 1e6,
                channels=int(num("FSK_CHANNELS")), dwell_ms=int(num("FSK_DWELL_MS")),
                preamble_bits=int(num("FSK_PREAMBLE_BITS")),
                sync=bytes(int(x, 16) for x in sync.group(1).split(",")),
                reply_wait_ms=int(num("FSK_REPLY_WAIT_MS")))


# ---- hop schedule (same algorithm as firmware/include/hop.h) --------------------------------------
class HopSchedule:
    """Keyed pseudorandom hop schedule: slot = unix_ms // dwell; each run of `channels` slots visits
    every channel once, in an order from HMAC-SHA256(key, "hop" || cycle || counter)."""

    def __init__(self, key: bytes = DEMO_KEY, link: Link = None):
        self.key = key
        self.link = link or load_link()
        self._cycles = {}

    def slot(self, unix_ms: float) -> int:
        return int(unix_ms // self.link.dwell_ms)

    def channel(self, slot: int) -> int:
        n = self.link.channels
        cycle = slot // n
        if cycle not in self._cycles:
            if len(self._cycles) > 8:
                self._cycles.clear()
            self._cycles[cycle] = self._build(cycle)
        return self._cycles[cycle][slot % n]

    def _build(self, cycle: int):
        n = self.link.channels
        msg = b"hop" + cycle.to_bytes(8, "big")
        state = {"ctr": 0, "block": b"", "used": 32}

        def next16():
            if state["used"] + 2 > 32:
                state["block"] = hmac.new(self.key, msg + bytes([state["ctr"]]), hashlib.sha256).digest()
                state["ctr"] += 1
                state["used"] = 0
            v = (state["block"][state["used"]] << 8) | state["block"][state["used"] + 1]
            state["used"] += 2
            return v

        order = list(range(n))
        for i in range(n - 1, 0, -1):
            r = i + 1
            limit = (65536 // r) * r
            while True:
                v = next16()
                if v < limit:
                    break
            j = v % r
            order[i], order[j] = order[j], order[i]
        return order
