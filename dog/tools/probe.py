# Read-only check of the MechDog's firmware. Runs ON the dog:
#   mpremote connect <port> run dog/tools/probe.py
# It changes nothing: no files written, no servos moved.
import gc
import os
import sys

print("== MechDog probe ==")
print("python:", sys.implementation.name, sys.version)
try:
    print("board:", os.uname().machine, "| release", os.uname().release)
except Exception:
    pass
gc.collect()
print("free RAM:", gc.mem_free(), "bytes")
try:
    st = os.statvfs("/")
    print("free flash:", st[0] * st[3], "bytes")
except Exception:
    pass

print("files on the dog:", sorted(os.listdir()))

want = ["HW_MechDog", "Hiwonder", "Hiwonder_IIC", "Hiwonder_BLE", "network", "socket", "bluetooth", "machine"]
for name in want:
    try:
        __import__(name)
        print("  %-13s yes" % name)
    except Exception as e:
        print("  %-13s NO  (%s)" % (name, e))

try:
    import network
    wlan = network.WLAN(network.STA_IF)
    print("wifi hardware: ok, MAC", ":".join("%02x" % b for b in wlan.config("mac")))
except Exception as e:
    print("wifi hardware: unavailable (%s)" % e)

try:
    import HW_MechDog
    print("MechDog methods:", sorted(n for n in dir(HW_MechDog.MechDog) if not n.startswith("_")))
except Exception as e:
    print("MechDog methods: unavailable (%s)" % e)

print("== done: paste this output back ==")
