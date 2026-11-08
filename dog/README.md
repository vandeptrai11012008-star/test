# MechDog (HiWonder): Wi-Fi control from the laptop

This is a mini project to learn the MechDog and drive it wirelessly, before connecting it to the rescue band project.

## How the MechDog works

- **Body:** 8 servos, a hip and a knee on each leg. Walking gaits and balance are built into Hiwonder's firmware.
- **Brain:** an ESP32 running **MicroPython**. At power-on it runs `main.py`.
- **Hiwonder's MicroPython modules on the dog:**
  - `HW_MechDog.MechDog`: `move(step_mm, turn_deg)`, `set_default_pose()`, `action_run("sit_dowm")`, `set_pose(...)`, `homeostasis(True)` for self-balancing
  - `Hiwonder`: buzzer, LED, buttons
  - `Hiwonder_IIC`: the ultrasonic sensor with RGB lights, other I2C modules
  - `Hiwonder_BLE`: the Bluetooth link the phone app uses
- **Factory control:** the Hiwonder phone app over Bluetooth, the Hiwonder Python Editor over USB, and WonderCode blocks.
- **Power:** 7.4 V battery (1500 mAh), charged at 8.4 V. The servos are weak on a low battery.

## What this folder adds

| File | Runs on | What it does |
|---|---|---|
| `tools/probe.py` | dog | Read-only check of the firmware: modules, Wi-Fi, files |
| `onboard/main.py` | dog | Starts Wi-Fi and listens for commands on UDP port 9000 |
| `onboard/dogcmd.py` | dog | The commands, with limits and a **1 s safety stop** |
| `onboard/config.py` | dog | Wi-Fi: its own hotspot `MechDog-Rescue` (default) or join a network |
| `dogctl.py` | laptop | Send commands, or drive with the keyboard |
| `sim_dog.py` | laptop | A pretend dog running the same command code, for practice |

## Fastest path: Bluetooth, nothing installed on the dog

**Tested on a real MechDog (`mechdog_94`):**
- Battery and ultrasonic replies work.
- Walking and the stop code work.
- Eye colours and the countersign flash work.
- The handshake works.
- A full link-up completes with `python3 -m rescue.console --dog ble:mechdog_94`.
- **Final approach:** the dog walks in, switches to short steps inside 60 cm, and stops at **29 cm** (test from 2.3 m). It uses the median of 3 sensor readings to filter gait wobble. If the echo is lost up close, it stops rather than walking on blind.

The commands come from the factory program, which you can read over USB without changing anything (`mpremote connect <port> fs cp :main.py dog/backup/factory_main.py`; that folder is gitignored). It turned out to include a hidden eye-colour command, `CMD|4|3|r|g|b|$`, and the 15 action numbers below.

**Works over Bluetooth:**
- Walking in 8 directions, and stop
- `stand` and `balance`
- `sonar` (ultrasonic) and `battery`
- `rgb r g b` (eye colour)
- `code 101`, the countersign. The laptop times it with eye-colour commands.
- `avoid on|off`, the factory's own obstacle-avoidance walk
- `action <name>` for all 15 actions: `left_foot_kick`, `right_foot_kick`, `stand_four_legs`, `sit_dowm`, `go_prone`, `stand_two_legs`, `handshake`, `scrape_a_bow`, `nodding_motion`, `boxing`, `stretch_oneself`, `pee`, `press_up`, `rotation_pitch`, `rotation_roll`

**Not available:**
- `imu`: the factory program has no handler for it.
- `beep` and `pose`: no factory command for them.

**Behaviour notes from the factory code:**
- A walk continues until stop.
- Actions are ignored while walking, so the link sends stop first.
- If Bluetooth disconnects, the dog stops.
- The laptop link also stops the dog if no move arrives for 1 s.

**USB:** the data connection is the USB port on the controller board, not the charging socket. It appears as `/dev/cu.usbserial-*`.

You only need the Wi-Fi route below if you want to run your own code on the dog.

## Wi-Fi route (our script on the dog)

## Steps

**0. Check the dog works as shipped**
- Charge it fully.
- Power it on and drive it with the Hiwonder app over Bluetooth.
- If it walks badly, do Hiwonder's servo calibration first.

**1. Try the laptop tool with the pretend dog (no robot needed)**
```bash
python3 dog/sim_dog.py                          # terminal 1
python3 dog/dogctl.py --host 127.0.0.1 drive    # terminal 2: W/A/S/D, space, 1-4, q
```

