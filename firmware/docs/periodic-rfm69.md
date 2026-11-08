# Paired periodic check-in: Feather RFM69HCW

The dog carries a **Feather 32u4 RFM69HCW**. Each wristband uses a **XIAO ESP32-S3**
for authentication, button and human cues, plus a matching Feather RFM69HCW modem.
A bare Feather wristband without the XIAO needs different firmware.

## Identity and pairing

- A wristband ID is the XIAO's full factory eFuse MAC, represented as 16 hex digits.
  It is stable across firmware updates. It is not the Bluetooth name or address.
- Read it on USB at 115200: send `#I` (newline). The response is
  `#PAIR_ID <16 hex digits> paired=0|1`. It is also printed at startup.
- Choose a nonzero 64-bit hex dog ID. Each registered dog–band pair gets a different
  random 128-bit authentication key. Provisioning creates a separate shared key
  for the hopping schedule; learning that schedule key does not authenticate a pair.
- This 32u4 build supports up to **four registered wristbands**. IDs of other
  wristbands are ignored. Duplicate/zero IDs are rejected.
- Without pairing configuration, the dog reports `paired=0` and does not transmit
  beacons. An unpaired XIAO ignores periodic requests.

From the repository root, generate keys into a new directory **outside the checkout**:

```sh
python3 scripts/provision_periodic.py \
  --dog-id D001 \
  --band-id YOUR_FIRST_XIAO_HEX_ID \
  --band-id YOUR_SECOND_XIAO_HEX_ID \
  --out-dir /tmp/rescue-private-pairing
```

Use one `--band-id` for a single wristband. The script refuses an existing output
directory, never prints keys, and restricts directory/file permissions. Keep the
headers private and retain a secure backup if needed. Regenerating keys replaces
pairing and requires reflashing every affected device.

## Flash the matching private header onto each device

All builds read **`firmware/include/secrets.h`**, which is gitignored. Before each
build copy that device's generated header into that path; never commit the header.
Do not overwrite a pre-existing user secrets file without preserving it first.

| Device | Header to copy as `include/secrets.h` | Build target |
| --- | --- | --- |
| Dog Feather | `dog-secrets.h` | `dog_feather_rfm69` |
| Every wristband Feather modem | `radio-secrets.h` | `feather_radio` |
| Each wristband XIAO | `band-<THAT_XIAO_ID>-secrets.h` | `band_xiao_feather` |

From `firmware/`, build with `pio run -e TARGET`, then upload with
`pio run -e TARGET -t upload --upload-port YOUR_DEVICE_PORT`.
The existing `dog_feather` target is for **RFM9x/SX1276**, not this RFM69HCW build.
Both the dog and XIAO firmware must be updated: legacy challenges do not carry IDs.

The existing RF profile is **903–927 MHz** and requires 900/915 MHz boards and
matching antennas; do not use this profile on 433 MHz hardware. A range of 100 m
is a field-test target, not a measured distance or guaranteed coverage boundary.

## Synchronize and run

Plug the dog Feather and all wristband XIAOs into USB. From the repository root:

```sh
uv run --with pyserial python scripts/set_clocks.py --auto 2
```

Clocks must be synchronized for hopping. Resetting the dog loses its clock and
stops transmission until re-synchronized. Keep boards powered if disconnecting
USB; synchronize again after a reset. A XIAO forwards its time to its radio modem.

## Behaviour

1. With radio, pairing and clock ready, the dog sends one addressed challenge
   every **2 seconds**, rotating among registered bands that need a response.
   With N eligible bands, each is normally addressed every 2 × N seconds.
2. Each band has a **30-second** response window. The nonce stays the same across
   retries, so retries do not repeatedly vibrate the wristband.
3. The addressed band verifies the dog ID, its own ID and the pair-key HMAC.
   It signals the wearer and sends an automatic radio acknowledgement.
   `#BAND_SEEN band=<ID> wait=button` does **not** mean the wearer has responded.
4. The wearer presses the button. The band sends an authenticated confirmation.
   The dog logs `#PERSON_CONFIRMED band=<ID> next=2h`.
5. The dog skips **that band for two hours starting at receipt of confirmation**.
   The band also suppresses new periodic prompts for two hours from the button
   press. Other bands remain eligible throughout. At expiry the next scheduled
   turn starts a fresh challenge.
6. If the button packet was lost, a retry of that same challenge elicits the
   confirmation again without another cue or button press. A new nonce cannot
   reuse a prior confirmation. Duplicate confirmations do not extend the cooldown.

The two-hour cooldown controls when to ask again. It is **not a two-hour guarantee
of proximity, link availability or wearer condition**. Timers are monotonic and
survive wall-clock adjustments but are currently in RAM: a reboot clears that
board's cooldown. A powered wristband still suppresses prompts if only the dog
restarts. Persistent cooldown across all-device power loss is not implemented.

## USB controls and status

| Command | Meaning |
| --- | --- |
| `#T <unix_seconds>.<milliseconds>` | Set the hopping clock; normally use the helper. |
| `#A 2` | Set the spacing between addressed transmissions to 2 seconds. |
| `#A 0` | Disable broadcasts and discard pending exchanges; retain two-hour cooldowns. |
| `#A 10` | Use 10-second spacing. Valid range: 0–3600. |
| `#?` on dog | Radio, clock, interval, peer count and each band's state. |
| `#I` on XIAO | Factory band ID and pairing-configured flag. |

Interval changes last until reboot. A large interval or many peers can exceed the
30-second response window; keep the default 2 seconds for normal use. The dog LED
indicates at least one band has a check-in cooldown; it is not live presence.
No robot movement or web-console integration is added by this standalone build.

## Protocol and validation

Periodic packets are 32 bytes: TYPE(1), VERSION(1), NONCE(4), SLOT16(2), HMAC8(8),
DOG_ID(8), BAND_ID(8). Types 6/7/8 are request/radio reply/human confirmation.
HMAC-SHA256 truncated to 8 bytes covers TYPE, NONCE, full slot and **both IDs**.
Legacy types 1–5 are unchanged. The IDs and contents are authenticated, **not encrypted**.
USB/BLE `#P` can still request a button response; physical-button-only enforcement
and BLE control authorization remain separate changes. Pair keys are compiled
into firmware; secure storage against physical extraction is not implemented.

Host tests cover two distinct pairs, routing, tampered IDs, wrong keys and nonces,
lost confirmations, retries without repeated cues, the exact two-hour boundary,
independent cooldowns, expired button windows, interval changes and timer rollover.
Provisioning tests check distinct private keys, permissions and no overwrites.
Real radio interoperability, full board build size and 100 m range still need
hardware/toolchain validation. The AVR nonce source remains a prototype entropy
pool, not a hardware CSPRNG.
