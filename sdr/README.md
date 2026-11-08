# SDR as the drone's radio

> **Licensing and regulation come first.** The SDR can transmit on almost any frequency and is not
> a certified transmitter. Only transmit where and how your radio regulator allows (for example FCC
> Part 15 limits on 915 MHz in the US; 868 MHz rules in the EU/UK), with any license required, at
> the lowest power that works (`--tx-atten`). Never use it to jam, spoof, intercept or interfere
> with any radio or device. This repository does not support unlicensed, unlawful or unethical
> use. See [../RESPONSIBLE_USE.md](../RESPONSIBLE_USE.md).

There are two bridges, one per link. Both give the console the same localhost UDP packet radio:

| Bridge | Link | Talks to |
|---|---|---|
| `sdr_fsk_bridge.py` | FSK with channel hopping (the RFM69 wristband, the dog's SX1276 in FSK) | the current wristband and dog |
| `sdr_bridge.py` | LoRa through GNU Radio and [gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr) | LoRa nodes only (`band_xiao_lora`, the LoRa `dongle`) |

## FSK bridge (`sdr_fsk_bridge.py`), for the wristband and dog

```
console (rescue.radio.SdrRadio) --udp--> sdr_fsk_bridge.py --FSK, 50 channels hopping--> wristband / dog
```

```
# one-time (macOS; there is no libiio in Homebrew): a conda-forge environment with libiio and pyadi-iio
# (libiio 0.25 and pyadi-iio 0.0.19: with libiio 1.x the Pluto's transmit stream cannot be restarted)
micromamba create -y -n pluto025 -r ~/micromamba -c conda-forge python=3.11 "libiio=0.25" numpy pip
micromamba run -r ~/micromamba -n pluto025 pip install pyadi-iio==0.0.19 pylibiio==0.25.0

python3 sdr/sdr_fsk_bridge.py --loopback                                      # no SDR, only numpy needed
micromamba run -r ~/micromamba -n pluto025 python sdr/sdr_fsk_bridge.py       # real ADALM-PLUTO (found over USB)
python3 -m rescue.console --sdr
```
The Pluto shows up as a USB device (`usb:...`) and is found automatically; `--uri ip:192.168.2.1` forces the network address.

It is the dongle's firmware logic in Python: the channel comes from the same keyed hop schedule and the
laptop's clock (so there is no clock to set on the drone side). A packet from the console goes out on
the current slot's channel, then the next, then the previous, listening 60 ms for a reply on each; when
idle it follows the schedule so it hears the dog's verdict and the wristband's ALIVE. Link settings are
read from `firmware/include/config.h`; `--key-hex` is the group key (the hop order comes from it).

**Tested without a radio** (`tests/test_sdr_fsk.py`): the modem round-trips frames through noise, carrier
offset and timing; the Python hop schedule equals the firmware's; the exchange logic finds a simulated
wristband on the current, next or previous slot. **Not tested:** the real Pluto (`PlutoRadio` is written
from the pyadi-iio API), and whether a real RFM69 / SX1276 decodes these frames. Three frame details come
from the chips' datasheets and are constants at the top of `fsk_link.py`: `CRC_OUT_XOR`,
`CRC_OVER_LENGTH` and `BIT1_IS_HIGH_FREQ`. If the first on-air test decodes nothing, flip those.

## LoRa bridge (`sdr_bridge.py`)

The SDR speaks LoRa through GNU Radio and [gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr).
`sdr_bridge.py` turns it into a packet radio for the console over localhost UDP.

```
console (rescue.radio.SdrRadio) --udp--> sdr_bridge.py --LoRa 915 MHz--> band / dog's Feather
```

## One-time setup (macOS, Apple Silicon or Intel)

```
brew install micromamba
micromamba create -y -n sdr -r ~/micromamba -c tapparelj -c conda-forge python=3.12 gnuradio gnuradio-iio gnuradio-lora_sdr
```

Plug the SDR into the Mac with a USB data cable (on boards with two ports, use the data port, not
the power-only one). On Apple Silicon, click **Allow** when macOS asks about the accessory. Then
`micromamba run -r ~/micromamba -n sdr iio_info -s` lists it.

## Run

```
micromamba run -r ~/micromamba -n sdr python sdr/sdr_bridge.py      # terminal 1
python3 -m rescue.console --sdr                                           # terminal 2
```

`--loopback` runs the bridge without an SDR (what it sends, it hears), for testing.

## What goes over the air

- Far check: the drone's CHALLENGE to the band, the band's REPLY back.
- Near check: the drone sends a signed **DOG_CHECK** to the dog's Feather
  (`firmware`, env `dog_feather`). The Feather challenges the band itself, measures signal
  strength and round trip, and answers with a signed **DOG_VERDICT**. Formats: `rescue/protocol.py`.

LoRa settings everywhere: 915 MHz, SF7, BW 500 kHz, CR 4/5, explicit header, CRC on,
sync word 0x12, 8-symbol preamble. The bridge transmits at low power by default (`--tx-atten 15`, about -8 dBm), which is plenty across a room.