**2. Connect the dog over USB and check its firmware**
```bash
pip install mpremote                            # official MicroPython tool
mpremote connect list                           # find the port, e.g. /dev/cu.usbserial-110
mpremote connect /dev/cu.usbserial-110 run dog/tools/probe.py
```
- Close the Hiwonder Python Editor first, because only one program can hold the port.
- The probe changes nothing. Paste its output back to me. It tells us whether `network` (Wi-Fi) is in the firmware, and the exact method names of `MechDog`.

**3. Back up the factory program (do not skip this)**
```bash
mkdir -p dog/backup
mpremote connect /dev/cu.usbserial-110 cp :main.py dog/backup/factory_main.py
```
`dog/backup/` is gitignored, so Hiwonder's code stays on your machine.

**4. Install the Wi-Fi server on the dog**
```bash
P=/dev/cu.usbserial-110
mpremote connect $P cp dog/onboard/dogcmd.py :dogcmd.py
mpremote connect $P cp dog/onboard/config.py :config.py
mpremote connect $P cp dog/onboard/main.py :main.py
mpremote connect $P reset
```
- Watch the boot message with `mpremote connect $P repl`. You should see `MechDog server: hotspot MechDog-Rescue, dog IP 192.168.4.1`, followed by a short beep. Exit with Ctrl-].

**5. Drive it wirelessly**
- Put the dog on the floor with space around it.
- Join the Wi-Fi **MechDog-Rescue** (password `mechdog123`) from the laptop. The laptop has no internet while it's on this network.
- Then:
```bash
python3 dog/dogctl.py ping                 # ok pong mechdog
python3 dog/dogctl.py action sit_dowm
python3 dog/dogctl.py stand
python3 dog/dogctl.py drive                # keyboard driving
```
- To keep your internet, put your phone hotspot's name and password in `onboard/config.py`. Copy it to the dog again; it joins that network and prints its IP at boot. Then use `--host <that IP>`.

**Put the factory program back** (the phone app needs it):
```bash
mpremote connect /dev/cu.usbserial-110 cp dog/backup/factory_main.py :main.py
mpremote connect /dev/cu.usbserial-110 reset
```

## Commands

`ping`, `move <step_mm> <turn_deg>`, `stop`, `stand`, `action <name>`, `pose x y z roll pitch yaw [ms]`, `balance on|off`, `sonar`, `rgb r g b`, `beep [hz] [ms]`, `code <bits> [r g b]` (flash a 3-pulse code on the eyes, e.g. `code 101`), `status`

- **Limits:** step is limited to ±100 mm, turn to ±45°, and pose to ±30.
- **Safety stop:** while walking, the dog stops by itself if no command arrives for 1 s. `drive` re-sends the move every 150 ms, so letting go of the keyboard (or losing Wi-Fi) stops the dog.
- **Actions** named in Hiwonder's docs include `sit_dowm` (Hiwonder's spelling), `stand_four_legs`, `go_prone`, `handshake`, `left_foot_kick` and `rotation_roll`.

## Unconfirmed until the probe runs

- **Wi-Fi in the firmware:** whether Hiwonder's firmware includes MicroPython's `network` module. Standard ESP32 builds do. If it's missing, we switch to Bluetooth through `Hiwonder_BLE`.
- **Hotspot and app together:** Hiwonder's own Wi-Fi lesson uses an add-on module. Running the ESP32's built-in Wi-Fi alongside the factory firmware hasn't been tried here.
- **Model differences:** method names come from Hiwonder's docs for MechDog / MechDog Pro. The probe prints the real list.

## In the link-up

Run `python3 -m rescue.console --dog <dog IP>` and start a link-up. The dog uses the sensors it already has:

- **Eyes (RGB on the ultrasonic module):** amber while checking, green when verified, red on HOLD or ABORT. After the near check passes, the eyes flash this hour's code (`code` command) as a countersign for the person.
- **Ultrasonic:** on the final approach the dog walks forward at `move 40 0`, reads `sonar` about 5 times a second, and stops at 30 cm. If it detects nothing within 10 s, it holds. Without the module, it falls back to a short timed walk.
- **Movement:** it walks in during its approach, stops on HOLD, backs off on ABORT, and does a handshake at link-up.
