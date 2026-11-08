"""Command handling for the MechDog Wi-Fi server. Pure Python, so it runs both on the
dog (MicroPython) and on the laptop (tests, sim_dog.py) with a fake robot.

One command per UDP datagram, plain text. Every command gets one reply line:
"ok ..." or "err ...".

    ping                          -> ok pong mechdog
    move <step_mm> <turn_deg>     walk: step -100..100 (negative = back), turn -45..45 (positive = left)
    stop                          stop walking
    stand                         default standing pose
    action <name>                 built-in action group, e.g. sit_dowm, handshake, go_prone
    pose <x> <y> <z> <roll> <pitch> <yaw> [ms]   body pose (mm, degrees, duration)
    balance on|off                self-balancing using the IMU
    sonar                         -> ok sonar <mm>   (needs the ultrasonic module)
    rgb <r> <g> <b>               ultrasonic module LEDs, 0..255
    beep [hz] [ms]                buzzer
    code <bits> [r g b]           flash a 3-pulse code on the eyes, e.g. "code 101": 1 = LONG 450 ms,
                                  0 = SHORT 150 ms (the band's hourly blink code, used as a countersign)
    status                        -> ok status moving=<0|1> step=<mm> turn=<deg>
    battery                       -> ok battery <mV>

Safety: while walking, if no command arrives for DEADMAN_S seconds the dog stops.
Keep sending "move" (the laptop tool repeats it) to keep walking.
"""

import time

DEADMAN_S = 1.0
MAX_STEP, MAX_TURN = 100, 45
NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class DogCommands:
    """``dog``: the HW_MechDog.MechDog object (or a fake). ``sonar``, ``buzzer``: optional."""

    def __init__(self, dog, sonar=None, buzzer=None, clock=None, sleep=None, battery=None):
        self.dog, self.sonar, self.buzzer = dog, sonar, buzzer
        self.battery = battery                  # callable returning millivolts (Hiwonder.Battery_power)
        self.clock = clock
        self.sleep = sleep or time.sleep
        self.step = self.turn = 0
        self.last_cmd = 0.0

    def now(self):
        return self.clock()

    def handle(self, line):
        """Run one command line, return the reply line."""
        self.last_cmd = self.now()
        parts = line.strip().split()
        if not parts:
            return "err empty"
        cmd, args = parts[0].lower(), parts[1:]
        try:
            fn = getattr(self, "cmd_" + cmd)
        except AttributeError:
            return "err unknown command " + cmd
        try:
            return fn(args)
        except (ValueError, IndexError):
            return "err bad arguments for " + cmd
        except Exception as e:  # hardware error: report, keep serving
            return "err %s failed: %s" % (cmd, e)

    def tick(self):
        """Call often. Stops the dog if the laptop went quiet while it was walking."""
        if (self.step or self.turn) and self.now() - self.last_cmd > DEADMAN_S:
            self._walk(0, 0)
            return "deadman stop"
        return None

    def _walk(self, step, turn):
        self.step, self.turn = step, turn
        self.dog.move(step, turn)

    # ---- commands ------------------------------------------------------------
    def cmd_ping(self, a):
        return "ok pong mechdog"

    def cmd_move(self, a):
        step = clamp(int(float(a[0])), -MAX_STEP, MAX_STEP)
        turn = clamp(int(float(a[1])) if len(a) > 1 else 0, -MAX_TURN, MAX_TURN)
        self._walk(step, turn)
        return "ok move %d %d" % (step, turn)

    def cmd_stop(self, a):
        self._walk(0, 0)
        return "ok stop"

    def cmd_stand(self, a):
        self._walk(0, 0)
        self.dog.set_default_pose()
        return "ok stand"

    def cmd_action(self, a):
        name = a[0]
        if not all(c in NAME_CHARS for c in name):   # MicroPython str has no isalnum()
            return "err bad action name"
        self._walk(0, 0)
        self.dog.action_run(name)
        return "ok action " + name

    def cmd_pose(self, a):
        x, y, z, roll, pitch, yaw = [clamp(float(v), -30, 30) for v in a[:6]]
        ms = int(a[6]) if len(a) > 6 else 500
        self.dog.set_pose([x, y, z], [roll, pitch, yaw], clamp(ms, 100, 5000))
        return "ok pose"

    def cmd_balance(self, a):
        on = a[0].lower() in ("on", "1", "true")
        self.dog.homeostasis(on)
        return "ok balance " + ("on" if on else "off")

    def cmd_sonar(self, a):
        if self.sonar is None:
            return "err no ultrasonic module"
        return "ok sonar %d" % self.sonar.getDistance()

    def cmd_rgb(self, a):
        if self.sonar is None:
            return "err no ultrasonic module"
        r, g, b = [clamp(int(v), 0, 255) for v in a[:3]]
        self.sonar.setRGB(0, r, g, b)
        return "ok rgb %d %d %d" % (r, g, b)

    def cmd_beep(self, a):
        if self.buzzer is None:
            return "err no buzzer"
        hz = clamp(int(a[0]), 100, 5000) if a else 800
        ms = clamp(int(a[1]), 20, 2000) if len(a) > 1 else 120
        self.buzzer.playTone(hz, ms, True)
        return "ok beep"

    def cmd_code(self, a):
        bits = a[0]
        if len(bits) != 3 or any(c not in "01" for c in bits):
            return "err code needs 3 bits like 101"
        if self.sonar is None:
            return "err no ultrasonic module"
        self._walk(0, 0)
        r, g, b = [clamp(int(v), 0, 255) for v in a[1:4]] if len(a) >= 4 else (0, 255, 0)
        for bit in bits:                             # same timing as the band's LED
            self.sonar.setRGB(0, r, g, b)
            self.sleep(0.45 if bit == "1" else 0.15)
            self.sonar.setRGB(0, 0, 0, 0)
            self.sleep(0.25)
        return "ok code " + bits

    def cmd_battery(self, a):
        if self.battery is None:
            return "err no battery reading"
        return "ok battery %d mV" % int(self.battery())

    def cmd_status(self, a):
        return "ok status moving=%d step=%d turn=%d" % (1 if (self.step or self.turn) else 0, self.step, self.turn)
