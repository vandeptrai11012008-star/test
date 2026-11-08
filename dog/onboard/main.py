# MechDog Wi-Fi command server (MicroPython, runs on the dog at boot as main.py).
# Replaces the factory main.py: back that up first (see dog/README.md) so the phone
# app works again after you restore it.
import socket
import time

import network
from HW_MechDog import MechDog

import config
from dogcmd import DogCommands

dog = MechDog()
dog.set_default_pose()

sonar = buzzer = None
try:
    import Hiwonder_IIC
    sonar = Hiwonder_IIC.I2CSonar(Hiwonder_IIC.IIC(1))
    sonar.getDistance()
except Exception as e:
    sonar = None
    print("no ultrasonic module:", e)
try:
    import Hiwonder
    buzzer = Hiwonder.Buzzer()
except Exception as e:
    print("no buzzer:", e)


def start_wifi():
    if config.WIFI_SSID:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        sta.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(100):  # up to 10 s
            if sta.isconnected():
                return "joined %s, dog IP %s" % (config.WIFI_SSID, sta.ifconfig()[0])
            time.sleep(0.1)
        print("could not join", config.WIFI_SSID, "- falling back to hotspot")
        sta.active(False)
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=config.AP_SSID, password=config.AP_PASSWORD, authmode=3)  # WPA2
    return "hotspot %s, dog IP %s" % (config.AP_SSID, ap.ifconfig()[0])


print("MechDog server:", start_wifi(), "port", config.PORT)
battery = None
try:
    import Hiwonder
    battery = Hiwonder.Battery_power        # millivolts, as the factory program reports it
except Exception as e:
    print("no battery reading:", e)
cmds = DogCommands(dog, sonar, buzzer, clock=lambda: time.ticks_ms() / 1000, battery=battery)
if buzzer:
    buzzer.playTone(1200, 80, True)  # ready

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", config.PORT))
sock.settimeout(0.1)
while True:
    try:
        data, addr = sock.recvfrom(256)
    except OSError:  # timeout: nothing arrived
        data = None
    if data:
        reply = cmds.handle(data.decode())
        sock.sendto(reply.encode(), addr)
    note = cmds.tick()
    if note:
        print(note)
