#!/usr/bin/env python3
"""Set the wall clock on every rescue board plugged in over USB: the wristband (XIAO), the dog
Feather and the laptop dongle. Run it before a demo and again after any board restarts (the
FSK hop schedule needs every board within a few seconds of the others):

    uv run --with pyserial python scripts/set_clocks.py            # set all, show status
    uv run --with pyserial python scripts/set_clocks.py --auto 2   # set the dog's periodic interval

Boards are found by asking each serial port "#?" and reading "#OK band|dog|dongle ...".
The wristband's radio modem (its Feather) gets the clock from the XIAO, not from here."""
import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
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


def find_boards(ports=None):
    boards = []
    devices = ports if ports is not None else [port.device for port in list_ports.comports()]
    for dev in sorted(set(devices)):
        try:
            p = serial.Serial(dev, 115200, timeout=0.1)
        except (OSError, serial.SerialException) as exc:
            print("WARN %s OPEN_FAILED: %s (close any Serial Monitor using this port)" % (dev, exc), file=sys.stderr)
            continue
        time.sleep(0.4)
        kind = None
        try:
            for attempt in range(3):
                reply = ask(p, "#?", 1.2)
                kind = next((k for k in ("band", "dog", "dongle") if "#OK " + k in reply), None)
                if kind:
                    break
                if attempt < 2:
                    time.sleep(0.6)  # opening native USB can reset/re-enumerate a board
            if not kind:
                print("WARN %s NO_REPLY: no #OK band/dog/dongle after 3 requests" % dev, file=sys.stderr)
        except (OSError, serial.SerialException) as exc:
            print("WARN %s IO_FAILED: %s" % (dev, exc), file=sys.stderr)
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


def clear_wait(boards):
    """Verify both firmware roles support USB reset before changing either side."""
    targets = [(kind, dev, port) for kind, dev, port in boards if kind in ("band", "dog")]
    if {kind for kind, _, _ in targets} != {"band", "dog"}:
        raise RuntimeError("--clear-wait needs both dog and wristband XIAO connected over USB")
    for kind, dev, port in targets:
        reply = ask(port, "#?", 1.2)
        if not any(line.startswith("#OK " + kind + " ") and "clear_wait=1" in line.split()
                   for line in reply.splitlines()):
            raise RuntimeError("%s does not advertise clear-wait support; update its firmware first" % dev)
    # Band first: make it ready before the dog starts a new challenge.
    for kind, dev, port in sorted(targets, key=lambda item: item[0]):
        expected = "#WAIT_CLEARED " + kind
        if expected not in ask(port, "#F", 1.2).splitlines():
            raise RuntimeError("%s did not acknowledge #F; reset may be incomplete" % dev)
        print("%s %s" % (dev, expected))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--auto", type=int, metavar="SECONDS",
                    help="dog interval in seconds, 0 stops; RFM69 beacon defaults to 2, RFM9x near checks to off")
    ap.add_argument("--port", action="append", help="only use this USB port; repeat for dog and XIAOs (e.g. COM5)")
    ap.add_argument("--clear-wait", action="store_true", help="clear both sides' two-hour wait over USB without changing pair keys")
    args = ap.parse_args()
    if args.auto is not None and not 0 <= args.auto <= 3600:
        ap.error("--auto must be between 0 and 3600 seconds")
    boards = find_boards(args.port)
    if not boards:
        sys.exit("no rescue boards answered on USB")
    missing = sorted(set(args.port or []) - {dev for _, dev, _ in boards})
    if args.clear_wait and missing:
        for _, _, port in boards:
            port.close()
        sys.exit("cannot clear wait; no board identified on: " + ", ".join(missing))
    # For a staged demo, stop transmission before setting clocks or clearing waits.
    if args.auto == 0:
        for kind, _, port in boards:
            if kind == "dog":
                port.write(b"#A 0\n")
                port.flush()
                time.sleep(0.3)
    send_clocks(boards)
    if args.clear_wait:
        try:
            clear_wait(boards)
        except (RuntimeError, OSError, serial.SerialException) as exc:
            for _, _, port in boards:
                port.close()
            sys.exit(str(exc))
    for kind, dev, p in boards:
        if kind == "dog" and args.auto is not None and args.auto != 0:
            p.write(("#A %d\n" % args.auto).encode())
            p.flush()
            time.sleep(0.3)
        print("%-6s %s  %s" % (kind, dev, " | ".join(status_line(kind, p)) or "(no status)"))
        p.close()
    if missing:
        sys.exit("incomplete clock sync; no board identified on: " + ", ".join(missing))


if __name__ == "__main__":
    main()
