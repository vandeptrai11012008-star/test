# Rescue band: hardware integration brief

Goal: take the working Python protocol and run it over real LoRa between an
ESP32 + RFM95 band and an ESP32 + RFM95 USB dongle on a laptop.

## Already done (do not redo)
- `rescue/protocol.py`: packet layout, HMAC8 tags, 1 hour slots with plus or minus 1 drift, blink code.
- `rescue/band.py`: reference band logic (silent on any failure, nonce cache, auto-reply, ALIVE on press).
- `rescue/interrogator.py`: drone feed states UNKNOWN, FRIENDLY, ALIVE with replay rejection.
- `tests/test_protocol.py`: includes test vectors that match the web simulation. Keep them green.

## Rules that must survive porting
1. Never FRIENDLY without a verified exchange. Default and failure state is UNKNOWN.
2. Band stays completely silent on bad tag, bad length, reused nonce, or wrong type.
3. Reply and ALIVE reuse the challenge nonce. Drone accepts REPLY once, ALIVE once after it.
4. Compare tags in constant time. Packet is exactly 32 bytes.

## Hardware status
- Done, compiled and host-tested, NOT yet run on a board: `SerialLoRaRadio` (rescue/radio.py), `firmware/` band and dongle
  builds (PlatformIO, RadioLib), console `--serial`. Protocol core `firmware/lib/rescue_core` is checked against the
  Python vectors by tests/test_hardware_path.py. Firmware uses a portable SHA-256 instead of mbedtls so the
  exact same code is tested on the laptop.

## To do
1. `SerialLoRaRadio` in `rescue/radio.py` (pyserial, 0x20 length prefix framing).
2. Dongle firmware: RadioLib SX1276, 915.0 MHz, SF7, BW125, CR4/5, fixed 32-byte payload, USB serial passthrough.
3. Band firmware: port `band.py`. Needs HMAC-SHA256 (mbedtls on ESP32), wall clock for the slot
   (RTC set at issue time; drift tolerance is plus or minus 1 slot), nonce cache in RAM,
   LED blink with SHORT 150 ms and LONG 450 ms pulses, buzzer on verified challenge, button sends ALIVE.
4. Wake-on-radio / duty-cycled receive to hit battery life targets, then measure current.
5. Provisioning: flash the 128-bit group key per band. Never commit real keys.

## Known limits to flag in the pitch
- Pre-shared group key: one captured band exposes the group. Roadmap: per-band keys.
- Nonce cache is RAM only, so a band reboot forgets seen nonces (replay window until slot rolls).
- Blink code has 3 bits (8 patterns): a human cue, not a security control.
- Point to point only. Mesh and IR fallback are roadmap.
