# Firmware: ESP32 + Adafruit RFM95W (915 MHz)

There are two builds from one PlatformIO project:

- **`band`**: the wearable band, a port of `rescue/band.py`. It listens on LoRa **and** on USB, so a single board can be tested from the laptop before you have a second radio.
- **`dongle`**: a USB-to-LoRa passthrough for the laptop (the drone's radio). It needs a second ESP32 + RFM95W.

The protocol code is in `lib/rescue_core/` (portable C++). It's checked against the Python test vectors by `pytest` on the laptop, with no hardware needed.

## 1. Wire the RFM95W to the ESP32

| RFM95W pin | ESP32 pin |
|---|---|
| VIN | 3V3 |
| GND | GND |
| SCK | GPIO 18 |
| MISO | GPIO 19 |
| MOSI | GPIO 23 |
| CS | GPIO 5 |
| RST | GPIO 14 |
| G0 | GPIO 26 |
| G1 | GPIO 33 (optional) |

**LED:** long leg → 220–330 Ω resistor → GPIO 4; short leg → GND. If you have no resistor, use the board's built-in LED by setting `PIN_LED 2` in `include/config.h`.

**Button:** the ESP32's own **BOOT** button is I'M ALIVE. No wiring needed.

**Antenna:** attach it **before** powering on. Transmitting without one can damage the RFM95W.

Pins, frequency and power are all set in `include/config.h`.

## 2. Flash the band

In VS Code with PlatformIO, open the `firmware/` folder, pick the `env:band` environment, and click Upload. Or from a terminal:

```bash
cd firmware
pio run -e band -t upload
pio device monitor        # expect: #HELLO band radio=ok (code 0)
```

- `radio=missing` means the ESP32 can't reach the RFM95W. Check the wiring, especially CS, RST and G0.
- To find the port: `pio device list`. On a Mac it looks like `/dev/cu.usbserial-0001` or `/dev/cu.SLAB_USBtoUART`.
- Close the serial monitor before the next step, because only one program can hold the port.

## 3. Drive the real band from the console

```bash
pip install pyserial
python3 -m rescue.console --serial /dev/cu.usbserial-0001
```

The header shows **HARDWARE band over USB**. From then on:

- **CHALLENGE:** the packet goes to the ESP32. The board checks the HMAC, blinks the LED with the hour's code and replies. The console compares the board's reply byte for byte with the Python reference (see "Real band" in the protocol flow).
- **Spoofed challenge:** the board stays silent.
- **PRESS: I'M ALIVE:** the board signs ALIVE itself. Pressing the board's **BOOT** button does the same, and the console animates it when it arrives.
- **Link-up → Band clock drift:** really sets the board's clock 2 hours ahead, so you can watch the real band refuse the challenge. Reset puts the clock back.

The console sets the board's clock when it connects. Without that, the band warns `clock not set`, because the time slot comes from the clock.

## 4. Later: a real radio link (second board)

Wire a second ESP32 + RFM95W the same way and flash `pio run -e dongle -t upload`. Plug the dongle into the laptop and run the console with `--serial <dongle port>`. Power the band from a USB power bank. Packets then go over 915 MHz LoRa. Press the band's BOOT button for ALIVE, since the console can't press it remotely.

## Wristband: Seeed XIAO ESP32-S3

Same band logic, its own pins (`BOARD_XIAO_S3` in `include/config.h`). Numbers are the
D labels printed on the XIAO.

The wristband radio is an **RFM69HCW (915 MHz)**. It has no LoRa mode, so this build talks
**FSK** (50 kbps, 25 kHz deviation, 915.0 MHz, sync word 2D D4; all in `config.h`). The pinout is
the same as the RFM9x, so the wiring does not change. LoRa nodes (`band`, `dongle`, and the
dog's Feather) cannot hear it. For the laptop, flash a second ESP32 + RFM95W with
`pio run -e dongle_fsk -t upload`: the SX1276 runs FSK to match. The dog's Feather 32u4 is
LoRa-only and cannot yet do the near check against this band.
**Not yet tested on air**: no RFM69 <-> SX1276 packet has been exchanged on real boards.

**Feather 32u4 RFM69 as the radio (`band_xiao_feather` + `feather_radio`):** the Feather has the RFM69HCW
on the board (CS 8, IRQ 7, RST 4 are wired inside it), so there is nothing to wire to the radio chip.
The XIAO talks to it over serial and the Feather does the FSK hopping:

| Feather | XIAO |
|---|---|
| RX | D6 (XIAO TX) |
| TX | D7 (XIAO RX) |
| GND | GND |
| ANT pad | 78 mm solid wire |

Power the Feather from its own USB cable or a LiPo, not from the XIAO's pins. Flash it with
`pio run -e feather_radio -t upload` and the XIAO with `pio run -e band_xiao_feather -t upload`.
The XIAO sends the Feather the clock every 5 s (the hop schedule comes from it); the Feather never
transmits without it. The Feather build uses 95% of the 32u4's flash and 62% of its RAM before stack,
which is tight: don't add features to it without checking the size. Not yet tested on boards.

**Radio toggle:** the radio is picked by the PlatformIO env, and the wiring is the same for both.
`pio run -e band_xiao -t upload` is the RFM69HCW (FSK, hopping). `pio run -e band_xiao_lora -t upload`
is an RFM9x/RFM95W (LoRa) and works with the LoRa `dongle` and the dog's Feather. Swap the chip, flash
the matching env. A band flashed for the wrong chip prints `radio=missing`.

| Part | Pin |
|---|---|
| RFM69HCW SCK / MISO / MOSI / CS | D7 / D8 / D9 / D10 |
| RFM69HCW RST / G0 (DIO0) | D0 / D6 |
| RFM69HCW VIN / GND | 3V3 / GND |
| Blue LED button, switch | D3 and GND (internal pull-up, pressed = LOW) |
| Blue LED button, LED | + through 100 ohm to D1, - to GND (shows the blink code) |
| Grove Buzzer v1.3, SIG | D4 |
| Coin vibration motor | D5, through a transistor (below) |
| Status LED | the XIAO's own user LED |

```
pio run -e band_xiao -t upload
pio device monitor
```

On boot it prints `#HELLO band radio=ok` and a `#PINS ...` line, flashes the LED and chirps.
`radio=missing` means check the SPI wires and the 3V3 to the radio.
Don't hold the button while it boots: it learns the button's idle level then.

**Vibration motor:** a bare coin motor draws 60 to 100 mA when it starts, more than a GPIO
pin can give, so the firmware leaves it off (`PIN_MOTOR NO_PIN`). Wire it like this, then
set `PIN_MOTOR D5`:

```
D5 --[1k]--> base   NPN (2N2222 / S8050 / BC337)
             emitter -> GND
             collector -> motor (-)        motor (+) -> 3V3
diode (1N4148 or 1N4001) across the motor, stripe toward 3V3
```

Until then the buzzer plays the blink code. With the motor, the buzzer chirps once
and the motor plays the code.

**Hardware self-test** (type into `pio device monitor`, USB only, never sent over the air):

| Command | Does |
|---|---|
| `#L` | LEDs on for 1 s (XIAO LED + button LED) |
| `#B` | buzzer 0.3 s |
| `#M` | motor 0.3 s (once `PIN_MOTOR` is set) |
| `#C` | play blink code 101 on everything |
| `#R` | retry the radio, print its status code (-2 = chip not found) |
| `#V` | raw SPI read of the radio's version register (RFM69: 0x24, RFM9x: 0x12) |
| `#K` | button pin with pull-up / pull-down |
| `#J` | listen to D1 and D3 for 10 s without driving them |

The button prints `#BTN down` / `#BTN up`. Tested on the real wristband with the earlier RFM9x (Nov 2026; not repeated since the RFM69 swap): LEDs,
buzzer, button, radio init, challenge/reply against the Python vectors, replay/spoof/stale
silence, and ALIVE from the button, all over USB. Not yet tested: the motor, and LoRa over the air.

## Notes

- **Key:** the firmware uses the public demo key from the tests. Put a real key in `include/secrets.h` as `#define GROUP_KEY_BYTES {...}`. That file is gitignored. Never commit a real key.
- **Nonce cache:** the band remembers its last 64 challenge nonces in RAM, and a reboot forgets them. This matches the known limit in `CLAUDE.md`.
- **Debug text:** the `#...` lines on USB are a debug channel. The band never transmits them over the air, where it stays completely silent on any failure.

## Periodic dog discovery on RFM69HCW

For a dog and wristband using matching Feather RFM69HCW radios, see
[the periodic discovery guide](docs/periodic-rfm69.md). The dog broadcasts every
2 seconds after clock sync; the wearer still presses the wristband button to
confirm. Build target: `dog_feather_rfm69` (separate from the RFM9x `dog_feather`).

Windows: [Hướng dẫn nạp và thử từng bước](docs/nap-thu-windows.md).
