// Pins and radio settings. Two boards:
//   default (env band, dongle): ESP32 DevKit (esp32dev) + Adafruit RFM95W breakout (LoRa)
//   BOARD_XIAO_S3 (env band_xiao): the wristband, Seeed XIAO ESP32-S3 + RFM69HCW 900 MHz (FSK)
// The RFM69 has no LoRa mode, so the wristband speaks FSK (LINK_FSK, RADIO_RFM69). The laptop
// dongle must speak the same FSK: build env dongle_fsk (an RFM95W run in FSK mode).
#pragma once
#include <stdint.h>

#define NO_PIN -1
#define LOW_ON_LED 0     // set to 1 below for an LED that lights when the pin is LOW

#if defined(BOARD_XIAO_S3)
// ---- Wristband: XIAO ESP32-S3. Numbers are the D labels printed on the board. ----
//   RFM69HCW (pinout identical to the RFM9x): SCK -> D7  MISO -> D8  MOSI -> D9  CS -> D10  RST -> D0  G0 (DIO0) -> D6
//          VIN -> 3V3  GND -> GND  (any SPI pins work: the S3 routes SPI to any GPIO)
//   Blue LED button: switch between D3 and GND (internal pull-up, pressed = LOW);
//                    LED + -> 100 ohm -> D1, LED - -> GND (it shows the blink code)
//   Grove Buzzer v1.3: SIG -> D4 (driven with a 2.3 kHz tone; it has its own driver transistor)
//   Coin vibration motor: D5, ONLY through a transistor (see firmware/README.md).
// With the radio on a Feather 32u4 RFM69 (env band_xiao_feather) the six radio pins above are
// unused. The XIAO talks to the Feather over serial instead: XIAO D6 (TX) -> Feather RX,
// XIAO D7 (RX) <- Feather TX, and a shared GND. The Feather has its own power (USB or LiPo).
#define PIN_FEATHER_TX D6
#define PIN_FEATHER_RX D7
#define PIN_LORA_SCK D7
#define PIN_LORA_MISO D8
#define PIN_LORA_MOSI D9
#define PIN_LORA_CS D10
#define PIN_LORA_RST D0
#define PIN_LORA_DIO0 D6
#define PIN_LORA_DIO1 NO_PIN
#define PIN_LED LED_BUILTIN  // the XIAO's small orange user LED (GPIO 21), lit when LOW
#undef LOW_ON_LED
#define LOW_ON_LED 1
#define PIN_BUTTON D3        // switch to GND; pressed level is still learned at boot
#define BUTTON_MODULE 0      // 0: internal pull-up (bare switch), 1: a module drives the pin
#define PIN_BUTTON_LED D1     // LED button module: SIG1 -> D1, SIG2 (button) -> D3
#define PIN_BUZZER D4
#define BUZZER_HZ 2300     // driven with a tone: this buzzer only clicks on a steady HIGH
// A bare coin motor pulls 60-100 mA at start, more than a GPIO pin can safely give.
// Leave NO_PIN until the transistor is in, then change to D5.
#define PIN_MOTOR D5
// Motor wired straight to the pin (no transistor): cap the pin's drive current.
// 0 ~5 mA, 1 ~10 mA, 2 ~20 mA. Never 3 (~40 mA). Remove once a transistor is fitted.
#define MOTOR_DIRECT_DRIVE 0

#else
// ---- Adafruit RFM95W breakout -> ESP32 (VSPI) ------------------------------
//   VIN -> 3V3   GND -> GND   SCK -> 18   MISO -> 19   MOSI -> 23
//   CS  -> 5     RST -> 14    G0 (DIO0) -> 26          G1 (DIO1) -> 33 (optional)
#define PIN_LORA_SCK 18
#define PIN_LORA_MISO 19
#define PIN_LORA_MOSI 23
#define PIN_LORA_CS 5
#define PIN_LORA_RST 14
#define PIN_LORA_DIO0 26
#define PIN_LORA_DIO1 33

// ---- Band I/O --------------------------------------------------------------
#define PIN_LED 4        // LED long leg -> 220-330 ohm -> GPIO 4, short leg -> GND (onboard LED: try 2)
#define PIN_BUTTON 0     // BOOT button: pressed = LOW; works as a normal input after boot
#define BUTTON_MODULE 0
#define PIN_BUZZER NO_PIN  // active 3.3 V buzzer pin, or NO_PIN for none
#define PIN_MOTOR NO_PIN   // vibration motor (through a transistor), or NO_PIN
#endif

// Bluetooth LE host link on the band (same packets and commands as USB). 0 to turn it off.
#ifndef BAND_BLE
#define BAND_BLE 1
#endif

// XIAO <-> Feather serial link speed (both ends). 38400, not 115200: the Feather's 8 MHz chip is 3.5% off at 115200 (garbled bytes), 0.16% at 38400.
#define FEATHER_LINK_BAUD 38400

#ifndef BUZZER_HZ
#define BUZZER_HZ 2300
#endif
#ifndef PIN_BUTTON_LED
#define PIN_BUTTON_LED NO_PIN
#endif

