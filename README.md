# Rescue check-in band

A wearable band for search-and-rescue and responder teams. A drone or ground robot asks
"are you one of ours, and are you OK?" over long-range radio, the band answers with a signed
reply, and the wearer can press a button to check in. Nobody can fake or replay an answer
without the team's key. This repo holds the protocol, the band and robot firmware, and a demo
console.

> **Disclaimer:** a hackathon prototype for demonstration only. It has not been tested, validated or
> certified for any real-world, field or safety-critical use, and comes with no warranty. Anyone who
> uses any part of it does so at their own risk and is solely responsible for the results and for
> complying with the law. See [DISCLAIMER.md](DISCLAIMER.md).

> **Responsible use:** SDRs (software-defined radios) and LoRa boards are radio transmitters. Do not
> transmit without following your local radio regulations and holding any license they require.
> This repository does **not** support any unlicensed, unlawful or unethical use. Read
> [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md) before using the hardware parts.

## Operator console (demo)

**Setup for the real hardware (once):** the robot dog over Bluetooth and the band over USB need two extra packages:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then start the demo and use CONNECT DOG in the header:

```bash
.venv/bin/python -m rescue.console
```

Or connect straight to your dog:

```bash
.venv/bin/python -m rescue.console --dog ble:mechdog_94
```

**The page:**
- **PRESENT view (default):** fits one screen. The map has callout bubbles for each step, the step-by-step flow cards sit underneath, a control bar runs along the bottom, and a **live timeline** on the right auto-scrolls to the current step. Hover or focus any step for an explanation of what happened and why it matters.
- **DETAIL view:** the full page, with the packet inspector, the 32-byte strip, the event log and the band close-up.
- **CONNECT DOG:** scans Bluetooth and lists MechDogs first; you can also type a Wi-Fi IP. It opens by itself if `--dog` can't find the named dog.
- **Battery:** the dog's battery level shows in the header and refreshes every 15 s.

```bash
python3 -m rescue.console
```

This opens http://127.0.0.1:8011 in your browser. You need Python 3.9 or newer and no other installs. The page reaches out only for Google Fonts.

The Python code does all the protocol work. `rescue/protocol.py`, `rescue/band.py` and `rescue/interrogator.py` (the `rescue` package) build, sign and verify every packet, and the drone and band talk over a `LoopbackRadio`. The browser only animates packets in flight. Things to try:

- **CHALLENGE**: the drone pings the band. The band checks the tag, buzzes, blinks this hour's code and auto-replies, and the feed shows the person as verified (FRIENDLY).
- **PRESS: I'M ALIVE**: the wearer presses the band's button and the band sends a signed ALIVE. The feed goes ALIVE.
- **Spoofed challenge**: someone without the key sends a challenge. The band stays silent.
- **Replay captured reply**: someone re-sends a genuine recorded reply byte for byte. The drone rejects it and the feed drops to UNKNOWN.

**Link-up (far and near recognition).** Pick a scenario and press START LINK-UP. A checklist tracks each step, and an illustrative risk gauge (how sure the team is about who is there) shows HIGH, MEDIUM or LOW, with a command: ADVANCE, HOLD, ABORT or LINKED UP.

1. **The drone flies in to the far line, about 210 m out.** It runs the signed LoRa challenge and reply. If verified, risk drops to MEDIUM, and the drone **holds there and keeps watch**. The person presses I'M ALIVE to check in.
2. **The robot dog walks the last stretch to the near line, about 55 m out.** It carries its own LoRa radio (a second real `Interrogator`) and challenges the band at close range. The reply must pass three checks: a **valid tag**, a **strong signal** (at least −70 dBm), and a **round trip under 65 ms**. A direct reply takes about 43 ms; through a relay it takes about 86 ms, because LoRa radios must receive a whole packet before re-sending it. If it passes, risk drops to LOW and the dog closes in.

Failure scenarios:
- **No reply at far** (band off): HOLD.
- **Band clock 2 hours off:** the real `Band` stays silent, so HOLD.
- **Replay at far:** an old genuine reply is rejected, so HOLD.
- **Relay at near:** a relayed band passes far recognition, and the reply to the dog is genuine and strong but too slow, so ABORT and the dog backs off.

3. **Countersign:** the dog flashes this hour's code on its eyes (the RGB LEDs on its ultrasonic sensor). It's the same code the band blinks, so the person can check the robot is from their team. Recognition goes both ways.
4. **Final approach on the ultrasonic sensor:** the dog walks in slowly and stops at 30 cm. If it detects nobody ahead, it holds. The eyes show status throughout: amber while checking, green when verified, red on HOLD or ABORT.

The signal strength and round-trip times are simulated from distance and LoRa airtime. The ultrasonic readings are simulated unless a real dog is attached.

**Real robot dog (MechDog):** run `python3 -m rescue.console --dog 192.168.4.1` and the physical dog walks its part. It stops on HOLD, backs off on ABORT, and does a handshake at link-up. See [dog/README.md](dog/README.md) to set it up.

What you see for each packet:

- **Top-down map**: radio waves spread out from whoever transmits, and the packet travels with them. An eavesdropper in the middle hears every broadcast and records the band's reply. The drone's HUD box locks on in amber, green or cyan to match the feed.
- **Protocol flow**: the sender's build steps (nonce, slot, HMAC-SHA256, truncate, frame), then the receiver's checks in the same order the code runs them. A failed check stops the flow, and the remaining steps are greyed out.
- **Packet inspector**: all 32 bytes, colored by field, filling in as the packet is built.

Use the header controls to switch modes. **AUTO** plays the whole exchange. **STEP** waits at each step for **NEXT**, the Right arrow key or N, so you can talk over it. Speed can be set to 0.5x, 1x or 2x. You can also set both in the URL with `?mode=step&speed=2`. Server options: `--port 9000`, `--no-browser`.

## Other entry points

- **Real hardware:** see [firmware/README.md](firmware/README.md) for wiring, flashing, and running `python3 -m rescue.console --serial <port>` with an ESP32 + RFM95W band.
- **Wristband over Bluetooth:** the band firmware advertises as `Rescue-Band-xxxx`. Use **CONNECT BAND** in the console (or `--band ble`). Challenges, I'M ALIVE and the band's clock go over Bluetooth; range is about a room. The band stays silent to strangers on bad packets.
- **Flying drone (Ryze Tello):** join the Tello's Wi-Fi, then add `--drone tello`. It takes off and flies 1 m to the far line, hovers through the checks, and lands when linked up. EMERGENCY STOP (or the X key) cuts the motors. Test alone with `python3 drone/tello.py battery`.
- **Drone radio (SDR):** see [sdr/README.md](sdr/README.md). Start `sdr/sdr_bridge.py`, then `python3 -m rescue.console --sdr`. The dog's Feather (`firmware`, env `dog_feather`) then runs the near check over real LoRa.

- `python3 -m rescue.interrogator` runs the same exchange as a terminal demo.
- `scripts/ci.sh` runs every check (pytest, through `uv` if pytest isn't installed).
- Enable the pre-push hook once per clone: `git config core.hooksPath .githooks`.

The protocol is documented at the top of `rescue/protocol.py`, and `CLAUDE.md` has the hardware plan.
