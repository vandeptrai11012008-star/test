"""Drone side interrogator. Feed states: UNKNOWN (default), FRIENDLY, ALIVE.
Never FRIENDLY without a verified exchange."""
import argparse
from . import protocol as P
from .band import Band
from .radio import LoopbackRadio


class Interrogator:
    def __init__(self, key, radio, clock=P.slot_of):
        self.key, self.radio, self.clock = key, radio, clock
        self.out = None      # outstanding nonce
        self.ex = None       # {"nonce","slot","reply","alive"}
        self.feed = "UNKNOWN"

    def challenge(self):
        self.out = P.new_nonce()
        self.ex = None
        self.feed = "UNKNOWN"
        self.radio.send(P.make(self.key, P.CHALLENGE, self.out, self.clock()).encode())

    def poll(self, timeout=0.0):
        raw = self.radio.recv(timeout)
        while raw is not None:
            self._rx(raw)
            raw = self.radio.recv(0.0)
        return self.feed

    def _rx(self, raw):
        try:
            pkt = P.Packet.decode(raw)
        except ValueError:
            return self._bad()
        slot = P.verify(self.key, pkt, self.clock())
        if slot is None or self.out is None or pkt.nonce != self.out:
            return self._bad()
        if pkt.ptype == P.REPLY:
            if self.ex is not None:       # replay of an accepted reply
                return self._bad()
            self.ex = {"nonce": pkt.nonce, "slot": slot, "alive": False}
            self.feed = "FRIENDLY"
        elif pkt.ptype == P.ALIVE and self.ex and not self.ex["alive"]:
            self.ex["alive"] = True
            self.feed = "ALIVE"
        else:
            self._bad()

    def _bad(self):
        self.feed = "UNKNOWN"


def demo():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    d_radio, b_radio = LoopbackRadio.pair()
    drone, band = Interrogator(key, d_radio), Band(key)
    drone.challenge()
    reply = band.on_packet(b_radio.inbox.pop(0))
    b_radio.send(reply)
    print("after challenge:", drone.poll(), "| band blink bits", band.events[-1][1])
    b_radio.send(band.press())
    print("after press:    ", drone.poll())
    b_radio.send(reply)
    print("after replay:   ", drone.poll())


if __name__ == "__main__":
    argparse.ArgumentParser(description="Rescue band check-in loopback demo").parse_args()
    demo()