#ifndef LINK_FSK
// ---- LoRa, must match on every node (CLAUDE.md) ----------------------------
#define LORA_FREQ_MHZ 915.0   // 868.0 in Europe/UK
#define LORA_BW_KHZ 500.0     // 500 kHz: wide enough channel for single-channel operation in the US band
#define LORA_SF 7
#define LORA_CR 5             // 4/5
#define LORA_POWER_DBM 2      // RFM95W PA_BOOST: 2..20. Lowest setting by default; check your local limits before raising it.

// ---- US band limits, checked at compile time with margin ------------------------------------
// A single-channel link needs a 6 dB bandwidth of at least 500 kHz, so BW is fixed at 500.
// Keep the 500 kHz channel at least 1 MHz inside 902-928 MHz, and the power far below the
// 1 W limit (low power also keeps harmonics small). Change these only after checking the
// rules where you are.
static_assert(LORA_BW_KHZ == 500.0, "single-channel operation here relies on a 500 kHz LoRa channel");
static_assert(LORA_FREQ_MHZ >= 903.0 && LORA_FREQ_MHZ <= 927.0, "keep the LoRa channel inside 903-927 MHz");
static_assert(LORA_POWER_DBM <= 10, "keep LoRa power at or below 10 dBm (margin for harmonics and RF exposure)");
#else
// ---- FSK link (RFM69 band <-> SX1276 dongle in FSK mode), must match on both ends -----------
// 2-FSK, NRZ, Gaussian shaping (BT 0.5), no whitening, variable-length packets (the length byte lets the
// receiver reject anything that is not exactly 32 bytes), CRC on, 2-byte sync word.
// NOT checked on air yet: no RFM69 <-> SX1276 packet has been exchanged on real boards.
#define FSK_HOP 1
#define FSK_CHANNELS 50
#define FSK_CH0_MHZ 903.10        // channel 0; channel n = CH0 + n * STEP (include/hop.h)
#define FSK_CH_STEP_MHZ 0.48      // 50 channels: 903.10 .. 926.62 MHz
#define FSK_FREQ_MHZ FSK_CH0_MHZ  // where the radio starts
#define FSK_BITRATE_KBPS 50.0
#define FSK_DEV_KHZ 25.0      // modulation index 1
#define FSK_RXBW_KHZ 125.0    // valid on both chips (RFM69 and SX1276)
#define FSK_PREAMBLE_BITS 32
#define FSK_SYNC_WORD {0x2D, 0xD4}
#define FSK_SHAPING RADIOLIB_SHAPING_0_5  // Gaussian BT 0.5 on both ends: less sideband splatter
#define FSK_MIN_TX_GAP_MS 250  // band: never transmit twice within this window (caps airtime)
#define FSK_POWER_DBM -2      // RFM69HCW high-power path: -2..20. Lowest setting by default.
#define FSK_DONGLE_POWER_DBM 2  // SX1276 dongle: its lowest setting (2..17), 4 dB above the band
#define RFM69_HIGH_POWER 1    // 1 for RFM69HCW (the PA_BOOST-only part), 0 for the low-power RFM69W

// ---- Synchronized hopping (include/hop.h) -------------------------------------------------
// Channel = keyed pseudorandom schedule of the wall-clock dwell slot, computed on both ends from
// the group key, so the band's receiver hops in step with the dongle's transmitter. Every 50
// slots visit all 50 channels once (equal use). The band and dongle clocks come from the laptop
// (#T <unix>, millisecond resolution) and must agree within one dwell: the dongle tries the
// current slot, then the next, then the previous (FSK_GUARD), and the band sends ALIVE on the same
// three. Neither end transmits while its clock is unset. A drift beyond one dwell means misses
// until the clock is set again.
#define FSK_DWELL_MS 8000
#define FSK_REPLY_WAIT_MS 60  // per channel: the band answers through its serial radio modem, ~30 ms

// Hopping rules this design is built to (902-928 MHz): at least 50 hop channels, pseudorandom
// order and equal use, channel spacing >= the 20 dB bandwidth, 20 dB bandwidth < 250 kHz, average
// occupancy of a channel <= 0.4 s per 20 s, receivers shifting in synchronization with the
// transmitter. None of it is verified by measurement.
static_assert(FSK_CHANNELS >= 50, "at least 50 hop channels are needed when the 20 dB bandwidth is under 250 kHz");
static_assert(FSK_CH_STEP_MHZ >= 0.25, "channel spacing must be at least the 20 dB bandwidth (under 250 kHz)");
static_assert(FSK_CH0_MHZ >= 903.0 && FSK_CH0_MHZ + FSK_CH_STEP_MHZ * (FSK_CHANNELS - 1) <= 927.0,
              "keep every channel inside 903-927 MHz");
static_assert(FSK_DWELL_MS >= 2000, "the dwell must be longer than the expected clock error between the ends");
static_assert(FSK_DONGLE_POWER_DBM <= 2, "dongle stays at the SX1276 minimum");
static_assert(FSK_POWER_DBM <= 0, "keep FSK power at or below 0 dBm until the hop link is measured");
#endif

// ---- Group key -------------------------------------------------------------
// DEMO KEY ONLY: the same public key the Python tests and the console use.
// Never commit a real key. For a real band, put it in include/secrets.h (gitignored)
// as  #define GROUP_KEY_BYTES {0x.., ... 16 bytes}
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef GROUP_KEY_BYTES
#define GROUP_KEY_BYTES {0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, \
                         0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c}
#endif
