# Paired periodic check-in: Feather RFM69HCW

The dog carries a **Feather 32u4 RFM69HCW or RFM9x/SX1276**. RFM9x runs
**FSK**, not LoRa modulation, to match the wristband. Each wristband uses a **XIAO ESP32-S3**
for authentication, button and human cues, plus a matching Feather RFM69HCW modem.
A bare Feather wristband without the XIAO needs different firmware.

## Identity and pairing

- A wristband ID is the XIAO's full factory eFuse MAC, represented as 16 hex digits.
  It is stable across firmware updates. It is not the Bluetooth name or address.
- Read it on USB at 115200: send `#I` (newline). The response is
  `#PAIR_ID <16 hex digits> paired=0|1`. It is also printed at startup.
- Choose a nonzero 64-bit hex dog ID. Each registered dog–band pair gets a different
  random 128-bit pair key. Provisioning creates a separate shared key
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
| Dog Feather RFM69HCW | `dog-secrets.h` | `dog_feather_rfm69` |
| Dog Feather RFM9x / SX1276 | `dog-secrets.h` | `dog_feather_periodic_rfm9x` |
| Every wristband Feather modem | `radio-secrets.h` | `feather_radio` |
| Each wristband XIAO | `band-<THAT_XIAO_ID>-secrets.h` | `band_xiao_feather` |

From `firmware/`, build with `pio run -e TARGET`, then upload with
`pio run -e TARGET -t upload --upload-port YOUR_DEVICE_PORT`.
Select the target matching the **label on the radio board**. `RFM9x LoRa`
is not an RFM69. The existing `dog_feather` target is the older near-check
workflow; use `dog_feather_periodic_rfm9x` for paired periodic check-in.
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
3. The addressed band verifies the dog ID, its own ID and the AES-CCM authentication tag.
   It signals the wearer and sends an automatic radio acknowledgement.
   `#BAND_SEEN band=<ID> wait=button` does **not** mean the wearer has responded.
4. The wearer clicks/releases **twice**, then presses a **third time**, holds
   for at least **2 seconds**, and releases. Each short click must be <=500 ms;
   gaps between presses must be <=1.5 seconds. The whole gesture must finish
   within the 30-second request window. The band sends an authenticated confirmation.
   The dog logs `#PERSON_CONFIRMED band=<ID> next=2h` and sends an encrypted, authenticated ACK.
   Only a matching, authenticated ACK makes the band log `#CONFIRMED led=5s`.
   The status LED is normally off. An authenticated addressed request turns it
   on while awaiting the gesture and the dog ACK; it turns off exactly 5 seconds
   after the first valid ACK, or when an unanswered request/ACK wait expires.
   Idle button presses do not turn the status LED on. Duplicate ACKs
   cannot prolong that indication. The modem LED mirrors the XIAO status.
   The dog LED is also on for 5 seconds after a newly accepted confirmation.
5. The dog skips **that band for two hours starting at receipt of confirmation**.
   The band also suppresses new periodic prompts for two hours after the authenticated
   ACK arrives. While awaiting ACK it suppresses new prompts for at most 30 seconds. Other bands remain eligible throughout. At expiry the next scheduled
   turn starts a fresh challenge.
6. The band retries its confirmation every 2 seconds for at most 30 seconds
   while waiting for ACK. The dog acknowledges duplicate confirmations without
   extending the two-hour cooldown. If the button packet was lost, a retry of
   that same challenge elicits the
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
indicates a newly accepted confirmation for five seconds; it is not live presence.
No robot movement or web-console integration is added by this standalone build.

## Protocol and validation

Periodic wire **version 2** uses AES-128-CCM with an 8-byte authentication tag.
Packets remain 32 bytes: TYPE(1), VERSION(1), COUNTER(4), SLOT16(2), TAG(8),
ENCRYPTED_IDS(16). Types 6/7/8/9 are request/radio reply/human confirmation/dog ACK.
The dog and band IDs are encrypted; the clear 8-byte header is authenticated as
associated data. Type, counter, time-slot fragment and traffic timing remain visible.
Encryption does not hide radio presence, prevent jamming or prove physical distance.

