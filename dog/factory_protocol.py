"""Translate dogctl commands into the MechDog FACTORY protocol, so the laptop can drive the
dog over Bluetooth with nothing installed on it.

Frames are ASCII:  CMD|<function>|<data...>|$   (Hiwonder docs, "Serial Port Communication
Course"; the factory program parses the same CMD frames from the phone app over Bluetooth).

    1 posture   1 pitch +-1 | 2 roll +-1 | 3 self-balance 0/1 | 4 height +-1 | 5 normal posture
    2 action    1 <default group number> | 2 <custom group number>
    3 motion    1 small right fwd | 2 large right fwd | 3 forward | 4 large left fwd
                5 small left fwd  | 6 large left back | 7 backward | 8 large right back
    4 ultrasonic 1 = read distance (reply CMD|4|<mm>|$) | 2 obstacle avoidance 0/1
                3 <r> <g> <b> = eye colour (RGB on the ultrasonic module; undocumented,
                found in the factory main.py)
    5 IMU       reply CMD|5|<x_deg>|<y_deg>|$
    6 battery   reply CMD|6|<mV>|$

Everything below is taken from the factory main.py on a real MechDog (read over USB) and
tested over Bluetooth (service ffe0: write ffe1, notify ffe2). Notes from that code:
  - a walk keeps going until CMD|3|0|$; actions sent while walking are ignored, so the link
    sends stop first
  - if Bluetooth disconnects, the factory program stops the dog
  - there is no IMU (5) handler and no buzzer command Commands the factory
protocol has no equivalent for (eye colours, countersign flashes, beep, body pose) come back
as "err unsupported ..." and the console skips them.
"""

# ---- edit these after reading the factory main.py ------------------------------------
STOP = "CMD|3|0|$"            # factory: _RUN_DIR == 0 -> move(0, 0)
ACTIONS = {                   # factory main.py action_list
    "left_foot_kick": 1, "right_foot_kick": 2, "stand_four_legs": 3, "sit_dowm": 4,
    "go_prone": 5, "stand_two_legs": 6, "handshake": 7, "scrape_a_bow": 8,
    "nodding_motion": 9, "boxing": 10, "stretch_oneself": 11, "pee": 12,
    "press_up": 13, "rotation_pitch": 14, "rotation_roll": 15,
}
# ---------------------------------------------------------------------------------------

SMALL_TURN = 20               # |turn| above this (degrees) uses the "large" turn codes


def motion_code(step, turn):
    """Pick the factory direction code (1..8) for a step (mm) and turn (deg, + = left)."""
    if step >= 0:
        if turn == 0:
            return 3
        if turn > 0:
            return 4 if turn > SMALL_TURN or step == 0 else 5
        return 2 if -turn > SMALL_TURN or step == 0 else 1
    if turn == 0:
        return 7
    return 6 if turn > 0 else 8   # backward turns only come in "large"


def translate(line):
    """dogctl command -> (frame to send, reply function code to wait for, or None) or an
    ("err ...", None) tuple when there is no factory equivalent. ``ok`` replies that need no
    answer from the dog are produced by the caller."""
    parts = line.strip().split()
    if not parts:
        return "err empty", None
    cmd, a = parts[0].lower(), parts[1:]
    try:
        if cmd == "move":
            step = int(float(a[0]))
            turn = int(float(a[1])) if len(a) > 1 else 0
            if step == 0 and turn == 0:
                return STOP, None
            return "CMD|3|%d|$" % motion_code(step, turn), None
        if cmd == "stop":
            return STOP, None
        if cmd == "stand":
            return "CMD|1|5|$", None
        if cmd == "balance":
            return "CMD|1|3|%d|$" % (1 if a[0].lower() in ("on", "1", "true") else 0), None
        if cmd == "action":
            if a[0] not in ACTIONS:
                return "err unsupported action over factory protocol: %s (add it to ACTIONS)" % a[0], None
            return "CMD|2|1|%d|$" % ACTIONS[a[0]], None
        if cmd == "sonar":
            return "CMD|4|1|$", 4
        if cmd == "rgb":
            r, g, b = [max(0, min(255, int(v))) for v in a[:3]]
            return "CMD|4|3|%d|%d|%d|$" % (r, g, b), None
        if cmd == "avoid":                        # the factory's own obstacle-avoidance walk
            return "CMD|4|2|%d|$" % (1 if a[0].lower() in ("on", "1", "true") else 0), None
        if cmd == "imu":                          # factory program sends no IMU reply over Bluetooth
            return "err unsupported over factory protocol: imu", None
        if cmd == "battery":
            return "CMD|6|$", 6
    except (IndexError, ValueError):
        return "err bad arguments for " + cmd, None
    if cmd in ("beep", "pose"):
        return "err unsupported over factory protocol: " + cmd, None
    if cmd == "code":
        return "err code is timed by the link (BleDog), not one frame", None
    return "err unknown command " + cmd, None


def parse_frames(buf):
    """Split received bytes into complete CMD frames. Returns (frames, leftover)."""
    frames = []
    text = buf.decode(errors="replace") if isinstance(buf, (bytes, bytearray)) else buf
    while "$" in text:
        frame, text = text.split("$", 1)
        start = frame.find("CMD")
        if start >= 0:
            fields = [f for f in frame[start:].split("|")[1:] if f != ""]
            frames.append(fields)
    return frames, text


def reply_text(cmd, fields):
    """Turn a factory reply frame into a dogctl-style reply line."""
    if cmd == "sonar":
        return "ok sonar %d" % int(float(fields[1]))
    if cmd == "imu":
        return "ok imu x=%s y=%s" % (fields[1], fields[2])
    if cmd == "battery":
        return "ok battery %d mV" % int(float(fields[1]))
    return "ok " + "|".join(fields)
