"""A pretend MechDog on the laptop: the same command code (onboard/dogcmd.py) the real
dog runs, with a fake robot that prints what it would do. Use it to try dogctl.py:

    python3 dog/sim_dog.py                       # listens on 127.0.0.1:9000
    python3 dog/dogctl.py --host 127.0.0.1 drive
"""
import argparse
import os
import random
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "onboard"))
from dogcmd import DogCommands  # noqa: E402


class FakeMechDog:
    """Stands in for HW_MechDog.MechDog. Records calls so tests can check them."""

    def __init__(self, quiet=False):
        self.calls, self.quiet = [], quiet

    def _do(self, *call):
        self.calls.append(call)
        if not self.quiet:
            print("  dog:", *call)

    def move(self, step, turn):
        self._do("move", step, turn)

    def set_default_pose(self):
        self._do("set_default_pose")

    def action_run(self, name):
        self._do("action_run", name)

    def set_pose(self, pos, rot, ms):
        self._do("set_pose", pos, rot, ms)

    def homeostasis(self, on):
        self._do("homeostasis", on)


class FakeSonar:
    """Distance shrinks while the fake dog walks forward, like walking up to a person."""

    def __init__(self, dog=None, quiet=True):
        self.dog, self.quiet, self.mm, self.rgb = dog, quiet, 1500, (0, 0, 0)
        self._seen = 0

    def getDistance(self):
        if self.dog:                                  # each forward step since the last reading closes a little
            new, self._seen = self.dog.calls[self._seen:], len(self.dog.calls)
            for call in new:
                if call[0] == "move" and call[1] > 0:
                    self.mm = max(120, self.mm - 3 * call[1])
        return self.mm if self.dog else random.randint(150, 900)

    def setRGB(self, which, r, g, b):
        self.rgb = (r, g, b)
        if not self.quiet:
            print("  eyes:", r, g, b)


class FakeBuzzer:
    def playTone(self, hz, ms, wait):
        pass


def serve(host="127.0.0.1", port=9000, quiet=False, ready=None):
    dog = FakeMechDog(quiet)
    cmds = DogCommands(dog, FakeSonar(dog, quiet), FakeBuzzer(), clock=time.time, battery=lambda: 7950)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    sock.settimeout(0.1)
    if ready:
        ready(cmds, sock.getsockname()[1])
    if not quiet:
        print("sim dog listening on %s:%d (Ctrl-C to stop)" % sock.getsockname())
    while True:
        try:
            data, addr = sock.recvfrom(256)
        except socket.timeout:
            data = None
        if data:
            sock.sendto(cmds.handle(data.decode()).encode(), addr)
        note = cmds.tick()
        if note and not quiet:
            print(" ", note)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Simulated MechDog")
    ap.add_argument("--port", type=int, default=9000)
    try:
        serve(port=ap.parse_args().port)
    except KeyboardInterrupt:
        pass
