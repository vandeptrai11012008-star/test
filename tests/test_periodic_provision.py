import os
from pathlib import Path
import re
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/provision_periodic.py"


def test_provision_generates_distinct_private_keys_without_printing_them(tmp_path):
    out = tmp_path / "pairing"
    result = subprocess.run([sys.executable, str(SCRIPT), "--dog-id", "D01", "--band-id", "A01",
                             "--band-id", "B01", "--out-dir", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    dog = (out / "dog-secrets.h").read_text()
    a = (out / "band-0000000000000A01-secrets.h").read_text()
    b = (out / "band-0000000000000B01-secrets.h").read_text()
    def macro(text, name):
        return re.search(r"^#define " + name + r" (.+)$", text, re.M).group(1)
    key_a, key_b = macro(a, "PERIODIC_PAIR_KEY_BYTES"), macro(b, "PERIODIC_PAIR_KEY_BYTES")
    assert key_a != key_b
    assert key_a in dog and key_b in dog
    assert key_a not in result.stdout + result.stderr and key_b not in result.stdout + result.stderr
    assert macro(a, "GROUP_KEY_BYTES") == macro(b, "GROUP_KEY_BYTES") == macro(dog, "GROUP_KEY_BYTES")
    assert macro(a, "GROUP_KEY_BYTES") in (out / "radio-secrets.h").read_text()
    assert os.stat(out).st_mode & 0o777 == 0o700
    assert all(os.stat(p).st_mode & 0o777 == 0o600 for p in out.iterdir())
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    rerun = subprocess.run([sys.executable, str(SCRIPT), "--dog-id", "D01", "--band-id", "A01",
                            "--out-dir", str(out)], capture_output=True, text=True)
    assert rerun.returncode != 0
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_provision_rejects_duplicate_or_zero_ids_before_writing(tmp_path):
    for ids in (("A01", "A01"), ("0",), ("not-an-id",), ("10000000000000000",), ("1", "2", "3", "4", "5")):
        command = [sys.executable, str(SCRIPT), "--dog-id", "D01", "--out-dir", str(tmp_path / "absent")]
        for band in ids:
            command.extend(["--band-id", band])
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0
        assert not (tmp_path / "absent").exists()


def test_provision_refuses_output_inside_checkout():
    result = subprocess.run([sys.executable, str(SCRIPT), "--dog-id", "D01", "--band-id", "A01",
                             "--out-dir", str(SCRIPT.parent / "private-pairing")],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "outside the repository" in result.stderr
