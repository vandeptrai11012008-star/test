# Demo runbook

The happy path: **the drone challenges the wristband from far, the person presses I'M ALIVE, the dog
double-checks up close, and the feed says FRIENDLY.** Then the failures: spoof, replay, stale clock.

## What is real

| Piece | Real hardware | Stand-in |
|---|---|---|
| Wristband | XIAO ESP32-S3 + Feather 32u4 RFM69 radio modem | simulated band in the console |
| Dog radio | Feather 32u4 RFM9x running FSK | simulated dog |
| Drone radio | ADALM-PLUTO (`sdr/sdr_fsk_bridge.py`) or an ESP32 dongle | simulated drone |

The link is FSK with 50 channels hopping on a keyed schedule. `--link lora` switches the console to the
earlier LoRa link (and `band_xiao_lora` / `dongle` are the matching builds).

## Before the demo (once)

```
cd firmware
pio run -e feather_radio -t upload        # the wristband's Feather (its own USB cable)
pio run -e band_xiao_feather -t upload    # the wristband's XIAO
pio run -e dog_feather -t upload          # the dog's Feather
pio run -e dongle_fsk -t upload           # only if you use an ESP32 + RFM95W as the drone radio
```

Antennas on before power. The Feather needs its own power (USB or LiPo), never the XIAO's pins. Wires:
XIAO D6 to Feather RX, XIAO D7 to Feather TX, grounds joined (`firmware/docs/wristband-wiring.pdf`).

## On the day

1. Power everything. Plug in the wristband XIAO and the dog Feather (**the dog must be plugged in once
   per power-up**: it gets its clock over USB; the robot's power keeps it afterwards).
2. Check and set the clocks (every board must agree to a few seconds, or nothing hops together):
   ```
   uv run --with pyserial --with numpy python scripts/preflight.py --ble --exchange
   ```
   It prints PASS / WARN / FAIL per item and `READY` when all is well. `scripts/set_clocks.py` alone just
   sets the clocks. Re-run after any board restarts.
3. Drone radio, if using the Pluto: `python3 sdr/sdr_fsk_bridge.py`.
4. Start the console:
   ```
   python3 -m rescue.console --band ble        # wristband over Bluetooth, rest simulated
   python3 -m rescue.console --sdr             # Pluto drone radio: the dog's near check is real
   python3 -m rescue.console --serial PORT     # wristband (or dongle) over USB
   ```
   The header shows the live hop channel on the FSK link.

## The click path

1. **CHALLENGE**: the drone's signed challenge goes out; the reply is checked; the feed turns FRIENDLY.
   The wristband buzzes and blinks this hour's 3-pulse code.
2. **PRESS: I'M ALIVE** (or the wristband's button): the feed turns ALIVE.
3. **START LINK-UP**: far check, ALIVE, then the dog's close-range check (valid tag, strong signal, fast
   round trip: a relay fails it), the countersign, final approach.
4. Failures: **Spoofed drone challenge** (the wristband stays silent), **Replay captured reply**,
   link-up scenarios *No reply*, *Band clock drift* (the real wristband refuses a +2 h clock),
   *Replay at far*, *Impostor at near*.

## If something is off

| You see | Check |
|---|---|
| `radio=missing` on the wristband | the Feather has power, TX/RX crossed, grounds joined; `#R` asks it again |
| nothing answers after a restart | the clocks: run `scripts/set_clocks.py` |
| dog LED short flash only | the near check ran but failed (no reply, too weak, or too slow) |
| dog LED extra long flash | verified |
| dog keeps checking every few seconds | the bench auto-check; power-cycle it or send `#A 0` over USB |

## Not proven yet (say so if asked)

- The full drone-to-dog-to-wristband flow over the air with a real drone radio.
- That a real RFM69 and SX1276 decode the SDR bridge's frames (three constants in `sdr/fsk_link.py`).
- The near-check round-trip budget (65 ms) is an estimate; calibrate it from the dog's `#CHECK` lines.
- Clocks drift a few seconds a day: re-run the preflight on the day, not the day before.
