"""Wristband over Bluetooth: the console's band picker (scan / connect / disconnect over HTTP)
and the real band firmware logic behind it. The firmware's C++ band, compiled for the host,
stands in for the Bluetooth stream: same framing as the band's Bluetooth service."""
import json
import subprocess
import threading
import urllib.request

import pytest

import rescue.console as cm
from rescue.radio import SerialLoRaRadio
from test_hardware_path import CXX, Pipes, bins  # noqa: F401  (bins is a fixture)

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler")


@pytest.fixture
def make_band(bins):  # noqa: F811
    procs = []

    def make(target):
        if target == "missing":
            raise ConnectionError("no wristband missing found over Bluetooth")
        proc = subprocess.Popen([str(bins / "host_band")], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        procs.append(proc)
        radio = SerialLoRaRadio(stream=Pipes(proc), boot_wait=1.0)
        radio.port = "ble:Rescue-Band-TEST"
        return radio
    yield make
    for p in procs:
        p.kill()


def serve(console):
    server = cm.make_server(console, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    post = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(base + p, method="POST")).read())
    return server, post


def land_all(c, v):
    while v["flight"]:
        v = c.act("deliver")
    return v


def test_band_picker_failure_then_connect_challenge_alive_disconnect(make_band):
    c = cm.Console()
    c.make_band = make_band
    server, post = serve(c)
    try:
        v = post("/api/band/connect/missing")
        assert v["hw"] is None and v["bandPicker"]["open"] and "no wristband" in v["bandPicker"]["error"]
        v = post("/api/band/connect/Rescue-Band-TEST")
        assert v["hw"]["mode"] == "band over Bluetooth" and not v["bandPicker"]["open"]
        v = land_all(c, c.act("challenge"))                   # the real band logic answers
        assert v["feed"] == "FRIENDLY"
        v = land_all(c, c.act("press"))                       # "#P" over the link: band signs ALIVE
        assert v["feed"] == "ALIVE"
        v = land_all(c, c.act("spoof"))
        assert v["bandStatus"].startswith("SILENT")
        assert not [e for e in v["log"] if e["who"] == "HW" and "disagree" in e["text"]]
        v = post("/api/band/disconnect")
        assert v["hw"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_band_scan_without_bleak_explains_what_to_do(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_bleak(name, *a, **k):
        if name == "bleak" or name.startswith("bleak."):
            raise ImportError("no bleak")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_bleak)
    r = cm.Console().band_scan()
    assert r["devices"] == [] and ".venv" in r["error"]


def test_looks_like_band():
    from rescue.ble_band import NUS_SERVICE, looks_like_band
    assert looks_like_band("Rescue-Band-1A2B") and looks_like_band("", [NUS_SERVICE.upper()])
    assert not looks_like_band("mechdog_94")
