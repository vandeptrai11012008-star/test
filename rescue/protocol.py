"""Rescue band wire protocol. Single source of truth; the web sim mirrors it.

Packet (32 bytes): TYPE(1) VER(1) NONCE(4) SLOT16(2) HMAC8(8) PAD(16)
Tag: HMAC-SHA256(key, TYPE || NONCE || SLOT_u32_BE)[:8]
Slots: unix_seconds // 3600. Receivers accept slot-1, slot, slot+1.
Blink code: HMAC(key, b"BLINK" || SLOT_u32_BE)[0], bits 0..2 -> 3 pulses (0 short, 1 long).

Dog packets (the ground dog's own radio, a Feather 32u4 RFM9x). Same 32-byte layout; the
first bytes of PAD carry data, and the tag covers it:
  tag = HMAC-SHA256(key, TYPE || NONCE || SLOT_u32_BE || DATA)[:8]
  DOG_CHECK   (4) drone -> dog   DATA = SLOT_u32_BE (the dog has no clock; it adopts this)
  DOG_VERDICT (5) dog -> drone   DATA = OK, TAG_OK, RSSI_i8, SNR_i8, RTT_u16_BE  (NONCE = the order's)
Band and drone still accept only CHALLENGE / REPLY / ALIVE, so they ignore dog packets.
"""
import hmac
import hashlib
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Optional

CHALLENGE, REPLY, ALIVE = 1, 2, 3
DOG_CHECK, DOG_VERDICT = 4, 5
VERSION = 1
PACKET_LEN = 32
SLOT_SECONDS = 3600
DRIFT_SLOTS = (0, -1, 1)


def slot_of(unix_seconds: Optional[float] = None) -> int:
    return int((time.time() if unix_seconds is None else unix_seconds) // SLOT_SECONDS)


def tag(key: bytes, ptype: int, nonce: bytes, slot: int) -> bytes:
    msg = bytes([ptype]) + nonce + struct.pack(">I", slot)
    return hmac.new(key, msg, hashlib.sha256).digest()[:8]


def blink_bits(key: bytes, slot: int):
    h = hmac.new(key, b"BLINK" + struct.pack(">I", slot), hashlib.sha256).digest()
    return [(h[0] >> i) & 1 for i in range(3)]


@dataclass
class Packet:
    ptype: int
    nonce: bytes
    slot16: int
    mac: bytes

    def encode(self) -> bytes:
        return struct.pack(">BB4sH8s16x", self.ptype, VERSION, self.nonce, self.slot16, self.mac)

    @staticmethod
    def decode(raw: bytes) -> "Packet":
        if len(raw) != PACKET_LEN:
            raise ValueError("bad length")
        ptype, ver, nonce, slot16, mac = struct.unpack(">BB4sH8s16x", raw)
        if ver != VERSION or ptype not in (CHALLENGE, REPLY, ALIVE):
            raise ValueError("bad header")
        return Packet(ptype, nonce, slot16, mac)


def make(key: bytes, ptype: int, nonce: bytes, slot: int) -> Packet:
    return Packet(ptype, nonce, slot & 0xFFFF, tag(key, ptype, nonce, slot))


def new_nonce() -> bytes:
    return secrets.token_bytes(4)


def verify(key: bytes, pkt: Packet, now_slot: int) -> Optional[int]:
    """Return the matched slot or None. Constant-time tag compare."""
    for off in DRIFT_SLOTS:
        s = now_slot + off
        if (s & 0xFFFF) == pkt.slot16 and hmac.compare_digest(tag(key, pkt.ptype, pkt.nonce, s), pkt.mac):
            return s
    return None


# ---- dog packets ------------------------------------------------------------------
NO_REPLY_MS = 0xFFFF


def tag_ext(key: bytes, ptype: int, nonce: bytes, slot: int, data: bytes) -> bytes:
    msg = bytes([ptype]) + nonce + struct.pack(">I", slot) + data
    return hmac.new(key, msg, hashlib.sha256).digest()[:8]


def _frame(ptype: int, nonce: bytes, slot: int, mac: bytes, data: bytes) -> bytes:
    raw = struct.pack(">BB4sH8s", ptype, VERSION, nonce, slot & 0xFFFF, mac) + data
    return raw + bytes(PACKET_LEN - len(raw))


def make_dog_check(key: bytes, nonce: bytes, slot: int) -> bytes:
    data = struct.pack(">I", slot)
    return _frame(DOG_CHECK, nonce, slot, tag_ext(key, DOG_CHECK, nonce, slot, data), data)


def parse_dog_check(key: bytes, raw: bytes):
    """-> (nonce, slot) or None. The slot comes from the packet (the dog has no clock)."""
    if len(raw) != PACKET_LEN or raw[0] != DOG_CHECK or raw[1] != VERSION:
        return None
    nonce, slot16, mac = raw[2:6], struct.unpack(">H", raw[6:8])[0], raw[8:16]
    data = raw[16:20]
    slot = struct.unpack(">I", data)[0]
    if slot & 0xFFFF != slot16 or not hmac.compare_digest(tag_ext(key, DOG_CHECK, nonce, slot, data), mac):
        return None
    return nonce, slot


def make_dog_verdict(key: bytes, nonce: bytes, slot: int, ok: bool, tag_ok: bool,
                     rssi: int, snr: int, rtt_ms: int) -> bytes:
    data = struct.pack(">BBbbH", int(ok), int(tag_ok), max(-128, min(127, rssi)), max(-128, min(127, snr)),
                       min(rtt_ms, NO_REPLY_MS))
    return _frame(DOG_VERDICT, nonce, slot, tag_ext(key, DOG_VERDICT, nonce, slot, data), data)


def parse_dog_verdict(key: bytes, raw: bytes, nonce: bytes, now_slot: int):
    """Verdict for the order ``nonce``: dict(ok, tag_ok, rssi, snr, rtt) or None if forged/foreign."""
    if len(raw) != PACKET_LEN or raw[0] != DOG_VERDICT or raw[1] != VERSION or raw[2:6] != nonce:
        return None
    slot16, mac, data = struct.unpack(">H", raw[6:8])[0], raw[8:16], raw[16:22]
    for off in DRIFT_SLOTS:
        s = now_slot + off
        if (s & 0xFFFF) == slot16 and hmac.compare_digest(tag_ext(key, DOG_VERDICT, nonce, s, data), mac):
            ok, tag_ok, rssi, snr, rtt = struct.unpack(">BBbbH", data)
            return {"ok": bool(ok), "tag_ok": bool(tag_ok), "rssi": rssi, "snr": snr,
                    "rtt": None if rtt == NO_REPLY_MS else rtt}
    return None
