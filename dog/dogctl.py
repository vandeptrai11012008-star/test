"""Control the MechDog over Wi-Fi from the laptop. Standard library only.

    python3 dog/dogctl.py ping                 is it there?
    python3 dog/dogctl.py move 60 0            walk forward (keeps walking ~1 s, then the dog's safety stop)
    python3 dog/dogctl.py stop | stand | sonar | status
    python3 dog/dogctl.py action sit_dowm      built-in action (also: handshake, go_prone, stand_four_legs ...)
    python3 dog/dogctl.py drive                keyboard driving (W/A/S/D, space = stop, q = quit)

Options: --host (default 192.168.4.1, the dog's own hotspot), --port (9000).
Bluetooth instead of Wi-Fi (factory program, nothing installed on the dog; pip install bleak):
    python3 dog/dogctl.py --ble drive          or  --ble-name <name or address>
Try it without the dog: run  python3 dog/sim_dog.py  and add  --host 127.0.0.1
"""
import argparse
import socket
import sys
import time


class Dog:
    def __init__(self, host="192.168.4.1", port=9000, timeout=1.0):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)

    def send(self, line, retries=2, timeout=None):
        """Send one command, return the dog's reply (or raise TimeoutError)."""
        if timeout is not None:
            self.sock.settimeout(timeout)
        for _ in range(retries + 1):
            self.sock.sendto(line.encode(), self.addr)
            try:
                data, _ = self.sock.recvfrom(256)
                return data.decode()
            except socket.timeout:
                continue
        raise TimeoutError("no reply from the dog at %s:%d" % self.addr)

    def move(self, step, turn=0):
        return self.send("move %d %d" % (step, turn))

    def stop(self):
        return self.send("stop")


def drive(dog):
    """Keyboard driving. Keys set a target; the target is re-sent every 150 ms so the
    dog's 1 s safety stop never fires while you're driving."""
    import curses

    keys = {"w": (60, 0), "s": (-50, 0), "a": (0, 25), "d": (0, -25), "e": (60, 20), "z": (60, -20)}
    actions = {"1": "sit_dowm", "2": "stand_four_legs", "3": "handshake", "4": "go_prone"}

    def ui(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        target, last_send = (0, 0), 0.0
        try:
            reply = dog.send("ping")
        except TimeoutError as e:
            reply = str(e)
        while True:
            k = scr.getch()
            ch = chr(k).lower() if 0 <= k < 256 else ""
            if ch == "q":
                break
            if ch in keys:
                target = keys[ch]
            elif ch == " ":
                target = (0, 0)
                reply = dog.stop()
            elif ch in actions:
                target = (0, 0)
                reply = dog.send("action " + actions[ch])
            elif ch == "r":
                reply = dog.send("stand")
            if time.time() - last_send > 0.15 and target != (0, 0):
                try:
                    reply = dog.move(*target)
                except TimeoutError as e:
                    reply = str(e)
                last_send = time.time()
            scr.erase()
            scr.addstr(0, 0, "MechDog drive  |  W fwd  S back  A/D turn  E/Z arc  SPACE stop  R stand  Q quit")
            scr.addstr(1, 0, "actions: 1 sit  2 stand  3 handshake  4 lie down")
            scr.addstr(3, 0, "target: step %d mm, turn %d deg" % target)
            scr.addstr(4, 0, "dog:    %s" % reply[:70])
            scr.refresh()
            time.sleep(0.02)
        dog.stop()

    curses.wrapper(ui)


def main():
    ap = argparse.ArgumentParser(description="MechDog Wi-Fi control")
    ap.add_argument("--host", default="192.168.4.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--ble", action="store_true", help="use Bluetooth and the factory program (needs bleak)")
    ap.add_argument("--ble-name", metavar="NAME_OR_ADDRESS", help="Bluetooth device to use (implies --ble)")
    ap.add_argument("command", nargs="+", help="drive, or a dog command such as: move 60 0")
    a = ap.parse_args()
    if a.ble or a.ble_name:
        from ble_link import BleDog
        dog = BleDog(a.ble_name)
        print("connected over Bluetooth:", dog.host)
    else:
        dog = Dog(a.host, a.port)
    try:
        if a.command == ["drive"]:
            drive(dog)
        else:
            print(dog.send(" ".join(a.command)))
    except TimeoutError as e:
        hint = ("Bluetooth: is the Hiwonder app closed?" if a.ble or a.ble_name
                else "Is the laptop on the dog's Wi-Fi (MechDog-Rescue) and the server running?")
        sys.exit("%s\n%s" % (e, hint))


if __name__ == "__main__":
    main()
