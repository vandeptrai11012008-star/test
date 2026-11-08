#!/usr/bin/env python3
"""Pre-demo check: finds the rescue boards on USB, sets their clocks, and says plainly what is ready.

    uv run --with pyserial --with numpy python scripts/preflight.py
    uv run --with pyserial --with numpy --with bleak python scripts/preflight.py --ble --exchange

Checks: this laptop (pyserial, console import, group key), each board on USB (radio found, clock set),
optionally the wristband over Bluetooth (--ble) and a real challenge/reply with the wristband over USB
(--exchange; proves the wristband and the console share the same group key).
Exit code 1 if anything FAILs. WARN means "probably fine, look at it".
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import set_clocks  # noqa: E402  (same folder)


def check_status(kind, lines):
    """-> (level, message) from a board's status lines. Pure, so it is unit tested."""
    text = " ".join(lines)
    if not text:
        return "FAIL", "no status answer"
    radio = re.search(r"radio=(\w+)", text)
    clock = re.search(r"clock=(\S+)", text)
    if kind == "band" and "feather" not in text:
        return "FAIL", "wristband did not report its radio modem (Feather): " + text
    if not radio or radio.group(1) != "ok":
        return "FAIL", "radio not ok (%s)" % (radio.group(1) if radio else "no radio field")
    if kind in ("band", "dog") and (not clock or clock.group(1) != "set"):
        return "FAIL", "clock not set"
    return "PASS", text


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ble", action="store_true", help="also look for the wristband over Bluetooth (needs bleak)")
    ap.add_argument("--exchange", action="store_true",
                    help="send the wristband a real challenge over USB and check the reply (takes ~6 s)")
    ap.add_argument("--key-hex", default=None, help="group key to test with (default: the demo key)")
    args = ap.parse_args()
    results = []

    def report(level, what, detail=""):
        results.append(level)
        print("%-4s %-22s %s" % (level, what, detail))

    # laptop
    try:
        import rescue.console  # noqa: F401
        report("PASS", "console imports")
    except Exception as e:                                      # noqa: BLE001
        report("FAIL", "console imports", repr(e))
    secrets = ROOT / "firmware" / "include" / "secrets.h"
    report("PASS" if secrets.exists() else "WARN", "group key",
           "firmware/include/secrets.h present" if secrets.exists() else "no secrets.h: boards use the public DEMO key")

    # boards on USB
    boards = set_clocks.find_boards()
    kinds = [k for k, _, _ in boards]
    if not boards:
        report("FAIL", "boards on USB", "none answered. Plug a board in, or check the cable carries data")
    else:
        set_clocks.send_clocks(boards)
        for kind, dev, port in boards:
            level, msg = check_status(kind, set_clocks.status_line(kind, port))
            report(level, "%s (%s)" % (kind, dev.split("/")[-1]), msg)
            port.close()
    if boards and "band" not in kinds:
        report("WARN", "wristband", "not on USB. It needs its clock set over USB or Bluetooth (#T) after every restart")
    if boards and "dog" not in kinds:
        report("WARN", "dog", "not on USB. If it is running on the robot's power it keeps its clock; "
                              "if it restarted, plug it in and run scripts/set_clocks.py")

    if args.ble:
        try:
            from rescue.ble_band import scan_bands
            found = [r for r in scan_bands(6.0) if r["likely"]]
            report("PASS" if found else "FAIL", "wristband over Bluetooth",
                   ", ".join("%s (%s dBm)" % (r["name"], r["rssi"]) for r in found) or "no Rescue-Band found")
        except Exception as e:                                  # noqa: BLE001
            report("FAIL", "wristband over Bluetooth", repr(e))

    if args.exchange:
        band = next((dev for k, dev, _ in boards if k == "band"), None)
        if not band:
            report("FAIL", "challenge / reply", "wristband not on USB")
        else:
            from rescue import protocol as P
            from rescue.radio import SerialLoRaRadio
            key = bytes.fromhex(args.key_hex) if args.key_hex else bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
            radio = SerialLoRaRadio(band, boot_wait=4.0)
            try:
                nonce, slot = os.urandom(4), P.slot_of()
                radio.send(P.make(key, P.CHALLENGE, nonce, slot).encode())
                reply = radio.recv(3.0)
                ok = reply == P.make(key, P.REPLY, nonce, slot).encode()
                report("PASS" if ok else "FAIL", "challenge / reply",
                       "byte-exact reply: wristband and console share the key" if ok else
                       "no matching reply: wrong group key, or the clock is off")
                time.sleep(1.5)
            finally:
                radio.close()

    bad, warn = results.count("FAIL"), results.count("WARN")
    print("\n%s: %d fail, %d warn" % ("READY" if not bad else "NOT READY", bad, warn))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
