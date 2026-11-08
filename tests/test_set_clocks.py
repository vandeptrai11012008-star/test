import importlib.util
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace


def load_script():
    spec = importlib.util.spec_from_file_location("clock_helper_test", Path(__file__).resolve().parents[1] / "scripts/set_clocks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_ports_are_discovered_and_explicit_ports_limit_access(monkeypatch):
    clock = load_script()
    opened = []
    def connect(device, *args, **kwargs):
        opened.append(device)
        return SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(clock.serial, "Serial", connect)
    monkeypatch.setattr(clock.list_ports, "comports", lambda: [SimpleNamespace(device="COM5"), SimpleNamespace(device="COM8")])
    monkeypatch.setattr(clock.time, "sleep", lambda _: None)
    monkeypatch.setattr(clock, "ask", lambda *args: "#OK dog radio=ok clock=set")
    assert [dev for _, dev, _ in clock.find_boards()] == ["COM5", "COM8"]
    opened.clear()
    assert [dev for _, dev, _ in clock.find_boards(["COM8", "COM8"])] == ["COM8"]
    assert opened == ["COM8"]


def test_native_usb_board_is_identified_after_delayed_reply(monkeypatch):
    clock = load_script()
    replies = iter(("", "", "#OK dog radio=ok code=0"))
    monkeypatch.setattr(clock.serial, "Serial", lambda *a, **k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(clock.time, "sleep", lambda _: None)
    monkeypatch.setattr(clock, "ask", lambda *a: next(replies))
    assert [(kind, dev) for kind, dev, _ in clock.find_boards(["COM9"])] == [("dog", "COM9")]


def test_busy_port_and_unresponsive_board_have_distinct_diagnostics(monkeypatch, capsys):
    clock = load_script()
    closed = []
    def connect(device, *a, **k):
        if device == "COM9":
            raise PermissionError("access denied")
        return SimpleNamespace(close=lambda: closed.append(device))
    monkeypatch.setattr(clock.serial, "Serial", connect)
    monkeypatch.setattr(clock.time, "sleep", lambda _: None)
    monkeypatch.setattr(clock, "ask", lambda *a: "")
    assert clock.find_boards(["COM8", "COM9"]) == []
    diagnostics = capsys.readouterr().err
    assert "COM9 OPEN_FAILED" in diagnostics
    assert "COM8 NO_REPLY" in diagnostics
    assert closed == ["COM8"]


def test_clear_wait_preflights_support_then_clears_band_before_dog(monkeypatch):
    clock = load_script()
    calls = []
    def ask(port, command, wait):
        calls.append((port, command))
        if command == "#?":
            return "#OK %s radio=ok clear_wait=1" % port
        return "#WAIT_CLEARED " + port
    monkeypatch.setattr(clock, "ask", ask)
    clock.clear_wait([("dog", "COM9", "dog"), ("band", "COM8", "band")])
    assert calls == [("dog", "#?"), ("band", "#?"), ("band", "#F"), ("dog", "#F")]


def test_clear_wait_does_not_change_either_side_if_firmware_is_old(monkeypatch):
    clock = load_script()
    calls = []
    def ask(port, command, wait):
        calls.append(command)
        return "#OK %s radio=ok%s" % (port, " clear_wait=1" if port == "dog" else "")
    monkeypatch.setattr(clock, "ask", ask)
    with pytest.raises(RuntimeError, match="update its firmware"):
        clock.clear_wait([("dog", "COM9", "dog"), ("band", "COM8", "band")])
    assert "#F" not in calls
    with pytest.raises(RuntimeError, match="both dog and wristband"):
        clock.clear_wait([("dog", "COM9", "dog")])


def test_pause_is_sent_before_clock_sync_and_clear_wait(monkeypatch):
    clock = load_script()
    calls = []
    class Port:
        def write(self, raw): calls.append(raw)
        def flush(self): pass
        def close(self): pass
    monkeypatch.setattr(sys, "argv", ["set_clocks.py", "--port", "COM9", "--port", "COM8", "--auto", "0", "--clear-wait"])
    monkeypatch.setattr(clock, "find_boards", lambda ports: [("dog", "COM9", Port()), ("band", "COM8", Port())])
    monkeypatch.setattr(clock, "send_clocks", lambda boards: calls.append("clocks"))
    monkeypatch.setattr(clock, "clear_wait", lambda boards: calls.append("clear"))
    monkeypatch.setattr(clock, "status_line", lambda *a: ["#OK ready"])
    monkeypatch.setattr(clock.time, "sleep", lambda _: None)
    clock.main()
    assert calls == [b"#A 0\n", "clocks", "clear"]


def test_missing_requested_dog_reports_incomplete_sync(monkeypatch):
    clock = load_script()
    closed = []
    port = SimpleNamespace(close=lambda: closed.append("COM8"))
    monkeypatch.setattr(sys, "argv", ["set_clocks.py", "--port", "COM8", "--port", "COM9"])
    monkeypatch.setattr(clock, "find_boards", lambda ports: [("band", "COM8", port)])
    monkeypatch.setattr(clock, "send_clocks", lambda boards: None)
    monkeypatch.setattr(clock, "status_line", lambda *a: ["#OK feather radio=ok clock=set"])
    with pytest.raises(SystemExit, match="incomplete clock sync.*COM9"):
        clock.main()
    assert closed == ["COM8"]
