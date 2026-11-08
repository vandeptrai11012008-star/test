import importlib.util
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest


def load_monitor():
    spec = importlib.util.spec_from_file_location("checkin_monitor", Path(__file__).resolve().parents[1] / "scripts/checkin_monitor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_telemetry_and_disconnect_never_imply_current_presence():
    monitor = load_monitor()
    now = [100.0]
    state = monitor.Telemetry(clock=lambda: now[0])
    state.connection(True)
    state.line("#OK dog radio=ok code=0 clock=set auto=2 paired=1")
    state.line("#PERSON_CONFIRMED band=0000E432B076B144 next=2h")
    data = state.snapshot()
    assert data["radio"] == "ok" and data["clock"] == "set"
    assert data["peers"][0]["remaining_seconds"] == 7200
    now[0] += 20
    assert state.snapshot()["peers"][0]["stale"]
    state.line("#PEER band=0000E432B076B144 state=CONFIRMED_WAIT_2H")
    assert state.snapshot()["peers"][0]["remaining_seconds"] == 7180
    state.connection(False, "USB disconnected")
    data = state.snapshot()
    assert not data["connected"] and data["radio"] is None and data["peers"][0]["stale"]
    state.connection(True)
    state.line("#WAIT_CLEARED dog")
    assert state.snapshot()["peers"][0]["state"] == "WAITING"
    assert state.snapshot()["peers"][0]["confirmed_at"] is None


def test_joining_existing_cooldown_does_not_invent_a_start_time():
    state = load_monitor().Telemetry()
    state.line("#PEER band=0000E432B076B144 state=CONFIRMED_WAIT_2H")
    assert state.snapshot()["peers"][0]["remaining_seconds"] is None
    state.line("#BAND_SEEN band=0000E432B076B144 wait=button")
    assert state.snapshot()["peers"][0]["state"] == "BAND_SEEN_WAIT_BUTTON"
    state.line("#BEACON band=0000E432B076B144 wait=button")
    assert state.snapshot()["peers"][0]["state"] == "WAITING"
    for i in range(150):
        state.line("#MONITOR bounded log %d" % i)
    assert len(state.snapshot()["logs"]) == 100


def test_viewer_serves_actual_state_and_rejects_commands():
    monitor = load_monitor()
    state = monitor.Telemetry()
    state.line("#BEACON band=0000E432B076B144 wait=button")
    server = monitor.make_server(state, 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        with urllib.request.urlopen(url + "/api/state") as response:
            assert json.load(response)["peers"][0]["id"] == "0000E432B076B144"
        with urllib.request.urlopen(url) as response:
            assert 'Dog · Wristband' in response.read().decode()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url + "/api/confirm", method="POST"))
        assert error.value.code == 501
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("interval", [0, 2])
def test_usb_watcher_sets_missing_clock_without_confirming_or_resetting(monkeypatch, interval):
    import serial
    monitor = load_monitor()
    stop = threading.Event()
    state = monitor.Telemetry()
    sent = []
    replies = iter((("#OK dog radio=ok clock=NOT-set auto=%d paired=1\n" % interval).encode(),
                    ("#OK dog radio=ok clock=set auto=%d paired=1\n" % interval).encode()))
    class FakeSerial:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def write(self, raw): sent.append(raw)
        def readline(self, size):
            try: return next(replies)
            except StopIteration:
                stop.set()
                return b""
    monkeypatch.setattr(serial, "Serial", lambda *a, **kw: FakeSerial())
    monitor.watch_serial("COM9", state, stop)
    assert any(raw.startswith(b"#T ") for raw in sent)
    assert all(raw == b"#?\n" or raw.startswith(b"#T ") for raw in sent)
    assert state.snapshot()["clock"] == "set"
    assert state.snapshot()["interval_seconds"] == interval
