# Responsible use

This repository is a hackathon prototype of a check-in wristband for rescue and
responder teams. It is published for education, research and authorized demonstration only.

**This project does not support, and must not be used for, any unlicensed, unlawful or
unethical purpose.** The authors accept no responsibility for misuse.

## Hackathon prototype: no warranty, use at your own risk

- This project was made quickly at a hackathon. It has **not** been tested, validated or
  certified for any real-world, field, rescue, safety-critical or life-safety use, and it has
  not been approved by the FCC or any other authority. Never rely on it to identify, locate,
  protect or rescue anyone.
- It is provided **"as is", without warranty of any kind**, express or implied.
- **Anyone who uses, builds, modifies or operates any part of it does so at their own risk** and
  is solely responsible for that use, for complying with every applicable law and regulation,
  and for any harm, loss, damage or interference that results.
- To the maximum extent permitted by law, the authors and contributors are **not liable** for any
  claim, damages or other liability arising from this project or its use.
- If you do not accept these terms, do not use the project. Full text: [DISCLAIMER.md](DISCLAIMER.md).

## Radio transmitters (SDRs and LoRa boards)

Software-defined radios (SDRs) can transmit on almost any
frequency, at power levels that may exceed what the law allows without a license. LoRa boards
(RFM95/RFM9x, Feather RFM9x) are transmitters too.

- **Do not transmit with an SDR unless you are allowed to.** Follow the radio regulations
  where you are, for example FCC rules (47 CFR Part 15) in the US, Ofcom in the UK,
  ISED in Canada, or the ETSI / national rules in the EU. Some uses require a license, such
  as an amateur radio license, a lab or test license, or an experimental license.
- **Stay inside the band and power you are permitted.** This project uses 915 MHz LoRa,
  which is license-free in the US only within Part 15 limits. Other regions use different
  bands (868 MHz in the EU and UK) and limits. Check before you transmit, and change
  `LORA_FREQ_MHZ` in `firmware/include/config.h` and `--freq` in `sdr/sdr_bridge.py`.
- **Use the lowest power that works.** Indoors, a few metres needs very little. Raise
  `--tx-atten` on the SDR bridge and lower `LORA_POWER_DBM` in the firmware when you can.
  Keep an antenna on every transmitter.
- **An SDR is not a certified product.** Development boards are not approved transmitters
  in most countries. You are responsible for anything you transmit with one.

## Never use this code to

- jam, block or interfere with any radio, including Wi-Fi, Bluetooth, GPS, cellular,
  emergency, aviation or drone control links;
- spoof, replay, intercept or take over devices, systems or drones you do not own and are
  not explicitly authorized to test;
- target, track or harm people;
- get around any law, license condition or safety rule.

## Drones and robots

Fly and drive only where it is legal and safe: follow your aviation authority's rules (in the
US, the FAA rules and the TRUST certificate for recreational flying), keep people clear of
propellers and moving robots, and keep a manual override and emergency stop within reach.

## Not legal advice

This notice is not legal advice. If you are unsure whether a use is allowed, ask your national
radio regulator or a qualified professional before you transmit.
