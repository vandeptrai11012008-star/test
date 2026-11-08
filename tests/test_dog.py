"""MechDog command server logic and the laptop client, without the dog."""
import os
import sys
import threading
import time

ROOT = os.path.join(os.path.dirname(__file__), "..", "dog")
sys.path.insert(0, os.path.join(ROOT, "onboard"))
sys.path.insert(0, ROOT)

import dogcmd  # noqa: E402
from dogcmd import DogCommands  # noqa: E402
from dogctl import Dog  # noqa: E402
from sim_dog import FakeMechDog, FakeSonar, serve  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def rig():
    clock, dog = Clock(), FakeMechDog(quiet=True)
    return DogCommands(dog, FakeSonar(), None, clock=clock), dog, clock


def test_move_is_clamped_and_reaches_the_dog():
    c, dog, _ = rig()
    assert c.handle("move 500 -90") == "ok move 100 -45"
    assert dog.calls[-1] == ("move", 100, -45)


def test_deadman_stops_a_walking_dog():
    c, dog, clock = rig()
    c.handle("move 60 0")
    clock.t += 0.5
    assert c.tick() is None                      # still within 1 s
    clock.t += dogcmd.DEADMAN_S
    assert c.tick() == "deadman stop" and dog.calls[-1] == ("move", 0, 0)
    assert c.tick() is None                      # stops once, not repeatedly


def test_actions_and_bad_input():
    c, dog, _ = rig()
    assert c.handle("action sit_dowm") == "ok action sit_dowm"
    assert dog.calls[-1] == ("action_run", "sit_dowm")
    assert c.handle("action ../evil").startswith("err")
    assert c.handle("move fast").startswith("err bad arguments")
    assert c.handle("fly").startswith("err unknown")
    assert c.handle("beep") == "err no buzzer"
    assert c.handle("sonar").startswith("ok sonar ")


def test_laptop_client_drives_sim_dog_over_udp():
    started = threading.Event()
    box = {}

    def ready(cmds, port):
        box["cmds"], box["port"] = cmds, port
        started.set()

    threading.Thread(target=serve, kwargs=dict(port=0, quiet=True, ready=ready), daemon=True).start()
    assert started.wait(2)
    dog = Dog("127.0.0.1", box["port"], timeout=0.5)
    assert dog.send("ping") == "ok pong mechdog"
    assert dog.move(60, 10) == "ok move 60 10"
    assert dog.send("status") == "ok status moving=1 step=60 turn=10"
    time.sleep(dogcmd.DEADMAN_S + 0.3)           # laptop goes quiet: the dog stops itself
    assert dog.send("status") == "ok status moving=0 step=0 turn=0"


def test_code_command_flashes_the_countersign():
    c, dog, _ = rig()
    flashes = []
    c.sonar.setRGB = lambda which, r, g, b: flashes.append((r, g, b))
    c.sleep = lambda s: flashes.append(s)
    assert c.handle("code 101") == "ok code 101"
    assert [f for f in flashes if isinstance(f, float)] == [0.45, 0.25, 0.15, 0.25, 0.45, 0.25]
    assert c.handle("code 12x").startswith("err")


def test_console_drives_sim_dog_through_whole_linkup(monkeypatch):
    """RealDog over UDP against the simulated MechDog: walk, countersign on the eyes,
    ultrasonic close-in until 30 cm, handshake."""
    import rescue.console as cm
    started, box = threading.Event(), {}

    def ready(cmds, port):
        box["cmds"], box["port"] = cmds, port
        started.set()

    threading.Thread(target=serve, kwargs=dict(port=0, quiet=True, ready=ready), daemon=True).start()
    assert started.wait(2)
    box["cmds"].sleep = lambda s: None                      # skip the real blink timing
    c = cm.Console(realdog=cm.RealDog("127.0.0.1", box["port"]))
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
    assert v["linkup"]["stage"] == "countersign"
    c.act("linkup_countersign")
    for _ in range(150):
        v = c.act("linkup_complete")
        if v["linkup"]["stage"] != "closing":
            break
        time.sleep(0.05)
    assert v["linkup"]["stage"] == "complete", v["linkup"]
    sonar = v["linkup"]["sonar"]
    assert sonar["source"] == "real" and sonar["contact"] and sonar["mm"] <= cm.STOP_MM
    time.sleep(0.3)
    calls = box["cmds"].dog.calls
    assert ("action_run", "handshake") in calls and ("move", 40, 0) in calls
