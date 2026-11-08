"""Bluetooth path to the MechDog's factory program, with a fake transport (no radio)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dog"))

import factory_protocol as fp  # noqa: E402
from ble_link import BleDog, pick_channel  # noqa: E402

import rescue.console as cm  # noqa: E402


class FakeFactoryDog:
    """Acts like the factory program over BLE: records frames, answers CMD|4|1|$ etc."""

    def __init__(self):
        self.frames, self.on_data, self.mm, self.name = [], None, 1500, "MechDog"
        self.walking = False

    def connect(self, target):
        pass

    def write(self, data):
        frame = data.decode()
        self.frames.append(frame)
        if frame.startswith("CMD|3|") and frame != fp.STOP:
            self.walking = True
            self.mm = max(150, self.mm - 120)                   # each forward command closes in
        if frame in (fp.STOP, "CMD|1|5|$"):
            self.walking = False
        if frame == "CMD|4|1|$":
            self.on_data(("CMD|4|%d|$" % self.mm).encode())
        if frame == "CMD|6|$":
            self.on_data(b"CMD|6|7")
            self.on_data(b"812|$")

    def close(self):
        pass


def test_translate_motion_and_unsupported():
    assert fp.translate("move 60 0") == ("CMD|3|3|$", None)
    assert fp.translate("move 60 30")[0] == "CMD|3|4|$"      # large left forward
    assert fp.translate("move 60 -10")[0] == "CMD|3|1|$"     # small right forward
    assert fp.translate("move -50 0")[0] == "CMD|3|7|$"
    assert fp.translate("move 0 0")[0] == fp.STOP
    assert fp.translate("sonar") == ("CMD|4|1|$", 4)
    assert fp.translate("action sit_dowm")[0] == "CMD|2|1|4|$"
    assert fp.translate("action handshake")[0] == "CMD|2|1|7|$"       # from the factory action_list
    assert fp.translate("action moonwalk")[0].startswith("err unsupported action")
    assert fp.translate("rgb 0 255 0")[0] == "CMD|4|3|0|255|0|$"       # undocumented eye command
    assert fp.translate("beep")[0].startswith("err unsupported")


def test_frame_parser_handles_split_and_noise():
    frames, rest = fp.parse_frames("xxCMD|4|1234|$CMD|6|78")
    assert frames == [["4", "1234"]] and rest == "CMD|6|78"
    frames, rest = fp.parse_frames(rest + "12|$")
    assert frames == [["6", "7812"]] and rest == ""


def test_pick_channel_prefers_uart_service():
    services = [("00001800-0000-1000-8000-00805f9b34fb", [("2a00", ["read"])]),
                ("0000ffe0-0000-1000-8000-00805f9b34fb", [("0000ffe1-0000-1000-8000-00805f9b34fb",
                                                           ["read", "write-without-response", "notify"])])]
    assert pick_channel(services) == ("0000ffe1-0000-1000-8000-00805f9b34fb",) * 2


def test_bledog_sends_factory_frames_and_reads_replies():
    fake = FakeFactoryDog()
    dog = BleDog(transport=fake, timeout=0.5)
    assert dog.send("ping").startswith("ok pong")
    assert dog.move(60, 0).startswith("ok move")
    assert fake.frames[-1] == "CMD|3|3|$"
    assert dog.send("battery") == "ok battery 7812 mV"
    assert dog.send("sonar") == "ok sonar 1380"
    t0 = time.time()
    assert dog.send("code 101") == "ok code 101"
    assert 1.5 < time.time() - t0 < 2.5                                 # LONG SHORT LONG with gaps
    eyes = [f for f in fake.frames if f.startswith("CMD|4|3|")]
    assert eyes == ["CMD|4|3|0|255|0|$", "CMD|4|3|0|0|0|$"] * 3
    dog.move(60, 0)
    dog.send("action handshake")
    assert fake.frames[-2:] == [fp.STOP, "CMD|2|1|7|$"]                # stop first, then the action


def test_bledog_laptop_side_safety_stop():
    fake = FakeFactoryDog()
    dog = BleDog(transport=fake)
    dog.move(60, 0)
    time.sleep(BleDog.DEADMAN_S + 0.5)
    assert fake.frames[-1] == fp.STOP and not dog.moving


def test_linkup_over_bluetooth_skips_what_factory_lacks(monkeypatch):
    fake = FakeFactoryDog()
    c = cm.Console(realdog=cm.RealDog("ble", link=BleDog(transport=fake, timeout=0.5)))
    c.act("linkup_start", "normal")
    v = c.act("linkup_far")
    while v["flight"]:
        v = c.act("deliver")
    v = c.act("linkup_alive")                               # the wearer presses I'M ALIVE
    while v["flight"]:
        v = c.act("deliver")
    v = c.act("linkup_near")
    while v["flight"]:
        v = c.act("deliver")
    c.act("linkup_countersign")
    for _ in range(200):
        v = c.act("linkup_complete")
        if v["linkup"]["stage"] != "closing":
            break
        time.sleep(0.05)
    assert v["linkup"]["stage"] == "complete", v["linkup"]
    assert v["linkup"]["sonar"]["source"] == "real" and v["linkup"]["sonar"]["mm"] <= cm.STOP_MM
    time.sleep(0.3)
    skipped = set(v["dog"]["skipped"]) | c.realdog.skipped
    assert not skipped, skipped                                         # eyes, countersign, handshake all work
    assert "CMD|2|1|7|$" in fake.frames                                 # handshake at link-up
    assert "CMD|4|3|0|255|0|$" in fake.frames                           # green eyes / countersign
    cs = next(st for st in v["linkup"]["steps"] if st["id"] == "countersign")
    assert "not on the real dog" not in cs["note"]
    assert "CMD|3|3|$" in fake.frames and "CMD|4|1|$" in fake.frames    # walked and read the ultrasonic


def test_forward_walk_stops_at_an_obstacle():
    fake = FakeFactoryDog()
    fake.mm = 200                                   # something 20 cm in front
    rd = cm.RealDog("ble", link=BleDog(transport=fake, timeout=0.5))
    rd.walk(60, 2000)
    time.sleep(0.6)
    assert rd.blocked == 200
    assert "CMD|3|3|$" not in fake.frames and fake.frames[-1] == fp.STOP


def test_no_echo_is_not_an_obstacle():
    fake = FakeFactoryDog()
    fake.mm = 5000
    rd = cm.RealDog("ble", link=BleDog(transport=fake, timeout=0.5))
    assert rd.sonar_mm() is None


def test_lost_echo_up_close_stops_instead_of_speeding_up():
    fake = FakeFactoryDog()
    fake.mm = 450                                   # inside the slow zone
    real_write = fake.write

    def write(data):                                # after the first reading the echo is lost
        real_write(data)
        if data == b"CMD|4|1|$" and len([f for f in fake.frames if f == "CMD|4|1|$"]) > 3:
            fake.mm = 5000
    fake.write = write
    rd = cm.RealDog("ble", link=BleDog(transport=fake, timeout=0.5))
    got = {}
    rd.close_in(300, 10, lambda mm, done=False, contact=False, note="": got.update(mm=mm, done=done, note=note) if done else None)
    for _ in range(100):
        if got.get("done"):
            break
        time.sleep(0.05)
    assert got["done"] and "echo lost" in got["note"]
    walks_after_loss = fake.frames[[i for i, f in enumerate(fake.frames) if f == "CMD|4|1|$"][4]:]
    assert "CMD|3|3|$" not in walks_after_loss                          # never walked on blind
