"""The hardware path without hardware: compile the firmware's C++ band logic as a host
program that speaks the same USB framing as the ESP32 band, and drive it through
SerialLoRaRadio and the console. Also runs the firmware core's own vector tests."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from rescue import protocol as P
from rescue.console import Console
from rescue.radio import SerialLoRaRadio

FW = Path(__file__).resolve().parent.parent / "firmware"
CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler")


def build(src, out):
    subprocess.run([CXX, "-std=c++17", "-O1", "-I", str(FW / "lib/rescue_core"),
                    str(FW / "lib/rescue_core/rescue_core.cpp"), str(FW / src), "-o", str(out)], check=True)


@pytest.fixture(scope="module")
def bins():
    d = Path(tempfile.mkdtemp())
    build("test/host/test_core.cpp", d / "test_core")
    build("test/host/host_band.cpp", d / "host_band")
    yield d
    shutil.rmtree(d, ignore_errors=True)


class Pipes:
    """read/write over a subprocess, returning whatever bytes are available like a serial port."""

    def __init__(self, proc):
        self.proc = proc

    def read(self, n):
        return os.read(self.proc.stdout.fileno(), n)

    def write(self, b):
        self.proc.stdin.write(b)
        self.proc.stdin.flush()

    def close(self):
        self.proc.kill()


@pytest.fixture
def board(bins):
    proc = subprocess.Popen([str(bins / "host_band")], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    link = SerialLoRaRadio(stream=Pipes(proc), boot_wait=2.0)
    yield link
    link.close()


def land_all(c, v):
    while v["flight"]:
        v = c.act("deliver")
    return v


def test_firmware_core_matches_python_vectors(bins):
    out = subprocess.run([str(bins / "test_core")], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
    assert "all checks passed" in out.stdout


def test_periodic_dog_waits_for_human_and_rejects_stale_replies(tmp_path):
    exe = tmp_path / "test_periodic_dog"
    build("test/host/test_periodic_dog.cpp", exe)
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "periodic dog: ok" in result.stdout


def test_fsk_hop_schedule_meets_the_channel_rules(tmp_path):
    """50+ channels inside 903-927 MHz, 250 kHz+ apart, keyed pseudorandom order, equal use,
    identical on both ends, and a clock error of a dwell lands on a neighbouring slot."""
    out = tmp_path / "test_hop"
    subprocess.run([CXX, "-std=c++17", "-I", str(FW / "include"), "-I", str(FW / "lib/rescue_core"),
                    str(FW / "lib/rescue_core/rescue_core.cpp"), str(FW / "test/host/test_hop.cpp"),
                    "-o", str(out)], check=True)
    r = subprocess.run([str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and "hop: ok" in r.stdout, r.stdout


def test_dog_radio_driver_matches_radiolib_registers(tmp_path):
    """The dog's lean SX1276 FSK driver leaves the chip registers as RadioLib does for the link settings."""
    out = tmp_path / "test_sx1276"
    subprocess.run([CXX, "-std=c++17", "-I", str(FW / "lib/sx1276_fsk"), str(FW / "test/host/test_sx1276.cpp"),
                    "-o", str(out)], check=True)
    r = subprocess.run([str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and "sx1276: ok" in r.stdout, r.stdout


def test_serial_radio_talks_to_firmware_band(board):
    assert board.hello.startswith("#HELLO band")
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    nonce = bytes([9, 9, 9, 9])
    challenge = P.make(key, P.CHALLENGE, nonce, P.slot_of()).encode()
    board.send(challenge)
    reply = board.recv(2.0)
    assert reply == P.make(key, P.REPLY, nonce, P.slot_of()).encode()   # byte for byte
    assert board.wait_line("#BUZZ", 1.0).split()[1:] == [str(b) for b in P.blink_bits(key, P.slot_of())]
    board.send(challenge)                                               # replayed challenge
    assert board.recv(0.5) is None and board.wait_line("#SILENT", 1.0)


def test_console_drives_firmware_band_and_agrees(board):
    c = Console(link=board)
    v = land_all(c, c.act("challenge"))
    assert v["feed"] == "FRIENDLY" and v["hw"]["mode"] == "band over USB"
    v = land_all(c, c.act("press"))                                      # board signs ALIVE via "#P"
    assert v["feed"] == "ALIVE"
    v = land_all(c, c.act("spoof"))                                      # board stays silent
    assert v["bandStatus"].startswith("SILENT")
    assert not [e for e in v["log"] if e["who"] == "HW"], "hardware and Python reference must agree"


def test_linkup_drift_really_skews_the_board(board):
    c = Console(link=board)
    c.act("linkup_start", "drift")                                       # sends "#T now+2h" to the board
    v = land_all(c, c.act("linkup_far"))
    assert v["linkup"]["stage"] == "hold"
    assert not [e for e in v["log"] if e["who"] == "HW"]
    _, v = c.act("reset"), None                                          # reset puts the clock back
    v = land_all(c, c.act("challenge"))
    assert v["feed"] == "FRIENDLY"
