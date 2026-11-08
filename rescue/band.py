"""Reference band state machine. Port this logic to ESP32 firmware."""
from . import protocol as P


class Band:
    def __init__(self, key: bytes, clock=P.slot_of):
        self.key, self.clock, self.seen = key, clock, set()
        self.last = None  # (nonce, slot) of last verified challenge
        self.events = []  # ("buzz", bits) for hardware/test hooks

    def on_packet(self, raw: bytes):
        """Return a reply packet (bytes) or None. Silence on any failure."""
        try:
            pkt = P.Packet.decode(raw)
        except ValueError:
            return None
        if pkt.ptype != P.CHALLENGE or pkt.nonce in self.seen:
            return None
        slot = P.verify(self.key, pkt, self.clock())
        if slot is None:
            return None
        self.seen.add(pkt.nonce)
        self.last = (pkt.nonce, slot)
        self.events.append(("buzz", P.blink_bits(self.key, slot)))
        return P.make(self.key, P.REPLY, pkt.nonce, slot).encode()

    def press(self):
        """Button press: signed ALIVE for the last verified challenge, else None."""
        if not self.last:
            return None
        nonce, slot = self.last
        return P.make(self.key, P.ALIVE, nonce, slot).encode()
