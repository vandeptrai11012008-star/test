"""Operator console: the real Band and Interrogator behind a local web UI.

Run:  python3 -m rescue.console          (opens http://127.0.0.1:8011)

Every packet is built, signed and verified by rescue/protocol.py, rescue/band.py and
rescue/interrogator.py over a LoopbackRadio. The browser only animates packets in
flight and asks the server to deliver each one when it "lands". Accept or reject
decisions always come from Band.on_packet and Interrogator.poll. The step-by-step
build and verify traces shown in the UI are diagnostics computed alongside, and
each trace's outcome is checked against the real decision (see tests).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import struct
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import protocol as P
from .band import Band
from .interrogator import Interrogator
from .radio import LoopbackRadio

DEMO_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")  # same demo key as the tests
ATTACKER_KEY = bytes.fromhex("deadbeef" * 4)
NAMES = {P.CHALLENGE: "CHALLENGE", P.REPLY: "AUTO REPLY", P.ALIVE: "ALIVE"}
HTML = Path(__file__).with_name("console.html")


def render_html():
    """console.html with the radio wording of the current link (FSK or LoRa) filled in."""
    text = HTML.read_text()
    for token, key in (("@@RADIO@@", "radio"), ("@@SHORT@@", "short"), ("@@BROADCAST@@", "broadcast"), ("@@UP@@", "up")):
        text = text.replace(token, LINK[key])
    return text.encode()
# The radio link the real hardware speaks. "fsk" (default): RFM69 / SX1276 FSK with 50 channels hopping on a
# keyed schedule (firmware/include/config.h, rescue/hop.py). "lora": the earlier LoRa link. Switch with
# --link lora; set_link() changes the numbers the simulation, the UI text and the near check use.
LINKS = {
    "fsk": {"id": "fsk", "short": "FSK", "radio": "FSK hopping, 903-927 MHz", "band": "903-927 MHz hopping",
            "airtime": "FSK 50 kbps, ~7 ms on air", "airtime_ms": 7,
            # the band answers through its serial radio modem (Feather): ~30 ms, estimated, not measured
            "band_proc_ms": 30, "rtt_budget_ms": 65,
            "up": "903-927 MHZ, HOPPING", "broadcast": "903-927 MHz HOPPING BROADCAST"},
    "lora": {"id": "lora", "short": "LoRa", "radio": "LoRa 915 MHz", "band": "915 MHz",
             "airtime": "LoRa SF7 BW500, ~18 ms on air", "airtime_ms": 18, "band_proc_ms": 7, "rtt_budget_ms": 65,
             "up": "915 MHZ", "broadcast": "915 MHz BROADCAST"},
}
LINK = LINKS["fsk"]
AIRTIME = LINK["airtime"]


def set_link(name):
    """Switch the link profile ("fsk" or "lora"): updates the module-level numbers the Console reads."""
    global LINK, AIRTIME, AIRTIME_MS, BAND_PROC_MS, RTT_BUDGET_MS
    LINK = LINKS[name]
    AIRTIME = LINK["airtime"]
    AIRTIME_MS = LINK["airtime_ms"]
    BAND_PROC_MS = LINK["band_proc_ms"]
    RTT_BUDGET_MS = LINK["rtt_budget_ms"]

SCENARIOS = {
    "normal": ("Normal link-up", "Team member, healthy band"),
    "noreply": ("No reply at far", "Band powered off or out of range"),
    "drift": ("Band clock drift", "Band clock 2 hours off, outside the plus or minus 1 window"),
    "replay": ("Replay at far", "No band; an attacker answers with a reply it recorded earlier"),
    "impostor": ("Impostor at near", "A relay forwards a real band's radio; the person on site is not the wearer"),
}
LINKUP_STEPS = [
    ("approach_far", "Drone to far line", "about 210 m, out of visual range"),
    ("far_sent", "Far challenge sent", "drone's signed {radio} CHALLENGE"),
    ("far_verified", "Far reply verified", "feed FRIENDLY; drone holds and keeps watch"),
    ("alive", "Person presses I'M ALIVE", "band signs ALIVE with the drone's nonce; feed ALIVE"),
    ("approach_near", "Dog to near line", "ground robot walks in, carrying a {radio} radio"),
    ("near_sent", "Near challenge sent", "dog's own close-range CHALLENGE"),
    ("near_verified", "Near reply verified", "valid tag, strong signal, fast round trip"),
    ("countersign", "Dog shows countersign", "eyes flash this hour's code for the person"),
    ("closing", "Final approach", "ultrasonic: walk in, stop at %d cm" % 30),
    ("linkup", "Link-up", "dog stops at the person"),
]


def linkup_steps():
    """The link-up checklist, worded for the current link."""
    return [(k, t, d.replace("{radio}", LINK["short"])) for k, t, d in LINKUP_STEPS]

# stage -> (risk, caption, command, tone). Illustrative levels, not measured probabilities.
RISK = {
    "approach_far": ("HIGH", "Someone is out there and we don't know who. Don't go closer yet.", "DRONE: ADVANCE TO FAR LINE", "ok"),
    "far": ("HIGH", "The drone is asking by radio from far away.", "DRONE: HOLD AT FAR LINE", "warn"),
    "alive": ("MEDIUM", "The radio answer checked out. The person presses I'M ALIVE to say they are OK.",
              "PERSON: PRESS I'M ALIVE", "alive"),
    "approach_near": ("MEDIUM", "The radio answer says friend, from 210 m away. The dog goes in to double-check up close.",
                      "DOG: ADVANCE TO NEAR LINE", "ok"),
    "near": ("MEDIUM", "The dog asks the band up close and times the answer.", "DOG: HOLD AT NEAR LINE", "warn"),
    "countersign": ("LOW", "Proved from far and up close. The dog flashes the secret pattern so the person can check the robot too.",
                    "DOG: SHOW COUNTERSIGN", "ok"),
    "closing": ("LOW", "The dog uses its distance sensor for the last few metres and stops an arm's length away.", "DOG: CLOSE IN", "ok"),
    "complete": ("LOW", "Recognized twice: by the drone from far away and by the dog up close.", "LINKED UP", "alive"),
    "hold": ("HIGH", "", "HOLD: DO NOT APPROACH", "warn"),
    "abort": ("HIGH", "", "ABORT: DOG BACKS OFF", "bad"),
}

# Near recognition by the dog: a close-range challenge must come back with a valid tag, a strong
# signal and a short round trip. A relay has to receive the whole packet before re-sending it,
# which adds a full airtime in each direction. (set_link() switches these between FSK and LoRa.)
TELLO_ADVANCE_CM = 100   # the drone flies this far forward to the "far line" (about 2 m of room)
AIRTIME_MS = LINK["airtime_ms"]        # 32-byte packet: ~7 ms FSK at 50 kbps, ~18 ms LoRa SF7 BW500
BAND_PROC_MS = LINK["band_proc_ms"]    # band turnaround: ~30 ms FSK (through its serial modem), ~7 ms LoRa
RTT_BUDGET_MS = LINK["rtt_budget_ms"]  # direct exchange ~44 ms; through one relay ~88 ms (FSK)
RSSI_MIN_DBM = -70       # weaker than this: the band is not close to the dog
DOG_NEAR_M = 55

# Final approach on the dog's ultrasonic sensor (real readings with --dog, simulated otherwise).
STOP_MM = 300            # stop at arm's length
SONAR_START_MM = 1500    # where the sensor first picks up the person
SIM_CLOSE_MM_S = 450     # simulated closing speed
CLOSE_TIMEOUT_S = 25     # no contact by then: hold
COUNTERSIGN_S = 1.6      # three pulses on the eyes before the dog moves again
EYES = {"approach_near": "amber", "near": "amber", "countersign": "green", "closing": "green",
        "complete": "green", "hold": "red", "abort": "red"}
EYE_RGB = {"amber": (255, 140, 0), "green": (0, 255, 0), "red": (255, 0, 0), "off": (0, 0, 0)}


def fields(raw: bytes):
    """Split a raw 32-byte packet into the wire fields for the inspector."""
    return [
        {"name": "TYPE", "hex": raw[0:1].hex()},
        {"name": "VER", "hex": raw[1:2].hex()},
        {"name": "NONCE", "hex": raw[2:6].hex()},
        {"name": "SLOT16", "hex": raw[6:8].hex()},
        {"name": "HMAC8", "hex": raw[8:16].hex()},
        {"name": "PAD", "hex": "00 x16" if raw[16:] == bytes(16) else raw[16:].hex()},
    ]


def insp(title, route, raw, checks):
    return {"title": title, "route": route, "raw": raw.hex(), "fields": fields(raw), "checks": checks}


def full_hmac(key, ptype, nonce, slot):
    """The untruncated HMAC, only to show the truncation step. Same input as P.tag."""
    return hmac.new(key, bytes([ptype]) + nonce + struct.pack(">I", slot), hashlib.sha256).digest()


def step(label, detail, value="", status="ok", tone=None, **extra):
    d = {"label": label, "detail": detail, "value": value, "status": status}
    if tone:
        d["tone"] = tone
    d.update(extra)
    return d


def settle(steps):
    """Everything after the first failing check never runs: mark it skipped."""
    failed = False
    for s in steps:
        if failed:
            s["status"], s["value"] = "skip", "not reached"
        elif s["status"] == "bad":
            failed = True
    return steps


class Console:
    """One drone, one band, one attacker sharing a simulated radio channel.
    At most one packet is in flight at a time (``self.flight``)."""

    def __init__(self, key: bytes = DEMO_KEY, clock=P.slot_of, link=None, realdog=None, tello=None):
        """``link``: an rescue.radio.SerialLoRaRadio to real hardware. Then every packet
        for the band goes to the ESP32 and its reply is the decision; the in-process
        Band keeps running as the reference, and any disagreement is flagged."""
        self.key, self.clock = key, clock
        self.hw = link
        self.hw_band = bool(link and (link.hello or "").startswith("#HELLO band"))  # USB test mode
        # SDR as the drone's radio: the dog's Feather does the near check for real (DOG_CHECK/VERDICT)
        self.hw_sdr = bool(link and (link.hello or "").startswith("#HELLO sdr"))
        self.hw_unsolicited = []    # packets the board sent on its own (physical button)
        self.hw_bits = None         # last blink code the board reported
        self.realdog = realdog      # optional RealDog: the MechDog walks the near link-up for real
        self.tello = tello          # optional drone/tello.py Tello: the drone flies the link-up stages
        self._tello_stage = None
        self.dog_picker_open = False  # ask the browser to open the dog picker (e.g. --dog not found)
        self.dog_error = None
        self.make_dog = lambda target: RealDog(target)   # tests swap this out
        self.make_band = ble_band_radio                   # tests swap this out
        self.band_picker_open = False
        self.band_error = None
        self.lock = threading.Lock()
        self.reset()

    # ---- lifecycle -------------------------------------------------------

    def reset(self, band_drift=0):
        if self.hw_band:
            self.hw.set_clock(time.time() + band_drift * 3600)   # drift scenario: really skew the board
        self.d_radio, self.b_radio = LoopbackRadio.pair()
        self.drone = Interrogator(self.key, self.d_radio, self.clock)
        self.band = Band(self.key, (lambda: self.clock() + band_drift) if band_drift else self.clock)
        # The dog carries its own interrogator for the near check (a second real Interrogator).
        self.dog_d, self.dog_b = LoopbackRadio.pair()
        self.dog_rescue = Interrogator(self.key, self.dog_d, self.clock)
        self.dog_near = None        # near check result: tag, rssi, rtt, ok
        if getattr(self, "realdog", None):
            self.realdog.stop()
        self.band_present = True    # False: band off or out of range, nothing answers
        self.impostor = False       # True: a relay forwards a real band's radio from elsewhere
        self.linkup = None          # far/near recognition state machine, see linkup_start
        self.captured = None        # last band REPLY the attacker sniffed off the air
        self.pressed_nonce = None   # band.last nonce the wearer already pressed ALIVE for
        self.flight = None          # packet in the air: who, to, label, build steps
        self.exchanges = self.rejects = 0
        self.band_status = "LISTENING (wake-on-radio)"
        self.last_reject = None
        self.insp = {"title": "No packet yet", "route": "Press CHALLENGE to start the exchange.",
                     "raw": "", "fields": [], "checks": []}
        self.t0 = time.time()
        self.log = []
        return self.view()

    def _log(self, who, text, tone="info"):
        self.log.append({"t": "+%.1fs" % (time.time() - self.t0), "who": who, "text": text, "tone": tone})

    def _launch(self, label, who, to, build, sniffed=False, src="drone"):
        """``src``: which interrogator a band-bound packet came from (its reply goes back there)."""
        self.flight = {"label": label, "who": who, "to": to, "dir": "right" if to == "band" else "left",
                       "build": build, "sniffed": sniffed, "src": src}

    def _busy(self):
        return self.flight is not None

    def _signed_build(self, key, pkt, slot, nonce_step, key_note="group key", first=None):
        mac_full = full_hmac(key, pkt.ptype, pkt.nonce, slot)
        bad = key is not self.key
        steps = [first] if first else []
        steps += [
            nonce_step,
            step("Slot", "UTC hour = unix time // 3600", "%d (SLOT16 %04x)" % (slot, slot & 0xFFFF)),
            step("HMAC-SHA256", "%s over TYPE | NONCE | SLOT" % key_note, mac_full.hex()[:24] + "...",
                 tone="bad" if bad else None),
            step("Truncate", "keep the first 8 bytes as the tag", pkt.mac.hex(), tone="bad" if bad else None),
            step("Frame", "TYPE VER NONCE SLOT16 HMAC8 PAD", "32 bytes"),
            step("Transmit", LINK["band"] + ", " + AIRTIME, "on air"),
        ]
        return steps

    # ---- operator / attacker / wearer actions ----------------------------

    def challenge(self):
        if self._busy():
            return self.view()
        self.drone.challenge()
        raw = self.b_radio.inbox[-1]
        pkt = P.Packet.decode(raw)
        slot = self.clock()
        self.last_reject = None
        self.band_status = "LISTENING (wake-on-radio)"
        self._log("DRONE", "TX CHALLENGE nonce %s slot %d" % (pkt.nonce.hex(), slot))
        self.insp = insp("CHALLENGE", "Drone to band, signed with the group key", raw, [
            {"label": "tag input", "value": "type | nonce | slot (4 bytes)", "tone": "info"},
            {"label": "HMAC8", "value": pkt.mac.hex(), "tone": "warn"}])
        build = self._signed_build(self.key, pkt, slot,
                                   step("Nonce", "4 fresh random bytes, never reused", pkt.nonce.hex()))
        self._launch("CHALLENGE", "DRONE", "band", build)
        return self.view()

    def spoof(self):
        """Attacker without the key sends its own challenge to the band."""
        if self._busy():
            return self.view()
        slot = self.clock()
        pkt = P.make(ATTACKER_KEY, P.CHALLENGE, P.new_nonce(), slot)
        raw = pkt.encode()
        self.b_radio.inbox.append(raw)
        self._log("ATTACKER", "TX CHALLENGE nonce %s signed with a wrong key" % pkt.nonce.hex(), "bad")
        self.insp = insp("SPOOFED CHALLENGE", "Attacker to band, wrong key", raw, [
            {"label": "tag input", "value": "type | nonce | slot (4 bytes)", "tone": "info"},
            {"label": "HMAC8 (wrong key)", "value": pkt.mac.hex(), "tone": "bad"}])
        build = self._signed_build(ATTACKER_KEY, pkt, slot,
                                   step("Nonce", "attacker picks any 4 bytes", pkt.nonce.hex()),
                                   key_note="attacker's guessed key")
        self._launch("CHALLENGE", "ATTACKER", "band", build)
        return self.view()

    def replay(self):
        """Attacker re-sends a genuine band reply it captured earlier, byte for byte."""
        if self._busy() or self.captured is None:
            return self.view()
        raw = self.captured
        pkt = P.Packet.decode(raw)
        self.d_radio.inbox.append(raw)
        self._log("ATTACKER", "TX replay of captured AUTO REPLY, byte for byte", "bad")
        self.insp = insp("REPLAYED REPLY", "Attacker to drone, valid old packet", raw, [
            {"label": "note", "value": "copy of a real reply: the tag is genuine", "tone": "warn"}])
        build = [
            step("Sniff", "recorded the band's reply off the air earlier", pkt.nonce.hex(), tone="bad"),
            step("Copy", "same 32 bytes, genuine tag untouched", pkt.mac.hex(), tone="bad"),
            step("Transmit", LINK["band"] + ", " + AIRTIME, "on air"),
        ]
        self._launch("AUTO REPLY", "ATTACKER", "drone", build)
        return self.view()

    def press(self):
        """Wearer presses the band button: signed ALIVE for the last verified challenge."""
        if self._busy() or not self._can_alive():
            return self.view()
        ref = self.band.press()
        raw = ref
        if self.hw:
            if not self.hw_band:
                self._log("HW", "Press the BOOT button on the band itself (radio link mode)", "warn")
                return self.view()
            self._stash_unsolicited()
            self.hw.clear()
            self.hw.command("#P")                 # the board signs ALIVE itself
            raw = self.hw.recv(1.5)
            if raw is None:
                self._log("HW", "The board sent no ALIVE (no verified challenge on it?)", "bad")
                return self.view()
            if raw != ref:
                self._log("HW", "ALIVE from the board differs from the Python reference", "bad")
        self.b_radio.send(raw)
        pkt = P.Packet.decode(raw)
        nonce = pkt.nonce
        slot = P.verify(self.key, pkt, self.band.clock()) or self.band.clock()
        self.pressed_nonce = nonce
        self.band_status = "ALIVE SENT"
        self._log("BAND", "Button pressed. TX ALIVE (same nonce, same key)")
        self.insp = insp("ALIVE", "Band to drone, signed with the group key and the challenge nonce", raw, [
            {"label": "tag input", "value": "type 03 | same nonce | slot", "tone": "info"},
            {"label": "HMAC8", "value": pkt.mac.hex(), "tone": "alive"}])
        build = self._signed_build(
            self.key, pkt, slot, step("Nonce", "reused from the last verified challenge", nonce.hex()),
            first=step("Button", "wearer presses I'M ALIVE", "pressed"))
        self._launch("ALIVE", "BAND", "drone", build)
        return self.view()

    def deliver(self):
        """The packet in flight lands. Returns the view plus the receiver's ``trace``,
        and ``buzz`` bits if the band verified."""
        if not self._busy():
            return self.view()
        to, src = self.flight["to"], self.flight.get("src", "drone")
        self.flight = None
        v = self._band_rx(src) if to == "band" else self._dog_rx() if to == "dog" else self._drone_rx()
        if self.flight is None and self.linkup:
            self._linkup_quiet()                  # channel is quiet: score the stage
            v["linkup"] = self.linkup_view()
        return v

    # ---- receivers -------------------------------------------------------

    def _window(self, pkt, now=None):
        """Slot window check, same order and rule as P.verify. Returns (matched_slot, chips)."""
        now = self.clock() if now is None else now
        matched, chips = None, []
        for off in P.DRIFT_SLOTS:
            s = now + off
            if matched is not None:
                state = "untried"
            elif (s & 0xFFFF) == pkt.slot16 and hmac.compare_digest(P.tag(self.key, pkt.ptype, pkt.nonce, s), pkt.mac):
                state, matched = "match", s
            else:
                state = "miss"
            chips.append({"off": off, "state": state})
        chips.sort(key=lambda c: c["off"])
        return matched, chips

    def _expected(self, pkt, now=None):
        """Expected tag for display: the window slot matching SLOT16, else the current slot."""
        now = self.clock() if now is None else now
        for off in P.DRIFT_SLOTS:
            s = now + off
            if (s & 0xFFFF) == pkt.slot16:
                return s, P.tag(self.key, pkt.ptype, pkt.nonce, s)
        return now, P.tag(self.key, pkt.ptype, pkt.nonce, now)

    def band_trace(self, raw, seen):
        """Band.on_packet's checks, in its order: decode, type, nonce cache, tag window."""
        try:
            pkt = P.Packet.decode(raw)
        except ValueError:
            return settle([step("Decode", "exactly 32 bytes, version 1", "malformed", "bad"),
                           step("Type", "", ""), step("Fresh nonce", "", ""), step("Tag in slot window", "", "")])
        now = self.band.clock()                   # the band's own clock, which may have drifted
        matched, chips = self._window(pkt, now)
        steps = [
            step("Decode", "exactly 32 bytes, version 1", "ok"),
            step("Type", "must be CHALLENGE (01)", NAMES.get(pkt.ptype, "%02x" % pkt.ptype),
                 "ok" if pkt.ptype == P.CHALLENGE else "bad"),
            step("Fresh nonce", "not in the band's RAM nonce cache", pkt.nonce.hex(),
                 "bad" if pkt.nonce in seen else "ok"),
            step("Tag in slot window", "recompute HMAC for slots -1, 0, +1 (band clock %d)" % now,
                 "match at %+d" % (matched - now) if matched is not None else "no match",
                 "ok" if matched is not None else "bad", slots=chips),
            step("Buzz + blink", "alert the wearer: friendly drone overhead",
                 " ".join("LONG" if b else "SHORT" for b in P.blink_bits(self.key, matched or now))),
            step("Sign reply", "AUTO REPLY with the same nonce", "queued"),
        ]
        return settle(steps)

    def drone_trace(self, raw, out, ex):
        """Interrogator._rx's checks, in its order: decode, tag window, nonce, exchange state."""
        try:
            pkt = P.Packet.decode(raw)
        except ValueError:
            return settle([step("Decode", "exactly 32 bytes, version 1", "malformed", "bad"),
                           step("Tag in slot window", "", ""), step("Nonce matches", "", ""),
                           step("Exchange state", "", "")])
        matched, chips = self._window(pkt)
        nonce_ok = out is not None and pkt.nonce == out
        if pkt.ptype == P.REPLY:
            ex_step = step("First reply", "a nonce is accepted once; replays are refused",
                           "already used" if ex is not None else "first use", "bad" if ex is not None else "ok")
        elif pkt.ptype == P.ALIVE:
            ok = bool(ex) and not ex.get("alive")
            ex_step = step("ALIVE allowed", "needs a verified exchange, accepted once",
                           "yes" if ok else ("already used" if ex else "no exchange"), "ok" if ok else "bad")
        else:
            ex_step = step("Is a reply", "drone only accepts REPLY or ALIVE", NAMES.get(pkt.ptype), "bad")
        steps = [
            step("Decode", "exactly 32 bytes, version 1", "ok"),
            step("Tag in slot window", "recompute HMAC for slots -1, 0, +1",
                 "match at %+d" % (matched - self.clock()) if matched is not None else "no match",
                 "ok" if matched is not None else "bad", slots=chips),
            step("Nonce matches", "same nonce as the outstanding challenge",
                 pkt.nonce.hex() if nonce_ok else "%s vs %s" % (pkt.nonce.hex(), out.hex() if out else "none"),
                 "ok" if nonce_ok else "bad"),
            ex_step,
            step("Update feed", "only a fully verified packet changes the feed",
                 "ALIVE" if pkt.ptype == P.ALIVE else "FRIENDLY"),
        ]
        return settle(steps)

    @staticmethod
    def trace_ok(steps):
        return all(s["status"] != "bad" for s in steps)

    def _band_reason(self, steps):
        bad = next((s for s in steps if s["status"] == "bad"), None)
        return {"Decode": "malformed packet", "Type": "not a challenge", "Fresh nonce": "nonce already seen",
                "Tag in slot window": "tag mismatch"}.get(bad["label"], "rejected") if bad else ""

    def _band_rx(self, src="drone"):
        side = self.b_radio if src == "drone" else self.dog_b   # band-side radio facing that interrogator
        raw = side.inbox.pop(0)
        if not self.band_present:
            return self._lost(raw)
        trace = self.band_trace(raw, set(self.band.seen))
        ref = self.band.on_packet(raw)            # the Python reference decision
        reply, bits = ref, (self.band.events[-1][1] if ref else None)
        pkt = P.Packet.decode(raw)
        slot, exp = self._expected(pkt, self.band.clock())
        checks = self.insp["checks"] + [
            {"label": "received tag", "value": pkt.mac.hex(), "tone": "plain"},
            {"label": "expected tag, slot %d" % slot, "value": exp.hex(), "tone": "plain"}]
        if self.hw:                               # real hardware decides; compare with the reference
            reply, hw_bits = self._hw_exchange(raw)
            agree = reply == ref
            bits = hw_bits or bits
            self.hw_bits = hw_bits
            where = "ESP32 over USB" if self.hw_band else "band over " + LINK["short"]
            hw_step = step("Real band", where, ("replied" if reply else "silent") +
                           (", same bytes as Python" if agree else ", DISAGREES with Python"),
                           "ok" if agree else "bad")
            trace = trace + [hw_step]
            checks.append({"label": "hardware vs Python reference", "value": "identical" if agree else "DISAGREE",
                           "tone": "ok" if agree else "bad"})
            if not agree:
                self._log("HW", "hardware band disagrees with the Python reference", "bad")
        if reply is None:
            why = self._band_reason(trace) or "rejected"
            self.rejects += 1
            self.band_status = "SILENT (%s)" % why
            self._log("BAND", "RX challenge: %s. No buzz, no reply" % why, "bad")
            checks.append({"label": "band verdict", "value": "%s: band stays silent" % why.upper(), "tone": "bad"})
            self.insp = dict(self.insp, checks=checks)
            v = self.view()
            v["trace"] = {"who": "BAND", "label": "verifies the challenge", "steps": trace, "ok": False,
                          "verdict": "SILENT: " + why}
            return v
        matched = P.verify(self.key, P.Packet.decode(reply), self.band.clock()) or self.band.clock()
        bits = bits or P.blink_bits(self.key, matched)
        off = matched - self.band.clock()
        self.captured = reply                    # the attacker is listening too
        self._log("BAND", "RX challenge verified (slot offset %+d). Buzz, blink code, auto-reply" % off, "ok")
        checks.append({"label": "band verdict", "value": "MATCH at slot offset %+d: buzz and auto-reply" % off,
                       "tone": "ok"})
        self.insp = dict(self.insp, checks=checks)
        side.send(reply)
        rpkt = P.Packet.decode(reply)
        self.band_status = "VERIFIED, AUTO-REPLIED"
        self._log("BAND", "TX AUTO REPLY (same nonce, same key)")
        build = self._signed_build(self.key, rpkt, matched,
                                   step("Nonce", "echoed from the verified challenge", rpkt.nonce.hex()))
        self._launch("AUTO REPLY", "BAND", src, build, sniffed=src == "drone")
        v = self.view()                           # still shows the band's verdict
        v["trace"] = {"who": "BAND", "label": "verifies the challenge", "steps": trace, "ok": True,
                      "verdict": "VERIFIED: buzz, blink, auto-reply"}
        self.insp = insp("AUTO REPLY", "Band to %s, signed with the group key and the challenge nonce" % src, reply, [
            {"label": "tag input", "value": "type 02 | same nonce | slot", "tone": "info"},
            {"label": "HMAC8", "value": rpkt.mac.hex(), "tone": "ok"}])
        v["buzz"] = bits
        v["inspNext"] = self.insp               # the UI swaps to this as the reply takes off
        return v

    HW_TIMEOUT = 1.5  # seconds to wait for a reply from real hardware (LoRa airtime is ~18 ms)

    def _stash_unsolicited(self):
        """Packets the board sent on its own (a physical button press) wait in a side queue,
        so they are never mistaken for the reply to the next challenge."""
        while self.hw.packets:
            self.hw_unsolicited.append(self.hw.packets.popleft())

    def _hw_exchange(self, raw):
        """Send one packet to the real band and wait for its reply. Returns (reply, bits)."""
        self._stash_unsolicited()
        self.hw.clear()
        self.hw.send(raw)
        deadline = time.time() + self.HW_TIMEOUT
        reply = None
        while time.time() < deadline:
            reply = self.hw.recv(0.05)
            if reply is not None or self.hw.wait_line("#SILENT", 0):
                break                             # USB mode says SILENT at once; over LoRa we time out
        bits = None
        if reply is not None and self.hw_band:
            line = self.hw.wait_line("#BUZZ", 0.5)
            if line:
                bits = [int(x) for x in line.split()[1:4]]
        return reply, bits

    def incoming(self):
        """Animate a packet the real band sent on its own, e.g. its BOOT button was pressed."""
        if not self.hw or self._busy():
            return self.view()
        self._stash_unsolicited()
        if not self.hw_unsolicited:
            return self.view()
        raw = self.hw_unsolicited.pop(0)
        try:
            pkt = P.Packet.decode(raw)
        except ValueError:
            self._log("HW", "board sent a malformed packet, dropped", "bad")
            return self.view()
        name = NAMES.get(pkt.ptype, "PACKET")
        if pkt.ptype == P.ALIVE:
            self.pressed_nonce = pkt.nonce
            self.band_status = "ALIVE SENT (button on the board)"
        self.b_radio.send(raw)
        self._log("BAND", "Button pressed on the real band: TX %s" % name)
        self.insp = insp(name, "Real band to drone, its BOOT button was pressed", raw, [
            {"label": "HMAC8", "value": pkt.mac.hex(), "tone": "alive"}])
        self._launch(name, "BAND", "drone", [
            step("Button", "BOOT button pressed on the ESP32", "pressed"),
            step("Sign", "%s with the last verified nonce" % name, pkt.mac.hex()),
            step("Transmit", "back the way the last challenge came", "sent")])
        return self.view()

    def _lost(self, raw):
        """No band in range: the challenge goes unanswered. In the replay scenario the
        attacker answers instead, with a genuine reply it recorded earlier."""
        self._log("AIR", "CHALLENGE unanswered: no band in range", "bad")
        trace = {"who": "BAND", "label": "is not there", "ok": False, "verdict": "NO REPLY",
                 "steps": [step("Listen", "is any band in range and powered on?", "silence", "bad")]}
        if self.linkup and self.linkup["scenario"] == "replay" and self.captured:
            old = self.captured
            pkt = P.Packet.decode(old)
            self.d_radio.inbox.append(old)
            self._log("ATTACKER", "TX recorded AUTO REPLY to fill the silence", "bad")
            self.insp = insp("REPLAYED REPLY", "Attacker to drone, recorded during an earlier exchange", old, [
                {"label": "note", "value": "genuine tag, old nonce", "tone": "warn"}])
            self._launch("AUTO REPLY", "ATTACKER", "drone", [
                step("Sniff", "recorded a real band reply earlier today", pkt.nonce.hex(), tone="bad"),
                step("Copy", "same 32 bytes, genuine tag untouched", pkt.mac.hex(), tone="bad"),
                step("Transmit", LINK["band"] + ", " + AIRTIME, "on air")])
            v = self.view()
            v["trace"] = trace
            v["inspNext"] = self.insp
            return v
        v = self.view()
        v["trace"] = trace
        return v

    def _drone_reason(self, steps):
        bad = next((s for s in steps if s["status"] == "bad"), None)
        if not bad:
            return ""
        return {"Decode": "malformed packet", "Tag in slot window": "bad tag",
                "Nonce matches": "no outstanding challenge with this nonce",
                "First reply": "CONSUMED, this reply was already accepted (replay)",
                "ALIVE allowed": "no open verified exchange for ALIVE" if bad["value"] == "no exchange"
                else "ALIVE already accepted once",
                "Is a reply": "not a reply"}.get(bad["label"], "rejected")

    def _drone_rx(self):
        raw = self.d_radio.inbox[0]
        out, ex = self.drone.out, dict(self.drone.ex) if self.drone.ex else None
        trace = self.drone_trace(raw, out, ex)
        pkt = P.Packet.decode(raw)
        name = NAMES.get(pkt.ptype, "PACKET")
        feed = self.drone.poll()                  # the real decision
        slot, exp = self._expected(pkt)
        tag_ok = P.verify(self.key, pkt, self.clock()) is not None
        checks = [
            {"label": "received tag", "value": pkt.mac.hex(), "tone": "plain"},
            {"label": "expected tag, slot %d" % slot, "value": exp.hex(), "tone": "plain"},
            {"label": "tag valid", "value": "YES" if tag_ok else "NO", "tone": "ok" if tag_ok else "bad"}]
        if feed == "UNKNOWN":
            why = self._drone_reason(trace) or "rejected"
            self.last_reject = why
            self._log("DRONE", "RX %s rejected: %s" % (name, why), "bad")
            checks += [{"label": "nonce state", "value": why, "tone": "bad"},
                       {"label": "result", "value": "REJECTED: feed shows UNKNOWN", "tone": "bad"}]
            self.insp = dict(insp(name + " (rejected)", self.insp["route"], raw, checks))
            v = self.view()
            v["trace"] = {"who": "DRONE", "label": "verifies the %s" % name.lower(), "steps": trace, "ok": False,
                          "verdict": "REJECTED: feed UNKNOWN"}
            return v
        self.last_reject = None
        if feed == "FRIENDLY":
            self.exchanges += 1
            self._log("DRONE", "RX AUTO REPLY verified: FRIENDLY", "ok")
            checks += [{"label": "nonce state", "value": "matches outstanding challenge, now consumed", "tone": "ok"},
                       {"label": "result", "value": "VERIFIED: FRIENDLY", "tone": "ok"}]
        else:
            self._log("DRONE", "RX ALIVE verified: person acknowledged", "ok")
            checks += [{"label": "nonce state", "value": "verified exchange open, ALIVE accepted once", "tone": "ok"},
                       {"label": "result", "value": "VERIFIED: ALIVE", "tone": "ok"}]
        self.insp = insp("%s (verified)" % name, self.insp["route"], raw, checks)
        v = self.view()
        v["trace"] = {"who": "DRONE", "label": "verifies the %s" % name.lower(), "steps": trace, "ok": True,
                      "verdict": "VERIFIED: feed " + feed}
        return v

    # ---- link-up: far recognition by radio, near recognition by sight --------

    def _lu(self, sid, status, note=""):
        s = self.linkup["steps"][sid]
        s["status"], s["note"] = status, note or s["note"]
        s["t"] = "+%.1fs" % (time.time() - self.t0)

    def linkup_start(self, scenario):
        scenario = scenario if scenario in SCENARIOS else "normal"
        self.reset(band_drift=2 if scenario == "drift" else 0)
        self.band_present = scenario not in ("noreply", "replay")
        self.impostor = scenario == "impostor"
        if scenario == "replay":
            # a genuine reply the attacker recorded during an earlier exchange this hour
            earlier = Band(self.key, self.clock)
            self.captured = earlier.on_packet(P.make(self.key, P.CHALLENGE, P.new_nonce(), self.clock()).encode())
        self.linkup = {"scenario": scenario, "stage": "approach_far", "near": None, "verdict": "",
                       "steps": {sid: {"status": "pending", "t": "", "note": ""} for sid, _, _ in LINKUP_STEPS}}
        self._lu("approach_far", "active")
        self._log("DRONE", "Link-up started: %s" % SCENARIOS[scenario][0])
        return self.view()

    def linkup_far(self):
        if not self.linkup or self.linkup["stage"] != "approach_far" or self._busy():
            return self.view()
        self._lu("approach_far", "ok", "holding at ~210 m")
        self.linkup["stage"] = "far"
        self.challenge()
        self._lu("far_sent", "ok", "nonce " + self.drone.out.hex())
        self._lu("far_verified", "active")
        return self.view()

    def linkup_alive(self):
        """The wearer presses I'M ALIVE: the band signs ALIVE with the drone's challenge nonce."""
        if not self.linkup or self.linkup["stage"] != "alive" or self._busy():
            return self.view()
        self._lu("alive", "active", "button pressed")
        return self.press()

    def linkup_near(self):
        """The dog, now at the near line, sends its own close-range challenge."""
        if not self.linkup or self.linkup["stage"] != "approach_near" or self._busy():
            return self.view()
        self._lu("approach_near", "ok", "dog at ~%d m" % DOG_NEAR_M)
        self.linkup["stage"] = "near"
        self.dog_near = None
        self.dog_rescue.challenge()                  # real Interrogator on the dog's radio
        raw = self.dog_b.inbox[-1]
        pkt = P.Packet.decode(raw)
        self._lu("near_sent", "ok", "nonce " + pkt.nonce.hex())
        self._lu("near_verified", "active")
        self._log("DOG", "TX close-range CHALLENGE nonce %s" % pkt.nonce.hex())
        self.insp = insp("CHALLENGE (dog)", "Dog to band at close range, signed with the group key", raw, [
            {"label": "HMAC8", "value": pkt.mac.hex(), "tone": "warn"}])
        build = self._signed_build(self.key, pkt, self.clock(),
                                   step("Nonce", "the dog's own fresh nonce", pkt.nonce.hex()))
        build[-1] = step("Transmit", "dog's %s radio at ~%d m; start the round-trip timer" % (LINK["short"], DOG_NEAR_M), "t = 0")
        self._launch("CHALLENGE", "DOG", "band", build, src="dog")
        return self.view()

    def _near_measure(self):
        """Simulated radio measurements for the dog's exchange. With a relay in the path the
        reply is still genuine and strong, but arrives a full extra round of airtime later."""
        rssi = int(round(-45 - 0.25 * DOG_NEAR_M))
        rtt = 2 * AIRTIME_MS + BAND_PROC_MS
        if self.impostor:
            rtt += 2 * AIRTIME_MS + BAND_PROC_MS  # store-and-forward relay: receive whole packet, re-send
        return rssi, rtt

    def _real_near(self, wait=2.0):
        """Order the dog's Feather to check the band over the air, and wait for its signed verdict.
        -> dict(tag_ok, rssi, snr, rtt) or None (no verdict: Feather off, out of range, or forged)."""
        order = P.new_nonce()
        slot = self.clock()
        while self.hw.packets:
            self.hw_unsolicited.append(self.hw.packets.popleft())
        self.hw.send(P.make_dog_check(self.key, order, slot))
        deadline = time.time() + wait
        while time.time() < deadline:
            raw = self.hw.recv(max(0.01, deadline - time.time()))
            if raw is None:
                break
            v = P.parse_dog_verdict(self.key, raw, order, slot)
            if v:
                return v
        return None

    def _dog_rx(self):
        raw = self.dog_d.inbox[0]
        out, ex = self.dog_rescue.out, dict(self.dog_rescue.ex) if self.dog_rescue.ex else None
        steps = self.drone_trace(raw, out, ex)[:-1]   # same Interrogator checks as the drone
        feed = self.dog_rescue.poll()                # the real crypto decision on the dog
        rssi, rtt = self._near_measure()
        tag_ok = feed == "FRIENDLY"
        if self.hw_sdr:                         # the real dog radio decides
            real = self._real_near()
            if real is None:
                tag_ok, rssi, rtt = False, -128, 9999
                self._log("HW", "No verdict from the dog's Feather (off, out of range, or not flashed)", "bad")
            else:
                tag_ok, rssi = real["tag_ok"], real["rssi"]
                rtt = real["rtt"] if real["rtt"] is not None else 9999
                self._log("HW", "Dog's Feather: tag %s, %d dBm, SNR %d, round trip %s ms" % (
                    "ok" if tag_ok else "bad", rssi, real["snr"], real["rtt"]), "ok" if real["ok"] else "bad")
        rssi_ok, rtt_ok = rssi >= RSSI_MIN_DBM, rtt <= RTT_BUDGET_MS
        steps += [
            step("Signal strength", "band must be close: at least %d dBm" % RSSI_MIN_DBM, "%d dBm" % rssi,
                 "ok" if rssi_ok else "bad"),
            step("Round trip", "no relay: back within %d ms (direct ~%d ms)" % (RTT_BUDGET_MS, 2 * AIRTIME_MS + BAND_PROC_MS),
                 "%d ms" % rtt, "ok" if rtt_ok else "bad"),
        ]
        steps = settle(steps)
        ok = tag_ok and rssi_ok and rtt_ok
        self.dog_near = {"tag": tag_ok, "rssi": rssi, "rssiMin": RSSI_MIN_DBM, "rtt": rtt,
                         "rttMax": RTT_BUDGET_MS, "ok": ok, "relay": tag_ok and not rtt_ok}
        pkt = P.Packet.decode(raw)
        self.insp = insp("AUTO REPLY (dog, %s)" % ("verified" if ok else "rejected"),
                         "Band to dog at close range", raw, [
                             {"label": "tag + nonce", "value": "valid" if tag_ok else "INVALID", "tone": "ok" if tag_ok else "bad"},
                             {"label": "signal", "value": "%d dBm" % rssi, "tone": "ok" if rssi_ok else "bad"},
                             {"label": "round trip", "value": "%d ms (budget %d)" % (rtt, RTT_BUDGET_MS),
                              "tone": "ok" if rtt_ok else "bad"}])
        self._log("DOG", "RX reply: tag %s, %d dBm, round trip %d ms" % ("ok" if tag_ok else "bad", rssi, rtt),
                  "ok" if ok else "bad")
        v = self.view()
        verdict = ("NEAR VERIFIED: close and direct" if ok else
                   "TOO SLOW: relay suspected" if self.dog_near["relay"] else "REJECTED")
        v["trace"] = {"who": "DOG", "label": "verifies the reply up close", "steps": steps, "ok": ok, "verdict": verdict}
        return v

    def _linkup_quiet(self):
        """The channel went quiet: score the far or near stage."""
        lu = self.linkup
        if lu["stage"] == "far":
            if self.drone.feed == "FRIENDLY":
                self._lu("far_verified", "ok", "drone holds and keeps watch")
                if lu["scenario"] == "normal" and not (self.hw and not self.hw_band):
                    lu["stage"] = "alive"             # the wearer felt the buzz: I'M ALIVE next
                    self._lu("alive", "active")
                else:
                    self._lu("alive", "skip", "real band: press its button any time" if self.hw else "not in this scenario")
                    lu["stage"] = "approach_near"
                    self._lu("approach_near", "active")
            else:
                self._lu("far_verified", "bad", self.last_reject or "no reply")
                lu["stage"] = "hold"
                lu["verdict"] = "No verified reply at the far line. Treat the contact as unknown; the dog stays back."
                self._log("DRONE", "Link-up HOLD at far line: contact not verified", "bad")
                self._realdog("stop")
        elif lu["stage"] == "alive":
            if self.drone.feed == "ALIVE":
                self._lu("alive", "ok", "feed ALIVE, accepted once")
                self._log("DRONE", "Person confirmed I'M ALIVE; the dog goes in", "ok")
            else:
                self._lu("alive", "bad", self.last_reject or "no ALIVE")
            lu["stage"] = "approach_near"
            self._lu("approach_near", "active")
        elif lu["stage"] == "near":
            near = self.dog_near
            if near and near["ok"]:
                self._lu("near_verified", "ok", "%d dBm, %d ms" % (near["rssi"], near["rtt"]))
                lu["stage"] = "countersign"
                self._lu("countersign", "active")
                self._log("DOG", "Near recognition verified: close and direct", "ok")
            elif near and near["relay"]:
                self._lu("near_verified", "bad", "%d ms: too slow" % near["rtt"])
                lu["stage"] = "abort"
                lu["verdict"] = ("The reply was genuine but took %d ms, not ~%d. A relay must receive the whole "
                                 "packet before re-sending it, adding a full airtime each way. Whoever is in front "
                                 "of the dog is not wearing that band. Dog backs off."
                                 % (near["rtt"], 2 * AIRTIME_MS + BAND_PROC_MS))
                self._log("DOG", "Link-up ABORT: round trip too long, relay suspected", "bad")
                self._realdog("back")
            else:
                self._lu("near_verified", "bad", "no valid reply")
                lu["stage"] = "hold"
                lu["verdict"] = "The band did not answer the dog. Dog holds at the near line."
                self._log("DOG", "Link-up HOLD at near line: no valid reply", "bad")
                self._realdog("stop")

    def linkup_countersign(self):
        """Robot-to-human recognition: the dog flashes this hour's code on its eyes (the same
        code the band blinks), then starts the ultrasonic final approach."""
        lu = self.linkup
        if not lu or lu["stage"] != "countersign":
            return self.view()
        bits = list(P.blink_bits(self.key, self.clock()))
        lu["countersign"] = bits
        code = "".join(str(b) for b in bits)
        self._lu("countersign", "ok", " ".join("LONG" if b else "SHORT" for b in bits))
        self._log("DOG", "Countersign on the eyes: %s" % " ".join("LONG" if b else "SHORT" for b in bits), "ok")
        lu["stage"] = "closing"
        self._lu("closing", "active")
        self.close = {"t0": time.time() + COUNTERSIGN_S, "mm": None, "done": False, "contact": False,
                      "source": "real" if self.realdog else "simulated", "note": ""}
        if self.realdog:
            self.realdog.close_in(STOP_MM, CLOSE_TIMEOUT_S, self._sonar_report, countersign=code)
        return self.view()

    def _sonar_report(self, mm, done=False, contact=False, note=""):
        """Called from the real dog's thread with each ultrasonic reading."""
        c = getattr(self, "close", None)
        if c is None:
            return
        if mm is not None:
            c["mm"] = mm
        c["note"] = note or c["note"]
        if done:
            c["done"], c["contact"] = True, contact

    def _close_state(self):
        c = getattr(self, "close", None)
        if not c:
            return None
        if c["source"] == "simulated" and not c["done"]:
            mm = int(SONAR_START_MM - SIM_CLOSE_MM_S * max(0.0, time.time() - c["t0"]))
            c["mm"] = max(STOP_MM, mm)
            if mm <= STOP_MM:
                c["done"], c["contact"] = True, True
        return {"mm": c["mm"], "stopMm": STOP_MM, "startMm": SONAR_START_MM, "done": c["done"],
                "contact": c["contact"], "source": c["source"], "note": c["note"]}

    def linkup_complete(self):
        lu = self.linkup
        if not lu or lu["stage"] != "closing":
            return self.view()
        c = self._close_state()
        if not c["done"]:
            return self.view()                    # still closing in
        if not c["contact"]:
            self._lu("closing", "bad", c["note"] or "no contact")
            lu["stage"] = "hold"
            lu["verdict"] = "The ultrasonic sensor found nobody ahead. Dog holds; re-check before closing in."
            self._log("DOG", "Link-up HOLD: no contact on the ultrasonic sensor", "bad")
            self._realdog("stop")
            return self.view()
        self._lu("closing", "ok", "stopped at %d cm%s" % (c["mm"] // 10, " (%s)" % c["note"] if c["note"] else ""))
        if self.realdog and "code" in self.realdog.skipped:   # e.g. Bluetooth to the factory program
            st = lu["steps"]["countersign"]
            st["note"] = st["note"] + " (screen only: not on the real dog)"
        self._lu("linkup", "ok", "dog at the person")
        lu["stage"] = "complete"
        lu["verdict"] = ("Recognized twice: by the drone at 210 m and by the dog's fast close-range exchange. "
                         "The dog showed the countersign and stopped at arm's length on its ultrasonic sensor.")
        self._log("DOG", "LINKED UP", "ok")
        self._realdog("handshake")
        return self.view()

    # ---- optional real MechDog (python3 -m rescue.console --dog <ip>) ----------

    def dog_walk(self, ms):
        """The UI animates the dog walking for ``ms``; the real dog walks forward meanwhile."""
        if self.realdog:
            self.realdog.walk(60, min(int(ms or 0), 6000))
        return self.view()

    def _realdog(self, what):
        if not self.realdog:
            return
        if what == "stop":
            self.realdog.stop()
        elif what == "back":
            self.realdog.walk(-50, 1500)
        elif what == "handshake":
            self.realdog.then("action handshake")

    def _eyes_follow(self):
        if self.realdog and self.linkup:
            color = EYES.get(self.linkup["stage"], "off")
            if color != getattr(self, "_eyes_now", None):
                self._eyes_now = color
                self.realdog.eyes(*EYE_RGB[color])

    def _tello_follow(self):
        """Fly the Tello through the link-up, once per stage change. Never blocks."""
        if not self.tello:
            return
        stage = self.linkup["stage"] if self.linkup else None
        prev, self._tello_stage = self._tello_stage, stage
        if stage == prev:
            return
        if stage == "approach_far" and prev in (None, "complete", "hold", "abort"):
            self.tello.takeoff_and_advance(TELLO_ADVANCE_CM)      # fly to the far line
        elif stage == "complete":
            self.tello.land()
        elif stage in ("hold", "abort"):
            self.tello.plan("wait 3", "land")                     # hover in place, then land
        elif stage is None and prev is not None and self.tello.flying:
            self.tello.land()                                     # reset

    def drone_land(self):
        if self.tello:
            self.tello.land()
        return self.view()

    def drone_emergency(self):
        if self.tello:
            self.tello.emergency()
            self._log("DRONE", "EMERGENCY STOP: motors off", "bad")
        return self.view()

    def _linkup_running(self):
        return bool(self.linkup) and self.linkup["stage"] not in ("complete", "hold", "abort")

    def linkup_view(self):
        lu = self.linkup
        self._eyes_follow()
        risk, caption, command, tone = RISK[lu["stage"]]
        return {
            "scenario": lu["scenario"], "name": SCENARIOS[lu["scenario"]][0], "desc": SCENARIOS[lu["scenario"]][1],
            "stage": lu["stage"], "running": self._linkup_running(),
            "risk": risk, "caption": lu["verdict"] or caption, "command": command, "tone": tone,
            "near": self.dog_near,
            "countersign": lu.get("countersign"),
            "eyes": EYES.get(lu["stage"], "off"),
            "sonar": self._close_state() if lu["stage"] in ("closing", "complete", "hold") else None,
            "steps": [dict(id=sid, label=label, detail=detail, **lu["steps"][sid])
                      for sid, label, detail in linkup_steps()],
        }

    # ---- view ------------------------------------------------------------

    def _can_alive(self):
        return self.band.last is not None and self.band.last[0] != self.pressed_nonce

    def view(self):
        self._tello_follow()
        feed = self.drone.feed
        pending = self.drone.out is not None and self.drone.ex is None and feed == "UNKNOWN"
        if feed == "FRIENDLY":
            caption = "Challenge answered, tag verified"
        elif feed == "ALIVE":
            caption = "Friendly, and the person pressed I'm alive"
        elif self.last_reject:
            caption = "Last packet rejected: " + self.last_reject
        elif pending:
            caption = "Challenge sent, waiting for a verified reply"
        else:
            caption = "No verified exchange"
        slot = self.clock()
        return {
            "feed": feed, "caption": caption, "busy": self._busy(), "flight": self.flight,
            "outNonce": self.drone.out.hex() if self.drone.out else "none",
            "exchanges": self.exchanges, "rejects": self.rejects,
            "slot": slot, "utc": time.strftime("%H:%MZ", time.gmtime()),
            "link": {k: LINK[k] for k in ("id", "short", "radio", "band", "up")}, "hop": self.hop_view(),
            "blink": P.blink_bits(self.key, slot),
            "canAlive": self._can_alive(), "replayReady": self.captured is not None,
            "bandStatus": self.band_status, "insp": self.insp,
            "log": list(reversed(self.log)),
            "linkup": self.linkup_view() if self.linkup else None,
            "hw": {"port": self.hw.port, "hello": self.hw.hello or "no banner",
                   "mode": ("band over Bluetooth" if str(self.hw.port).startswith("ble:") else "band over USB")
                   if self.hw_band else "SDR (%s)" % LINK["short"] if self.hw_sdr else "dongle (%s)" % LINK["short"]} if self.hw else None,
            "bandPicker": {"open": self.band_picker_open, "error": self.band_error},
            "dog": {"host": self.realdog.host, "last": self.realdog.last, "blocked": self.realdog.blocked,
                    "skipped": sorted(self.realdog.skipped), "battery": self.realdog.battery()} if self.realdog else None,
            "dogPicker": {"open": self.dog_picker_open, "error": self.dog_error},
            "tello": self.tello.status() if self.tello else None,
            "hwPending": (len(self.hw_unsolicited) + len(self.hw.packets)) if self.hw and not self._busy() else 0,
        }

    def hop_view(self):
        """FSK link only: the live hop channel from the keyed schedule and the laptop clock."""
        if LINK["id"] != "fsk":
            return None
        try:
            from .hop import HopSchedule
            if getattr(self, "_hop", None) is None:
                self._hop = HopSchedule(self.key)
            sched, now_ms = self._hop, time.time() * 1000.0
            slot = sched.slot(now_ms)
            ch = sched.channel(slot)
            link = sched.link
            return {"channel": ch, "channels": link.channels, "mhz": round(link.freq_hz(ch) / 1e6, 2), "slot": slot,
                    "nextIn": round((link.dwell_ms - now_ms % link.dwell_ms) / 1000.0, 1), "dwell": link.dwell_ms / 1000.0}
        except (OSError, ValueError):                     # firmware/include/config.h not found
            return None

    # ---- dog picker ----------------------------------------------------------------

    def dog_scan(self):
        """Nearby Bluetooth devices for the picker (slow: call without holding the lock)."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dog"))
        try:
            from ble_link import scan_devices
            return {"devices": scan_devices(5.0), "error": None}
        except ImportError:
            return {"devices": [], "error": "Bluetooth needs the bleak package. Start the console with "
                                            ".venv/bin/python -m rescue.console (see requirements.txt)."}
        except Exception as e:
            return {"devices": [], "error": "Bluetooth scan failed: %s" % e}

    def dog_connect(self, target):
        """Connect to a dog: a Bluetooth name/address, or a Wi-Fi IP (slow part outside the lock)."""
        target = (target or "").strip()
        is_ip = target.count(".") == 3 and all(x.isdigit() for x in target.split("."))
        spec = target if is_ip else "ble:" + target
        try:
            rd = self.make_dog(spec)
            pong = rd.cmd("ping")
            if not pong.startswith("ok"):
                rd.close()
                raise ConnectionError("no answer from %s (%s)" % (target, pong))
        except BaseException as e:            # SystemExit from a missing bleak too
            with self.lock:
                self.dog_error = str(e) or e.__class__.__name__
                self.dog_picker_open = True
                return self.view()
        with self.lock:
            if self.realdog:
                self.realdog.close()
            self.realdog, self.dog_error, self.dog_picker_open = rd, None, False
            self._eyes_now = None
            self._log("DOG", "Connected to %s" % rd.host, "ok")
            return self.view()

    # ---- wristband over Bluetooth ---------------------------------------------------

    def _set_link(self, link):
        """Swap the hardware link at runtime (Bluetooth band connect/disconnect)."""
        self.hw = link
        self.hw_band = bool(link and (link.hello or "").startswith("#HELLO band"))
        self.hw_sdr = bool(link and (link.hello or "").startswith("#HELLO sdr"))
        self.hw_unsolicited = []
        self.reset()

    def band_scan(self):
        """Nearby Bluetooth devices, wristbands first (slow: call without holding the lock)."""
        try:
            from .ble_band import scan_bands
            return {"devices": scan_bands(5.0), "error": None}
        except ImportError:
            return {"devices": [], "error": "Bluetooth needs the bleak package. Start the console with "
                                            ".venv/bin/python -m rescue.console (see requirements.txt)."}
        except Exception as e:
            return {"devices": [], "error": "Bluetooth scan failed: %s" % e}

    def band_connect(self, target):
        """Connect to the wristband over Bluetooth by name or address (slow part outside the lock)."""
        target = (target or "").strip() or None
        try:
            radio = self.make_band(target)
            if not (radio.hello or "").startswith("#HELLO band"):
                radio.close()
                raise ConnectionError("connected, but it did not answer as a band (%s)" % (radio.hello or "no reply"))
        except BaseException as e:            # SystemExit from a missing bleak too
            with self.lock:
                self.band_error = str(e) or e.__class__.__name__
                self.band_picker_open = True
                return self.view()
        with self.lock:
            if self.hw:
                self.hw.close()
            self._set_link(radio)
            self.band_error, self.band_picker_open = None, False
            self._log("HW", "Wristband connected over Bluetooth: %s" % radio.port, "ok")
            return self.view()

    def band_disconnect(self):
        with self.lock:
            if self.hw:
                self.hw.close()
                self._log("HW", "Disconnected from %s" % self.hw.port)
            self._set_link(None)
            return self.view()

    def dog_disconnect(self):
        with self.lock:
            if self.realdog:
                self.realdog.close()
                self._log("DOG", "Disconnected from %s" % self.realdog.host)
            self.realdog = None
            return self.view()

    FREE_PLAY = ("challenge", "spoof", "replay", "press", "incoming")
    ACTIONS = FREE_PLAY + ("deliver", "reset", "linkup_start", "linkup_far", "linkup_alive", "linkup_near",
                           "linkup_countersign", "linkup_complete", "dog_walk")

    def act(self, name, arg=None):
        with self.lock:
            if name in self.FREE_PLAY and self._linkup_running():
                return self.view()                # free play waits until the link-up finishes
            if name == "linkup_start":
                return self.linkup_start(arg)
            if name == "dog_walk":
                return self.dog_walk(arg)
            return getattr(self, name)()


def ble_band_radio(target=None):
    """SerialLoRaRadio over Bluetooth to the wristband (same framing as USB)."""
    from .ble_band import BleStream
    from .radio import SerialLoRaRadio
    stream = BleStream(target)
    radio = SerialLoRaRadio(stream=stream, boot_wait=0.3)   # no reset on connect: it answers "#?"
    radio.port = "ble:%s" % (stream.name or stream.address)
    return radio


class RealDog:
    """The physical MechDog, over Wi-Fi via dog/dogctl.py. Never blocks the console:
    commands run on a background thread, and errors are recorded, not raised."""

    def __init__(self, host, port=9000, link=None):
        """``host``: the dog's IP (Wi-Fi server), or "ble" / "ble:<name or address>" for
        Bluetooth to the factory program. ``link``: an already-built Dog-like object (tests)."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dog"))
        if link is not None:
            dog = link
        elif host == "ble" or host.startswith("ble:"):
            from ble_link import BleDog
            dog = BleDog(host[4:] or None)
            host = dog.host
        else:
            from dogctl import Dog
            dog = Dog(host, port, timeout=0.5)
        self.host, self.dog, self.last = host, dog, "not contacted"
        self.skipped = set()                      # features this link can't do (factory protocol)
        self.blocked = None                       # last obstacle (mm) that stopped a forward walk
        self.battery_mv, self.battery_t = None, 0.0
        self._closed = False
        self._job = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._battery_loop, daemon=True).start()   # needs the lock above

    BATTERY_EVERY_S = 15
    BATTERY_EMPTY_MV, BATTERY_FULL_MV, BATTERY_LOW_MV = 6800, 8400, 7000   # 2-cell lithium

    def _battery_loop(self):
        while not self._closed:
            reply = self.cmd("battery", timeout=1.5)
            if reply.startswith("ok battery"):
                try:
                    self.battery_mv, self.battery_t = int(reply.split()[2]), time.time()
                except (IndexError, ValueError):
                    pass
            time.sleep(self.BATTERY_EVERY_S)

    def battery(self):
        if self.battery_mv is None:
            return None
        mv = self.battery_mv
        pct = (mv - self.BATTERY_EMPTY_MV) * 100 // (self.BATTERY_FULL_MV - self.BATTERY_EMPTY_MV)
        return {"mv": mv, "pct": max(0, min(100, pct)), "low": mv < self.BATTERY_LOW_MV,
                "age": int(time.time() - self.battery_t)}

    def close(self):
        self._closed = True
        self._job += 1
        self.cmd("stop")
        closer = getattr(self.dog, "close", None)
        if closer:
            try:
                closer()
            except Exception:
                pass

    def cmd(self, line, timeout=0.5):
        with self._lock:                          # one socket, several threads
            try:
                self.last = self.dog.send(line, retries=0, timeout=timeout)
            except Exception as e:
                self.last = "no reply (%s)" % e.__class__.__name__
            if self.last.startswith("err unsupported"):
                self.skipped.add(line.split()[0] + (" " + line.split()[1] if line.startswith("action") else ""))
        return self.last

    def eyes(self, r, g, b):
        """Status colour on the ultrasonic module's LEDs. Does not interrupt walking."""
        threading.Thread(target=self.cmd, args=("rgb %d %d %d" % (r, g, b),), daemon=True).start()

    def close_in(self, stop_mm, timeout_s, report, countersign=None):
        """Flash the countersign on the eyes (the dog times it itself, ~1.6 s), then walk forward
        slowly, reading the ultrasonic sensor, until something is within stop_mm."""
        def go(job):
            if countersign:
                self.cmd("stop")
                self.cmd("code %s 0 255 0" % countersign, timeout=3.0)   # skipped if unsupported
            if self.cmd("sonar").startswith("err"):   # no sensor on this dog: short timed walk
                self.cmd("move 40 0"); time.sleep(1.5); self.cmd("stop")
                report(stop_mm, done=True, contact=True, note="no ultrasonic, timed walk")
                return
            end = time.time() + timeout_s
            near, lost, last = False, 0, None          # near: we have seen something close already
            while time.time() < end and job == self._job:
                mm = self.sonar_median(3)             # the gait wobbles the sensor: filter it
                if mm is None:
                    if not near:                      # no echo, nothing seen yet: keep walking
                        report(None)
                        self.cmd("move 40 0")
                        time.sleep(0.2)
                        continue
                    lost += 1                         # echo lost up close: never speed up again
                    self.cmd("stop")
                    if lost >= 4:                     # still lost: stop for good, say why
                        report(last, done=True, contact=True,
                               note="echo lost at ~%d cm, stopped for safety" % (last // 10))
                        return
                    time.sleep(0.35)
                    continue
                lost, last = 0, mm
                if mm <= stop_mm * self.SLOW_ZONE:
                    near = True
                if mm <= stop_mm:
                    self.cmd("stop")
                    report(mm, done=True, contact=True)
                    return
                report(mm)
                if near:                              # last stretch: short steps, stop, re-measure,
                    self.cmd("move 40 0")             # because the factory gait has one fast speed
                    time.sleep(self.STEP_S)
                    self.cmd("stop")
                    time.sleep(0.35)                  # let it settle before the next reading
                    continue
                self.cmd("move 40 0")
                time.sleep(0.2)
            if job == self._job:
                self.cmd("stop")
                report(None, done=True, contact=False, note="nothing within range")
        self._run(go)

    def _run(self, fn):
        self._job += 1
        job = self._job
        threading.Thread(target=fn, args=(job,), daemon=True).start()

    OBSTACLE_MM = 300   # forward walks stop if the ultrasonic sees anything this close
    STEP_S = 0.4        # one short step on the final approach (measured on a real MechDog)
    SLOW_ZONE = 2.0     # short steps once within SLOW_ZONE x the stop distance

    def sonar_mm(self):
        """One ultrasonic reading in mm, or None (no sensor, no echo, no reply)."""
        reply = self.cmd("sonar")
        try:
            mm = int(reply.split()[-1]) if reply.startswith("ok sonar") else None
        except ValueError:
            return None
        return None if mm is None or mm >= 4000 else mm   # 5000 = no echo

    def sonar_median(self, n=3):
        """Median of n readings, ignoring no-echo/no-reply: the gait wobbles the sensor."""
        vals = sorted(v for v in (self.sonar_mm() for _ in range(n)) if v is not None)
        return vals[len(vals) // 2] if vals else None

    def walk(self, step, ms):
        """Walk for ``ms``, re-sending the move so the dog's 1 s safety stop doesn't fire, then
        stop. Walking forward, it also stops early if the ultrasonic sees an obstacle."""
        def go(job):
            end = time.time() + ms / 1000.0
            while time.time() < end and job == self._job:
                if step > 0:
                    mm = self.sonar_mm()
                    if mm is not None and mm <= self.OBSTACLE_MM:
                        self.blocked = mm
                        break
                self.cmd("move %d 0" % step)
                time.sleep(0.3)
            if job == self._job:
                self.cmd("stop")
        self._run(go)

    def stop(self):
        self._run(lambda job: self.cmd("stop"))

    def then(self, line):
        self._run(lambda job: (self.cmd("stop"), self.cmd(line)))


def make_server(console: Console, host="127.0.0.1", port=8011) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                return self._send(200, render_html(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                with console.lock:
                    return self._send(200, console.view())
            if self.path == "/api/dog/scan":
                return self._send(200, console.dog_scan())
            if self.path == "/api/band/scan":
                return self._send(200, console.band_scan())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            parts = self.path.split("?")[0].split("/")[2:] if self.path.startswith("/api/") else []
            if parts[:1] == ["drone"] and len(parts) == 2 and parts[1] in ("land", "emergency"):
                if parts[1] == "emergency":               # never wait for the console lock
                    return self._send(200, console.drone_emergency())
                with console.lock:
                    return self._send(200, console.drone_land())
            if parts[:1] == ["band"]:
                from urllib.parse import unquote
                if len(parts) >= 2 and parts[1] == "connect":
                    return self._send(200, console.band_connect(unquote("/".join(parts[2:]))))
                if len(parts) == 2 and parts[1] == "disconnect":
                    return self._send(200, console.band_disconnect())
                if len(parts) == 2 and parts[1] == "picker":
                    with console.lock:
                        console.band_picker_open = False
                        return self._send(200, console.view())
                return self._send(404, {"error": "not found"})
            if parts[:1] == ["dog"]:
                from urllib.parse import unquote
                if len(parts) >= 3 and parts[1] == "connect":
                    return self._send(200, console.dog_connect(unquote("/".join(parts[2:]))))
                if len(parts) == 2 and parts[1] == "disconnect":
                    return self._send(200, console.dog_disconnect())
                if len(parts) == 2 and parts[1] == "picker":   # browser closed or opened it
                    with console.lock:
                        console.dog_picker_open = False
                        return self._send(200, console.view())
                return self._send(404, {"error": "not found"})
            if not parts or parts[0] not in Console.ACTIONS:
                return self._send(404, {"error": "not found"})
            self._send(200, console.act(parts[0], parts[1] if len(parts) > 1 else None))

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main():
    ap = argparse.ArgumentParser(description="Rescue band operator console (local web UI)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--serial", metavar="PORT",
                    help="drive real hardware: the ESP32 band (USB test mode) or a radio dongle, "
                         "e.g. /dev/cu.usbserial-0001 (needs pyserial)")
    ap.add_argument("--dog", metavar="HOST",
                    help="also drive the real MechDog during the link-up: its Wi-Fi IP (e.g. 192.168.4.1) "
                         "or 'ble' / 'ble:<name>' for Bluetooth to the factory program (see dog/README.md)")
    ap.add_argument("--drone", metavar="tello",
                    help="fly a Ryze Tello through the link-up (join its TELLO-XXXXXX Wi-Fi first)")
    ap.add_argument("--band", metavar="ble",
                    help="connect the wristband over Bluetooth: 'ble' (first Rescue-Band found) or 'ble:<name>'")
    ap.add_argument("--sdr", action="store_true",
                    help="use an SDR as the drone's radio (start sdr/sdr_fsk_bridge.py, or sdr/sdr_bridge.py "
                         "with --link lora, first); the dog's Feather then does the near check for real")
    ap.add_argument("--link", choices=sorted(LINKS), default="fsk",
                    help="which radio link the hardware speaks: fsk (default: RFM69 / SX1276 FSK, 50-channel hopping) "
                         "or lora (the earlier LoRa link); changes the wording, timings and near-check numbers")
    args = ap.parse_args()
    set_link(args.link)
    realdog, dog_error = None, None
    if args.dog:
        try:
            realdog = RealDog(args.dog)
            print("MechDog at", args.dog, "->", realdog.cmd("ping"))
        except BaseException as e:            # not found, Bluetooth off, or bleak missing
            dog_error = "Could not connect to %s: %s" % (args.dog, str(e) or e.__class__.__name__)
            print(dog_error, "- pick a dog in the browser")
    link = None
    band_error = None
    if args.band:
        try:
            link = ble_band_radio(args.band[4:] if args.band.startswith("ble:") else None)
            print("Wristband over Bluetooth ->", link.port, link.hello)
        except BaseException as e:
            band_error = "Could not connect the wristband: %s" % (str(e) or e.__class__.__name__)
            print(band_error, "- pick it in the browser")
    elif args.sdr:
        from .radio import SdrRadio
        link = SdrRadio()
        print("SDR bridge ->", link.hello or "not answering: start sdr/sdr_fsk_bridge.py (or sdr/sdr_bridge.py for LoRa) first")
    elif args.serial:
        from .radio import SerialLoRaRadio
        link = SerialLoRaRadio(args.serial)
        print("Hardware on", args.serial, "->", link.hello or "no boot banner (is the firmware flashed?)")
    tello = None
    if args.drone:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "drone"))
        from tello import Tello
        tello = Tello()
        if tello.connect():
            print("Tello ->", "battery %s%%" % tello.battery)
        else:
            print("Tello not answering (is the Mac on the TELLO-XXXXXX Wi-Fi?) - running without it")
            tello.close()
            tello = None
    console = Console(link=link, realdog=realdog, tello=tello)
    if dog_error:
        console.dog_error, console.dog_picker_open = dog_error, True
    if band_error:
        console.band_error, console.band_picker_open = band_error, True
    server = make_server(console, args.host, args.port)
    url = "http://%s:%d/" % (args.host, server.server_address[1])
    print("Rescue band console running at", url, "(Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
