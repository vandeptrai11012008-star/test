#!/usr/bin/env python3
"""Read paired check-in telemetry over dog USB and display it on the laptop.

Usage: python scripts/checkin_monitor.py --port COM9
Owns the serial port; close Serial Monitor or this app before clock/reset helpers.
Sends #? and automatically sets the dog clock if unset. No confirmation,
clear-wait, or movement command. The wristband must already have a clock.
"""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
import webbrowser

PAGE = Path(__file__).with_name("checkin_monitor.html")


class Telemetry:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.connected = False
        self.error = "USB not connected"
        self.radio = self.device_clock = None
        self.encryption = self.nonce_counter = None
        self.interval = None
        self.peers = {}
        self.logs = deque(maxlen=100)

    def connection(self, connected, error=None):
        with self.lock:
            self.connected, self.error = connected, error
            if not connected:
                self.radio = self.device_clock = None
                self.encryption = self.nonce_counter = None
                self.interval = None

    def line(self, line):
        line = line.strip()[:512]
        if not line.startswith("#"):
            return
        now = self.clock()
        with self.lock:
            self.logs.append(line)
            fields = dict(re.findall(r"([a-z_]+)=([^\s]+)", line))
            if line.startswith(("#OK dog ", "#HELLO dog ")):
                self.radio = fields.get("radio", self.radio)
                self.device_clock = fields.get("clock", self.device_clock)
                self.encryption = fields.get("enc", self.encryption)
                self.nonce_counter = fields.get("counter", self.nonce_counter)
                interval = fields.get("auto", "")
                if interval.isdigit():
                    self.interval = int(interval)
            if line == "#WAIT_CLEARED dog":
                for peer in self.peers.values():
                    peer.update(state="WAITING", confirmed_at=None, remaining_until=None, updated=now)
            band = fields.get("band", "")
            if not re.fullmatch(r"[0-9a-fA-F]{16}", band):
                return
            band = band.upper()
            peer = self.peers.setdefault(band, {"id": band, "state": "WAITING", "confirmed_at": None, "remaining_until": None})
            peer["updated"] = now
            if line.startswith("#PERSON_CONFIRMED "):
                peer.update(state="CONFIRMED_WAIT_2H", confirmed_at=now, remaining_until=None)
            elif line.startswith("#BAND_SEEN "):
                peer.update(state="BAND_SEEN_WAIT_BUTTON", confirmed_at=None, remaining_until=None)
            elif line.startswith("#BEACON "):
                peer.update(state="WAITING", confirmed_at=None, remaining_until=None)
            elif line.startswith("#PEER "):
                state = fields.get("state")
                if state in ("WAITING", "BAND_SEEN_WAIT_BUTTON", "CONFIRMED_WAIT_2H"):
                    peer["state"] = state
                    if state != "CONFIRMED_WAIT_2H":
                        peer["confirmed_at"] = None
                        peer["remaining_until"] = None
                    else:
                        remaining = fields.get("remaining_s", "")
                        if remaining.isdigit() and 0 <= int(remaining) <= 7200:
                            peer["remaining_until"] = now + int(remaining)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            peers = []
            for peer in self.peers.values():
                age = max(0, now - peer["updated"])
                remaining = None
                if peer["state"] == "CONFIRMED_WAIT_2H":
                    if peer["remaining_until"] is not None:
                        remaining = max(0, min(7200, int(peer["remaining_until"] - now)))
                    elif peer["confirmed_at"] is not None:
                        remaining = max(0, min(7200, int(7200 - (now - peer["confirmed_at"]))))
                peers.append(dict(peer, age_seconds=int(age), stale=not self.connected or age > 10,
                                  remaining_seconds=remaining))
            return {"connected": self.connected, "error": self.error, "radio": self.radio,
                    "clock": self.device_clock, "interval_seconds": self.interval,
                    "encryption": self.encryption, "nonce_counter": self.nonce_counter,
                    "peers": peers, "logs": list(self.logs)}


def watch_serial(port, state, stop, sync_clock=True):
    import serial
    while not stop.is_set():
        try:
            with serial.Serial(port, 115200, timeout=0.2, write_timeout=1) as stream:
                state.connection(True)
                next_poll = 0
                last_sync, sync_attempts = -10.0, 0
                while not stop.is_set():
                    if time.monotonic() >= next_poll:
                        stream.write(b"#?\n")
                        next_poll = time.monotonic() + 2
                    raw = stream.readline(2048)
                    if raw:
                        line = raw.decode(errors="replace")
                        state.line(line)
                        if (sync_clock and line.startswith("#OK dog ") and "clock=NOT-set" in line.split()
                                and sync_attempts < 3 and time.monotonic() - last_sync >= 5):
                            stream.write(("#T %.3f\n" % time.time()).encode())
                            last_sync = time.monotonic()
                            sync_attempts += 1
                            state.line("#MONITOR setting dog clock from laptop")
        except (OSError, serial.SerialException) as exc:
            state.connection(False, str(exc))
            stop.wait(2)


def make_server(state, port=8012):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/state":
                data = json.dumps(state.snapshot()).encode()
                content_type = "application/json; charset=utf-8"
            elif path == "/":
                data = PAGE.read_bytes()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True, help="dog USB port, e.g. COM9")
    ap.add_argument("--web-port", type=int, default=8012)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-sync-clock", action="store_true", help="read status without automatically setting an unset dog clock")
    args = ap.parse_args()
    try:
        import serial  # noqa: F401
    except ImportError:
        ap.error("install pyserial: python -m pip install pyserial==3.5")
    state, stop = Telemetry(), threading.Event()
    server = make_server(state, args.web_port)
    worker = threading.Thread(target=watch_serial, args=(args.port, state, stop, not args.no_sync_clock), daemon=True)
    worker.start()
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("Laptop viewer:", url, "| dog:", args.port, "| Ctrl+C to close")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
