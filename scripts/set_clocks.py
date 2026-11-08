#!/usr/bin/env python3
"""Set the wall clock on every rescue board plugged in over USB: the wristband (XIAO), the dog
Feather and the laptop dongle. Run it before a demo and again after any board restarts (the
FSK hop schedule needs every board within a few seconds of the others):

    uv run --with pyserial python scripts/set_clocks.py            # set all, show status
    uv run --with pyserial python scripts/set_clocks.py --auto 6   # also: dog checks the band every 6 s

Boards are found by asking each serial port "#?" and reading "#OK band|dog|dongle ...".
The wristband's radio modem (its Feather) gets the clock from the XIAO, not from here."""
import argparse
import glob
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("needs pyserial: uv run --with pyserial python scripts/set_clocks.py")


def ask(port, text, wait=0.8):
    port.reset_input_buffer()
    port.write((text + "\n").encode())
    port.flush()
    end, out = time.time() + wait, b""
    while time.time() < end:
        out += port.read(200)
    return out.decode(errors="replace")


def find_boards():
    boards = []
    for dev in sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*") +
                      glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
        try:
            p = serial.Serial(dev, 115200, timeout=0.1)
        except (OSError, serial.SerialException):
            continue
        time.sleep(0.4)
        reply = ask(p, "#?", 1.2)
        kind = next((k for k in ("band", "dog", "dongle") if "#OK " + k in reply), None)
        if kind:
            boards.append((kind, dev, p))
        else:
            p.close()
    return boards


def send_clocks(boards):
    """Send the laptop's time to every board back to back: they should agree to well under a second."""
    for kind, dev, p in boards:
        p.write(("#T %.3f\n" % time.time()).encode())
        p.flush()
    time.sleep(0.5)


def status_line(kind, port):
    """One status line from a board: the wristband's also asks its Feather radio modem."""
    lines = ask(port, "#R" if kind == "band" else "#?", 1.2).splitlines()
    return [l for l in lines if l.startswith(("#OK", "#TEST"))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--auto", type=int, metavar="SECONDS",
                    help="bench: dog runs a near check by itself every N seconds (cleared by a reboot)")
    args = ap.parse_args()
    boards = find_boards()
    if not boards:
        sys.exit("no rescue boards answered on USB")
    send_clocks(boards)
    for kind, dev, p in boards:
        if kind == "dog" and args.auto is not None:
            p.write(("#A %d\n" % args.auto).encode())
            p.flush()
            time.sleep(0.3)
        print("%-6s %s  %s" % (kind, dev, " | ".join(status_line(kind, p)) or "(no status)"))
        p.close()


if __name__ == "__main__":
    main()
