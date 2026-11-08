"""Ryze Tello over its official SDK: the demo's drone flies the link-up by itself.

The Mac joins the Tello's own Wi-Fi (TELLO-XXXXXX). Commands are plain text over UDP to
192.168.10.1:8889 ("command", "takeoff", "forward 100", "land", "battery?"); the Tello answers
"ok", "error ..." or a value. It holds position with its downward camera, so these work indoors.

Safety:
- emergency() cuts the motors at once (the drone drops). It skips the queue.
- The Tello lands by itself after 15 s without a command, so a keep-alive runs while it flies.
- Every command has a timeout; nothing here blocks the console.

Run by itself:  python3 drone/tello.py battery | takeoff | land | emergency
"""
import queue
import socket
import threading
import time

TELLO = ("192.168.10.1", 8889)
LOCAL_PORT = 9000
MOVE_TIMEOUT_S = 12          # takeoff and moves answer when they finish
KEEPALIVE_S = 5              # well inside the Tello's 15 s auto-land


class Tello:
    def __init__(self, addr=TELLO, local_port=LOCAL_PORT, sock=None, timeout=3.0):
        self.addr = addr
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if sock is None:
            self.sock.bind(("", local_port))
        self.sock.settimeout(0.2)
        self.timeout = timeout
        self.flying = False
        self.battery = None
        self.last = "not contacted"
        self.busy = None             # command running right now
        self._replies = queue.Queue()
        self._jobs = queue.Queue()
        self._io = threading.Lock()  # one command on the wire at a time
        self._closed = False
        self._last_cmd = 0.0
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._keepalive, daemon=True).start()

    # -- wire ---------------------------------------------------------------
    def _reader(self):
        while not self._closed:
            try:
                data, _ = self.sock.recvfrom(1024)
            except OSError:
                continue
            self._replies.put(data.decode(errors="replace").strip())

    def cmd(self, text, timeout=None):
        """Send one command and wait for its answer. Returns the reply or 'timeout'."""
        with self._io:
            while not self._replies.empty():
                self._replies.get_nowait()
            self._last_cmd = time.time()
            self.sock.sendto(text.encode(), self.addr)
            try:
                reply = self._replies.get(timeout=timeout or self.timeout)
            except queue.Empty:
                reply = "timeout"
            self.last = "%s -> %s" % (text, reply)
            return reply

    def connect(self):
        """Enter SDK mode. True if the Tello answered."""
        ok = self.cmd("command") == "ok"
        if ok:
            self.read_battery()
        return ok

    def read_battery(self):
        r = self.cmd("battery?")
        if r.isdigit():
            self.battery = int(r)
        return self.battery

    # -- plans (run on the worker, never block the caller) -----------------------
    def plan(self, *commands):
        """Replace whatever was queued with these commands, run in order."""
        while not self._jobs.empty():
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        for c in commands:
            self._jobs.put(c)

    def takeoff_and_advance(self, cm):
        self.plan("takeoff", "forward %d" % max(20, min(500, cm)))

    def land(self):
        self.plan("land")

    def emergency(self):
        """Motors off now. Skips the queue and the lock: this must always get through."""
        self.plan()
        try:
            self.sock.sendto(b"emergency", self.addr)
        finally:
            self.flying = False
            self.last = "emergency sent"

    def _worker(self):
        while not self._closed:
            try:
                c = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            self.busy = c
            if c.startswith("wait "):              # pause in the plan; the Tello hovers by itself
                time.sleep(min(10.0, float(c.split()[1])))
                self.busy = None
                continue
            reply = self.cmd(c, timeout=MOVE_TIMEOUT_S if c != "command" else None)
            if c == "takeoff" and reply == "ok":
                self.flying = True
            elif c == "land" and reply == "ok":
                self.flying = False
            elif c == "takeoff" and reply != "ok":   # failed takeoff: don't try to move
                self.plan()
            self.busy = None

    def _keepalive(self):
        while not self._closed:
            time.sleep(1.0)
            if self.flying and self.busy is None and time.time() - self._last_cmd > KEEPALIVE_S:
                self.read_battery()                    # any command resets the 15 s auto-land

    def status(self):
        return {"flying": self.flying, "battery": self.battery, "busy": self.busy, "last": self.last}

    def close(self):
        self._closed = True
        self.sock.close()


if __name__ == "__main__":
    import sys
    t = Tello()
    print("SDK mode:", "ok" if t.connect() else "no answer (is the Mac on the TELLO-XXXXXX Wi-Fi?)")
    what = sys.argv[1] if len(sys.argv) > 1 else "battery"
    if what == "emergency":
        t.emergency()
    elif what != "battery":
        print(what, "->", t.cmd(what, timeout=MOVE_TIMEOUT_S))
    print("battery:", t.read_battery(), "%")
