# Rocket Avionics - System Scripts

Two computers, two deployments. Nothing here runs on your Mac; these are files
you transfer to the Raspberry Pis.

- **TX = Raspberry Pi Zero 2 W** (on the rocket): reads GPS, MPU-6050,
  HMC5883L and BMP280, records the ov5647 camera locally, transmits telemetry
  over RFM9x LoRa at 915 MHz. Autostarts on boot via systemd. This board is
  already fully set up (venv, apt packages, UART, camera); the TX setup below
  is kept so it can be rebuilt from scratch.
- **RX = Raspberry Pi 3** (ground station): receives telemetry, decodes it,
  stores it in SQLite, and shows a live display. Launched by a desktop
  double-click. This board has NOT been set up yet; follow the from-scratch
  guide in the RX section.

Every script, service and launcher runs on the venv interpreter
`/home/pi/libs-venv/bin/python3` (both Pis, same layout). Current Raspberry Pi
OS blocks system-wide pip (PEP 668), so the system `python3` cannot import
`pynmea2`, `smbus2`, `bmp280` or `pyLoraRFM9x`.

> **Hostnames.** Both Pis are currently named `raspberrypi.local`. Two hosts
> with the same mDNS name on one network collide, so `.local` lookups are
> ambiguous whenever both are powered on. The scp examples below use
> `pi@raspberrypi.local` for both; either power on one Pi at a time, use IP
> addresses, or (recommended) rename the Pi 3 during its setup:
> `sudo raspi-config` -> System Options -> Hostname -> e.g. `rocket-rx`
> (then it is `rocket-rx.local`).

## Files and where they go

| File                       | Goes to      | Purpose                                                   |
|----------------------------|--------------|-----------------------------------------------------------|
| `TX_Telemetry.py`          | Pi Zero 2 W  | Flight script: sensors + camera + LoRa transmit           |
| `rocket-tx.service`        | Pi Zero 2 W  | systemd unit for autostart on boot (venv interpreter)     |
| `install_tx_autostart.sh`  | Pi Zero 2 W  | One-time installer for the autostart service              |
| `RX_Temenetry.py`          | Pi 3         | All-in-one receiver + decode + DB + live display          |
| `telemetry_decode.py`      | Pi 3         | Decode module (packet contract reference; used by tests)  |
| `telemetry_db.py`          | Pi 3         | SQLite storage module (used by `playback.py`)             |
| `playback.py`              | Pi 3         | Post-flight replay from the database                      |
| `start_rx.sh`              | Pi 3         | Launcher that `Rocket-RX.desktop` calls (terminal dashboard) |
| `make_rx_gui_shortcut.sh`  | Pi 3         | Generates a second desktop icon that opens the Tkinter GUI |
| `Rocket-RX.desktop`        | Pi 3         | Double-click desktop launcher                             |

All deploy paths below assume the project lives at
`/home/pi/Desktop/Avionics for Rocket/` on each Pi. If you put it elsewhere,
update the absolute paths in `rocket-tx.service`, `Rocket-RX.desktop`, and the
`--db` default (currently `/home/pi/Desktop/RxTelemetry.db`).

---

## TX side (Pi Zero 2 W) - autostart on boot

The Zero 2 W is already set up. Steps 1-2 are only needed for a rebuild (new
SD card). Steps 3-5 are what you run after copying updated scripts.

### 1. Copy the project to the Pi Zero 2 W

