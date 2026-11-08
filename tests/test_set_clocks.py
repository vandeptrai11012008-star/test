import importlib.util
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