A per-pair AES key is the first 16 bytes of HMAC-SHA256(pair key,
`RESCUE-CCM-v2` || dog ID || band ID), using big-endian 64-bit IDs. The 13-byte CCM
nonce is type || counter32 || full hour-slot32 || `52 42 02 00`; integers are
big-endian. Message types separate the two transmission directions. Retries reuse
exactly the same authenticated content, counter and slot, so ciphertext is identical.
Paired devices reject periodic version 1 and legacy plaintext radio challenges;
there is no automatic downgrade. Legacy types 1–5 remain for unpaired demo builds.
USB/BLE `#P` is refused for paired periodic requests: use the physical gesture.

### Persistent nonce counter

The dog owns **EEPROM bytes 0–31** for a two-slot journal. It reserves blocks of
256 counters before transmission and skips unused reserved counters on restart.
Only a fresh exchange consumes a counter; retransmissions reuse the original.
Failed storage checks or exhaustion block broadcasts (`counter=BLOCKED`). Erased
EEPROM initializes once; corrupt non-erased storage fails closed. `#F`, clock sync
and cooldown reset do not reset the counter. This is a uniqueness counter, not a
random-number generator. The journal tolerates interrupted writes; it is not
physical tamper protection.

**Do not erase, restore an old backup of, or clone dog EEPROM while retaining
its pair keys.** Reprovision affected pairs with new random keys before reuse if
EEPROM was erased/restored or a dog was cloned/replaced. Keep only one active dog
for each provisioned identity/key set. Normal flashing must preserve EEPROM.
Pair keys are compiled into firmware; physical key extraction, authenticated BLE
control and persistent replay state on the wristband are separate limitations.
The fixed gesture checks intent; it is not a secret password.

### Upgrade existing paired devices

Reflash **dog and XIAO together**, copying their existing private headers before
each build. The wristband Feather modem transports opaque 32-byte packets and
needs no encryption-specific update. Existing pair keys can stay because the AES
key uses a separate derivation context. Do not regenerate private headers just
for this update, and preserve dog EEPROM. Sync clocks again after flashing.
A mixed old/new pair will not complete check-in.

Host tests compare the production CCM implementation with RFC 3610 vector 1 and
Python cryptography AESCCM, including altered ciphertext/header/tag and wrong keys.
Tests also cover interrupted EEPROM writes, restart allocation, routing, gesture,
ACK retries and independent cooldowns. A manual AVR build using GCC 14.2,
Arduino AVR core 1.8.6 and the Adafruit Feather variant fits the RFM9x target
(23,674 bytes Flash; 1,048 bytes static SRAM with one test pair; 24,304 bytes
Flash and 1,145 bytes static SRAM with four test pairs). Heap and stack
need additional SRAM. This is not the pinned PlatformIO toolchain; the PlatformIO
registry is blocked in the development environment. New encrypted firmware still
needs end-to-end testing on the actual dog and wristband; 100 m is not validated.

## Updating to the gesture and ACK version

Update **all three** firmware roles with the existing private headers: dog,
XIAO and wristband Feather modem. The hardware ID and pair keys stay unchanged.
Old dog firmware does not send ACK; with an old dog the new wristband cannot
show the five-second success indication. Power/charge LEDs or LEDs hardwired to
3.3 V are not firmware-controlled and can remain lit. This feature controls the
configured user/status LEDs only. Periodic prompts use a short nonblocking
buzzer/motor cue, not a boot light or repeated LED blinking.

## USB laptop viewer and bench reset

See [USB monitoring and automatic-start limitations](usb-monitor.md).
`checkin_monitor.py --port COM9` reads telemetry and automatically sets an unset
dog clock. It never clears cooldowns, confirms a wearer or moves the robot.
Firmware defaults to periodic sending after clock/pairing/radio readiness.
USB `#F` clears only local cooldown/pending state, retaining IDs, keys and replay
cache; clear both sides using `set_clocks.py --clear-wait`. Both roles advertise
`clear_wait=1` when available. The band refuses this reset through BLE.
