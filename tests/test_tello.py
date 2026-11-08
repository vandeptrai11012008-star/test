"""Tello drone: the console flies it through the link-up stages, and EMERGENCY always gets
through. FakeTello is a UDP server on localhost that answers like the real drone."""
import json
import os
import socket
import sys
import threading
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "drone"))

import rescue.console as console_mod  # noqa: E402
from rescue.console import Console, make_server  # noqa: E402
from tello import Tello  # noqa: E402
from test_console import KEY, SLOT, land_all  # noqa: E402


class FakeTello:
    def __init__(self, battery=87):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.addr = self.sock.getsockname()
        self.battery, self.got, self.flying = battery, [], False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                data, peer = self.sock.recvfrom(256)
            except OSError:
                return
            c = data.decode()
            self.got.append(c)
            if c == "battery?":
                reply = str(self.battery)
            elif c == "emergency":
                self.flying = False
                continue                      # the real Tello does not answer emergency
            elif c == "takeoff":
                self.flying, reply = True, "ok"
            elif c == "land":
                self.flying, reply = False, "ok"
            else:
                reply = "ok"
            self.sock.sendto(reply.encode(), peer)

    def moves(self):
        return [c for c in self.got if c not in ("battery?", "command")]


def tello_for(fake):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    return Tello(addr=fake.addr, sock=s, timeout=1.0)


def wait_for(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def fast_sim(monkeypatch):
    monkeypatch.setattr(console_mod, "COUNTERSIGN_S", 0.0)
    monkeypatch.setattr(console_mod, "SIM_CLOSE_MM_S", 1e6)


def test_connect_reads_battery():
    fake = FakeTello(battery=64)
    t = tello_for(fake)
    assert t.connect() and t.battery == 64 and fake.got[:2] == ["command", "battery?"]


def test_linkup_takes_off_advances_then_lands_when_linked_up():
    fake = FakeTello()
    t = tello_for(fake)
    t.connect()
    c = Console(KEY, clock=lambda: SLOT, tello=t)
    c.act("linkup_start", "normal")
    assert wait_for(lambda: fake.moves()[:2] == ["takeoff", "forward %d" % console_mod.TELLO_ADVANCE_CM])
    v = land_all(c, c.act("linkup_far"))
    v = land_all(c, c.act("linkup_alive"))
    v = land_all(c, c.act("linkup_near"))
    c.act("linkup_countersign")
    for _ in range(100):
        v = c.act("linkup_complete")
        if v["linkup"]["stage"] == "complete":
            break
        time.sleep(0.01)
    assert v["linkup"]["stage"] == "complete"
    assert wait_for(lambda: fake.moves()[-1:] == ["land"]) and not fake.flying
    assert v["tello"]["battery"] == 87


def test_hold_hovers_then_lands(monkeypatch):
    fake = FakeTello()
    t = tello_for(fake)
    c = Console(KEY, clock=lambda: SLOT, tello=t)
    c.act("linkup_start", "noreply")
    land_all(c, c.act("linkup_far"))
    assert c.view()["linkup"]["stage"] == "hold"
    assert wait_for(lambda: "land" in fake.moves(), t=6.0)


def test_emergency_skips_the_queue_over_http():
    fake = FakeTello()
    t = tello_for(fake)
    c = Console(KEY, clock=lambda: SLOT, tello=t)
    t.plan("takeoff", "forward 100", "wait 5", "land")      # a long plan is running
    server = make_server(c, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:%d/api/drone/emergency" % server.server_address[1]
        v = json.loads(urllib.request.urlopen(urllib.request.Request(url, method="POST")).read())
        assert wait_for(lambda: "emergency" in fake.got)
        assert not v["tello"]["flying"]
        time.sleep(0.3)
        assert "land" not in fake.got                      # the rest of the plan was dropped
    finally:
        server.shutdown()
        server.server_close()


def test_no_tello_no_drone_controls():
    assert Console(KEY, clock=lambda: SLOT).view()["tello"] is None
