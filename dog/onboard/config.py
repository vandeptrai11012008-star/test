# Wi-Fi settings for the MechDog command server (copied to the dog as config.py).
#
# Leave WIFI_SSID empty: the dog makes its own hotspot (AP_SSID). Join it from the
# laptop; the dog is then at 192.168.4.1.
# Set WIFI_SSID/WIFI_PASSWORD: the dog joins that network instead (e.g. your phone
# hotspot) and prints its IP on the USB console at boot.
WIFI_SSID = ""
WIFI_PASSWORD = ""

AP_SSID = "MechDog-Rescue"
AP_PASSWORD = "mechdog123"   # at least 8 characters

PORT = 9000