From your Mac (TX powered on, RX off, or use the TX's IP address):

```bash
scp -r "/Users/ajarretm/Desktop/Avionics for Rocket" pi@raspberrypi.local:/home/pi/Desktop/
```

### 2. One-time OS and Python setup (rebuild only)

**UART for the GPS.** By default Bluetooth owns the stable PL011 UART and the
header gets the clock-unstable mini-UART, which produces binary garbage at
9600 baud. Edit the real boot files (`/boot/config.txt` is only a stub):

```bash
sudo nano /boot/firmware/config.txt      # under [all] add:
#   enable_uart=1
#   dtoverlay=disable-bt
sudo nano /boot/firmware/cmdline.txt     # remove  console=serial0,115200   (keep console=tty1)
sudo systemctl stop serial-getty@ttyAMA0.service
sudo systemctl disable serial-getty@ttyAMA0.service
sudo reboot
ls -l /dev/serial0                       # -> ttyAMA0 ;  /dev/ttyAMA0 is crw-rw---- root dialout
```

**I2C and SPI.** `sudo raspi-config` -> Interface Options -> enable I2C and
SPI. Check the bus: `i2cdetect -y 1` must show `68` (MPU-6050), `1e`
(HMC5883L) and `76` (BMP280). The camera is auto-detected:
`rpicam-hello --list-cameras` lists the ov5647; `rpicam-still -o test.jpg`
tests the capture path.

**Python environment.** apt packages first, then a venv that can also see the
apt-installed `picamera2`:

```bash
sudo apt update
sudo apt install -y swig python3-dev liblgpio-dev python3-picamera2 i2c-tools python3-serial
python3 -m venv ~/libs-venv
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' ~/libs-venv/pyvenv.cfg
source ~/libs-venv/bin/activate
pip install pynmea2 pyserial smbus2 bmp280 pyLoraRFM9x
deactivate
```

Notes: `pip` inside the activated venv is the right installer; `pip3` from a
normal shell hits the system Python and is blocked. The pip name is `pyserial`
(import name `serial`); the barometer package is `bmp280` (not
`bmp280-python`); `pyLoraRFM9x` needs `swig python3-dev liblgpio-dev` to build
lgpio. Do not `mv` the venv (its `bin/` scripts hardcode the path). Thonny:
Tools -> Options -> Interpreter -> Alternative Python 3 interpreter ->
`/home/pi/libs-venv/bin/python3`.

Import check (all five must print `ok`):

```bash
/home/pi/libs-venv/bin/python3 -c 'import serial, pynmea2, smbus2, bmp280, pyLoraRFM9x; from picamera2 import Picamera2; print("ok")'
```

### 3. Install the autostart service

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
chmod +x install_tx_autostart.sh
sudo ./install_tx_autostart.sh
```

The installer copies `rocket-tx.service` into `/etc/systemd/system/`, reloads
systemd, runs `systemd-analyze verify` on the installed unit, and enables the
service. It warns if `/home/pi/libs-venv/bin/python3` is missing. It refuses to
run on anything that is not Linux-with-systemd, so it is safe if accidentally
run on the Mac. Re-run it whenever `rocket-tx.service` changes.

### 4. Test without rebooting

```bash
sudo systemctl restart rocket-tx.service
journalctl -u rocket-tx.service -f      # watch live output, Ctrl+C to stop watching
journalctl -b | grep -i "ordering cycle"   # must print nothing
```

Then reboot to confirm it comes up on its own: `sudo reboot`.

Handy commands:

```bash
sudo systemctl status  rocket-tx.service    # current state
sudo systemctl stop    rocket-tx.service    # stop now (SIGTERM -> video file is finalised)
sudo systemctl disable rocket-tx.service    # turn OFF autostart
```

### 5. How the service behaves

- `ExecStart` uses `/home/pi/libs-venv/bin/python3`; `WorkingDirectory` is the
  `System Scripts` folder (pyLoraRFM9x/lgpio writes `.lgd-nfy*` files into the
  current directory).
- The script transmits for one flight window, `TX_FLIGHT_DURATION_S` (set to
  480 s = 8 minutes in the unit), then exits cleanly. `Restart=always`
  relaunches it after about 15 s (`RestartSec=5` + `ExecStartPre=/bin/sleep 10`).
  Each relaunch re-captures the BMP280 pad baseline (AGL re-zeroes) and starts
  a new video file. If a pad wait could straddle a window boundary, raise
  `TX_FLIGHT_DURATION_S` in the unit and re-run the installer.
- Every sensor init fails soft: a missing GPS / MPU-6050 / HMC5883L / BMP280 /
  camera is logged (`<sensor> unavailable: ...`) and the packet carries zeros
  or empty fields for it. The LoRa radio is the one hard failure (exit 1,
  then systemd restarts the service): without it there is no telemetry.
- Once per second the journal shows
  `t=... pkts=... rate=... Hz gps=fix sats=N baro=1011.7hPa agl=+0.0m i2c_err=0 tx_fail=0`.

### TX hardware (bench-verified on the Zero 2 W, 2026-10-07/08)

| Device      | Part                                    | Bus / port                                   | Address      |
|-------------|-----------------------------------------|----------------------------------------------|--------------|
| GPS         | NMEA receiver (`$GNGGA`/`$GPGGA`)       | UART GPIO 14/15 (pins 8/10), `/dev/serial0`, 9600 baud | n/a |
| IMU         | MPU-6050                                | I2C bus 1 (SDA GPIO 2 pin 3, SCL GPIO 3 pin 5) | `0x68`     |
| Magnetometer| HMC5883L (genuine; a QMC clone is 0x0d) | I2C bus 1                                    | `0x1e`       |
| Barometer   | BMP280 (chip id 0x58, 3.3 V)            | I2C bus 1                                    | `0x76`       |
| Radio       | RFM9x LoRa, Adafruit-style breakout     | SPI0 CE1 (`/dev/spidev0.1`)                  | 915 MHz      |
| Camera      | ov5647 Camera Module v1, 2592x1944      | CSI                                          | n/a          |

RFM9x wiring (SPI0):

| RFM pin  | Physical pin | BCM        |
|----------|--------------|------------|
| MOSI     | 19           | 10         |
| MISO     | 21           | 9          |
| SCK      | 23           | 11         |
| CS       | 26           | 7 (CE1)    |
| RST      | 22           | 25         |
| G0 (IRQ) | 29           | 5          |
| VIN      | 1            | 3.3 V      |
| GND      | 6            | GND        |

EN stays unconnected (pulled high on the board). Antenna on before transmit.

Radio parameters (TX and RX must match): 915 MHz,
`ModemConfig.Bw125Cr45Sf128` (SF7 / BW125 / CR4/5), `acks=False`, TX node
address 2 sending to RX node 1. TX power is **15 dBm** (user decision
2026-10-08; the bench test used 5-14 dBm).

Sensor configuration in the flight code:

- **Accelerometer +/-16 g**: the TX writes `ACCEL_CONFIG (0x1C) = 0x18`; the
  decoders divide by 2048 LSB/g. Gyro stays +/-2000 deg/s (16.4 LSB/deg/s).
  The first MPU-6050 sample after wake is discarded (raw temp 0 shows as
  exactly 36.53 C).
- **BMP280 in the packet**: temperature, absolute pressure, altitude vs
  1013.25 hPa and altitude above the pad. The first conversion after init is
  discarded (it reads like 724 hPa / 3009 m); the pad baseline is the mean of
  10 readings taken right after. Standby is set to 62.5 ms (~7 Hz updates)
  instead of the bench default 500 ms so AGL can track a rocket; confirm on
  the bench that `baro=` in the journal changes several times per second.
- **GPS**: only `GGA` sentences are kept (they carry fix, satellites, lat,
  lon, altitude); bytes are decoded as ASCII with errors ignored; one bad
  sentence never stops the loop.
- **Camera**: H.264 1280x720 @ 30 fps, 4 Mbps, rotated 180 degrees, written to
  `/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/flight_YYYY-MM-DD_HH-MM-SS.h264`
  (about 240 MB per 8 minutes). Convert on any machine with
  `ffmpeg -framerate 30 -i flight_....h264 -c copy flight.mp4`. Stills were
  bench-verified with `Grok_Cam_Test.py`; the video path uses the same
  picamera2 library and lifecycle but is **not yet bench-verified**.

### TX troubleshooting

| Symptom (journalctl)                                   | Cause / fix                                                                 |
|--------------------------------------------------------|-----------------------------------------------------------------------------|
| GPS bytes like `b'\xc0\xe3\x9c\x88...'` instead of `$GNGGA` | Mini-UART clock mismatch: `enable_uart=1` + `dtoverlay=disable-bt`, reboot |
| `Permission denied` on `/dev/ttyAMA0` or `/dev/serial0`| `serial-getty@ttyAMA0` is resetting perms: stop/disable it, remove `console=serial0,115200` |
| `AssertionError: LoRa initialization failed`           | SPI could not read the radio: CS (CE1, pin 26) / MOSI / MISO / SCK wiring, not frequency |
| `ModuleNotFoundError: No module named 'pynmea2'` (or smbus2, bmp280, pyLoraRFM9x) | Service not using the venv: `ExecStart` must be `/home/pi/libs-venv/bin/python3` |
| TEMP exactly 36.53 C on the RX                         | Raw register 0 (first sample after wake); the TX now discards it           |
| `BMP280 unavailable: ...`                              | Wiring / address: `i2cdetect -y 1` must show `76`; 3.3 V only              |
| RX shows `no fix` while the GPS has a fix              | TX must send GGA sentences only (fixed in this update); check `gps=` in the journal |
| `MPU-6050 WHO_AM_I unexpected`                         | Wrong chip at 0x68 or bus glitch; `i2cdetect -y 1`                         |

---

## RX side (Pi 3) - first-time setup, from scratch

The Pi 3 has never been configured. Do these once.

### 1. OS, interfaces, hostname

1. Flash current Raspberry Pi OS (desktop) with SSH enabled, user `pi`.
2. `sudo raspi-config`:
   - Interface Options -> SPI -> enable (I2C and serial are not needed on the RX).
   - System Options -> Hostname -> `rocket-rx` (avoids the mDNS collision with the TX).
   - Finish -> reboot.

### 2. Packages and the venv

```bash
sudo apt update
sudo apt install -y swig python3-dev liblgpio-dev python3-tk
python3 -m venv ~/libs-venv
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' ~/libs-venv/pyvenv.cfg
source ~/libs-venv/bin/activate
pip install pyLoraRFM9x pynmea2
deactivate
/home/pi/libs-venv/bin/python3 -c 'import pyLoraRFM9x, pynmea2, tkinter; print("ok")'
```

`include-system-site-packages = true` lets the venv see apt's `python3-tk`
(needed for `--gui`). No separate GPIO library is needed: pyLoraRFM9x uses
lgpio.

### 3. Copy the project to the Pi 3

From your Mac (RX powered on, TX off, or use the RX's IP / new hostname):

```bash
scp -r "/Users/ajarretm/Desktop/Avionics for Rocket" pi@raspberrypi.local:/home/pi/Desktop/
# after the rename:  pi@rocket-rx.local
```

### 4. Wire the RFM9x

Use the same pinout as the TX table above (SPI0: CS -> CE1 pin 26, G0 -> GPIO 5
pin 29, RST -> GPIO 25 pin 22, MOSI 19, MISO 21, SCK 23, 3.3 V pin 1, GND
pin 6, antenna attached). This is assumed identical to the TX but is
**unverified on the Pi 3**: an `AssertionError: LoRa initialization failed` on
the first run means check CS / MOSI / MISO / SCK.

### 5. Make the launchers and put the icon on the desktop

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
chmod +x start_rx.sh make_rx_gui_shortcut.sh
cp Rocket-RX.desktop /home/pi/Desktop/
chmod +x /home/pi/Desktop/Rocket-RX.desktop
./make_rx_gui_shortcut.sh        # optional: second icon that opens the Tkinter GUI
```

Now there is a **Rocket RX Telemetry** icon on the Pi 3 desktop. Double-click
it: a terminal opens running the receiver with the live dashboard. Close the
window (or Ctrl+C) to stop; it prints a session summary first. Both launchers
run `/home/pi/libs-venv/bin/python3` and print a clear error pointing back
here if the venv is missing.

> If double-click asks whether to execute or display, choose "Execute". On some
> desktops you may need: File Manager -> Edit -> Preferences -> General ->
> "Don't ask options on launch executable file".

### 6. Run modes

`start_rx.sh` launches the default terminal dashboard. To change the display,
edit the `"$RX_PYTHON" RX_Temenetry.py` line in `start_rx.sh`, or run by hand:

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
/home/pi/libs-venv/bin/python3 RX_Temenetry.py            # live terminal dashboard (default)
/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui      # Tkinter window (needs desktop + monitor)
/home/pi/libs-venv/bin/python3 RX_Temenetry.py --scroll   # plain scrolling log (good over flaky SSH)
```

The dashboard shows ACCEL, GYRO, TEMP, HEADING, BARO (absolute altitude, AGL,
hPa, C) and GPS (with satellite count), plus MAX alt / MAX baro AGL / MAX
|accel|.

### 7. Post-flight playback

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
/home/pi/libs-venv/bin/python3 playback.py --list              # list recorded flights
/home/pi/libs-venv/bin/python3 playback.py                     # replay the most recent flight
/home/pi/libs-venv/bin/python3 playback.py --session flight_20260102_143000 --speed 4
```

### What changed on the RX in this update (RX redeploy)

**RX redeploy required: YES.** The packet format and decoder changed, so the
TX and RX must be deployed together (a new RX rejects old 10-field packets and
an old RX would mis-scale the new ones). RX files that changed:

- `RX_Temenetry.py`: 14-field packet (+4 BMP280 floats), accelerometer scale
  2048 LSB/g (+/-16 g), declination -0.113 rad (Cocoa, FL), GPS fix quality
  and satellite count, BARO line and MAX baro AGL on every display, DB schema
  + migration, radio call in keyword form with explicit `freq=915` and
  `acks=False` (same effective settings as before), receive callback hardened.
- `telemetry_decode.py`, `telemetry_db.py`, `playback.py`: same decoder /
  schema changes; playback prints a `BARO :` line and the session max AGL.
- `start_rx.sh`, `make_rx_gui_shortcut.sh`: run `/home/pi/libs-venv/bin/python3`.
- `Rocket-RX.desktop`: comment line only.

Not changed: LoRa pins, frequency, modem config, node addresses, CLI flags,
display modes.

---

## Database

Both the receiver and playback use one SQLite file (default
`/home/pi/Desktop/RxTelemetry.db`). Each receiver run is a new `session_id`
(one flight), so many flights coexist in the same database.

The `telemetry` table gained four BMP280 columns in this update:
`baro_temp_c`, `baro_press_hpa`, `baro_alt_m`, `baro_agl_m`. An existing
database is migrated automatically the first time it is opened (older rows
read as `NULL`; playback simply omits the `BARO :` line for them).
