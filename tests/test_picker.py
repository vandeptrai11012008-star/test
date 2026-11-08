"""Dog picker (scan / connect / disconnect over HTTP) and the dog's battery level."""
import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dog"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dog", "onboard"))

import rescue.console as cm  # noqa: E402
from ble_link import BleDog  # noqa: E402
from dogcmd import DogCommands  # noqa: E402
from sim_dog import FakeMechDog  # noqa: E402
from test_dog_ble import FakeFactoryDog  # noqa: E402


def fake_maker(target):
    if "missing" in target:
        raise ConnectionError("no MechDog found over Bluetooth")
    return cm.RealDog("ble", link=BleDog(transport=FakeFactoryDog(), timeout=0.5))


def serve(console):
    server = cm.make_server(console, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    post = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(base + p, method="POST")).read())
    get = lambda p: json.loads(urllib.request.urlopen(base + p).read())
    return server, post, get


def test_battery_level_shows_in_the_view():
    c = cm.Console(realdog=cm.RealDog("ble", link=BleDog(transport=FakeFactoryDog(), timeout=0.5)))
    for _ in range(40):                                   # first reading arrives on the poll thread
        b = c.view()["dog"]["battery"]
        if b:
            break
        time.sleep(0.05)
    assert b["mv"] == 7812 and 0 < b["pct"] < 100 and not b["low"]


def test_picker_connect_failure_then_success_then_disconnect():
    c = cm.Console()
    c.make_dog = fake_maker
    server, post, get = serve(c)
    try:
        v = post("/api/dog/connect/mechdog_missing")
        assert v["dog"] is None and v["dogPicker"]["open"] and "no MechDog" in v["dogPicker"]["error"]
        v = post("/api/dog/connect/mechdog_94")
        assert v["dog"]["host"].startswith("ble") and not v["dogPicker"]["open"] and v["dogPicker"]["error"] is None
        v = post("/api/dog/disconnect")
        assert v["dog"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_scan_without_bleak_explains_what_to_do(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_bleak(name, *a, **k):
        if name == "bleak" or name.startswith("bleak."):
            raise ImportError("no bleak")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_bleak)
    r = cm.Console().dog_scan()
    assert r["devices"] == [] and ".venv" in r["error"]


def test_wifi_dog_reports_battery():
    c = DogCommands(FakeMechDog(quiet=True), battery=lambda: 8100, clock=time.time)
    assert c.handle("battery") == "ok battery 8100 mV"
    assert DogCommands(FakeMechDog(quiet=True), clock=time.time).handle("battery").startswith("err")
