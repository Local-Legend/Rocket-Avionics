# Rocket Avionics Project

Always-included steering for the "Avionics for Rocket" workspace. This file is the
project's persistent memory for the agent: hardware facts, source-of-truth rules,
and a running history of decisions and work. Update it whenever something durable
is learned or decided.

## Source-of-truth rule (from the user, 2026-10-08)

Files whose names start with `Grok_` are KNOWN-WORKING scripts, bench-tested
against the real hardware:

- `Grok_Cam_Test.py`
- `Grok_GPSTest.py`
- `Grok_TX_test.py`

When any other file disagrees with a `Grok_` file (pins, I2C addresses, UART
device paths, baud rates, library choice, init sequences, radio parameters,
timing), the `Grok_` file wins. Treat the conflicting file as suspect and fix it
to match, never the other way around.

## Workspace layout

```
Avionics for Rocket/
  *.py                      Root-level component scripts (bench tests / early modules)
  compass calibration.txt   Magnetometer calibration offsets
  System Scripts/           Deployable flight TX and ground-station RX software
    TX_Telemetry.py         Flight computer transmitter loop
    RX_Temenetry.py         Ground station receiver (filename misspelling is real, keep it)
    telemetry_decode.py     Packet decoding
    telemetry_db.py         Telemetry storage
    playback.py             Replay of recorded telemetry
    rocket-tx.service       systemd unit for TX autostart
    install_tx_autostart.sh Installs the service
    start_rx.sh, make_rx_gui_shortcut.sh, Rocket-RX.desktop   RX launch helpers
    README.md               Ops instructions
  3D Prints/                SolidWorks parts, STL zips, MPU-6000 register map PDF
  .agents/tasks/            Investigation reports written by workflow agents
```

Not a git repository as of 2026-10-08. The path contains spaces; always quote it
in shell commands.

## System architecture (two Pis)

- TX, on the rocket: Raspberry Pi Zero 2 W. Reads GPS, MPU-6050, HMC5883L,
  BMP280, records the ov5647 camera locally, transmits over RFM9x LoRa.
  Autostarts via systemd. ALL the hardware that was re-bench-tested in the Grok
  session lives on this side; every hardware fact in this file is TX-side.
- RX, ground station: Raspberry Pi 3. RFM9x LoRa receive, decode, SQLite, live
  display (terminal / Tkinter / scroll), post-flight playback. Launched from a
  desktop shortcut. The Pi 3, venv, radio stack, GUI, and 7 Hz BW500 end-to-end
  receive path are set up and hardware-tested; its older Buster/Python 3.7
  environment remains a maintenance constraint. The user wants to be told
  explicitly whenever an RX redeploy is required.

## Target platform

- Verified over SSH on 2026-10-08:
  - TX `pi@192.168.1.172`: Raspberry Pi Zero 2 W Rev 1.0, Debian 13 Trixie,
    kernel 6.18 aarch64, Python 3.13.5, `/home/pi/libs-venv` present,
    `/dev/serial0 -> /dev/ttyAMA0`, I2C-1 and SPI0 enabled, project present.
  - RX `pi@192.168.1.101`: Raspberry Pi 3 Model B Plus Rev 1.3, Raspbian 10
    Buster, kernel 5.10 armv7l, Python 3.7.3, `/home/pi/libs-venv` created
    2026-10-08 with system packages visible, I2C-1 and SPI0 enabled, project
    present. Installed/verified RX stack: pyLoraRFM9x 0.9.2 (legacy constructor,
    RPi.GPIO backend), pynmea2 1.19.0, spidev 3.5, pyserial 3.4, RPi.GPIO 0.7.0,
    tkinter. `RX_Temenetry.py` supports both pyLoraRFM9x 0.9.x and 1.0+ APIs.
    RX code MUST remain Python 3.7-compatible unless the Pi 3 OS is upgraded.
  - Both hostnames are `raspberrypi`; always use IP addresses until one is renamed.

- Original target: Raspberry Pi Zero (first generation, BCM2835, single-core
  ARMv6, 512 MB). The scripts were written for and first run on that board.
- Current TX target: Raspberry Pi Zero 2 W (RP3A0 / BCM2710A1, quad-core
  Cortex-A53 ARMv8, 512 MB, Wi-Fi + Bluetooth). All new TX work targets this board.
- OS: current Raspberry Pi OS (boot files live in `/boot/firmware/`, `rpicam-*`
  camera tools, PEP 668 blocks system-wide pip). The venv contains `python3.13`,
  which points at Raspberry Pi OS Trixie (Debian 13). Hostname `raspberrypi.local`,
  user `pi` (in groups `dialout` and `i2c`).
- Python environment on the Pi: venv at `/home/pi/libs-venv` with
  `include-system-site-packages = true` (so apt-installed `picamera2` is visible).
  Interpreter for every script, service, and Thonny: `/home/pi/libs-venv/bin/python3`.
  Do not `mv` the venv; its `bin/` scripts hardcode the path. An unused `gps-venv`
  also exists.
  - pip (inside the venv): `pynmea2`, `pyserial` (import name `serial`), `smbus2`
    (import as `import smbus2 as smbus`), `bmp280` (not `bmp280-python`),
    `pyLoraRFM9x` (needs `sudo apt install swig python3-dev liblgpio-dev` first).
  - apt: `python3-picamera2`, `i2c-tools`, `python3-serial`.
- Project location on the Pi: `/home/pi/Desktop/Avionics for Rocket/`. Transfer with
  `scp -r` (quote the path; SSH enabled on the Pi; VNC file transfer is unavailable).
- Migration notes, original Zero to Zero 2 W: four Cortex-A53 cores vs one ARMv6
  core, so `sleep()`-tuned loops may run faster than intended; higher current draw
  matters for battery sizing; `RPi.GPIO`, `gpiozero`, `smbus2`, `pyserial`, `spidev`
  all still work on this board.

## Hardware and software facts

Source: the user's Grok bench-session transcript (`avionics-conversation.pdf`,
exported 2026-10-08) plus the `Grok_*` scripts. Bench location is Cocoa, FL
(~28.3208 N, 80.7416 W); local magnetic declination about -6.5 deg (-0.113 rad).

### Device, bus, address table

| Device | Part | Bus | Address / port | Notes |
|---|---|---|---|---|
| GPS | NMEA receiver, `$GN`/`$GP` sentences | UART (GPIO 14/15, phys 8/10) | `/dev/serial0` -> `/dev/ttyAMA0`, 9600 baud | Requires the UART config below |
| IMU | MPU-6050 | I2C bus 1 (SDA GPIO 2 phys 3, SCL GPIO 3 phys 5) | `0x68` | WHO_AM_I reg `0x75` = `0x68`; PWR_MGMT_1 `0x6B` = 0 after wake |
| Magnetometer | HMC5883L (genuine, not QMC5883L) | I2C bus 1 | `0x1e` | Data register order is X, Z, Y. QMC clone would be `0x0d` |
| Barometer | BMP280 (no humidity) | I2C bus 1, 3.3 V | `0x76` | Chip id reg `0xD0` = `0x58` (BME280 would be `0x60`) |
| Radio | RFM9x LoRa, Adafruit-style breakout | SPI0, CE1 | `/dev/spidev0.1` | 915 MHz (US) |
| Camera | ov5647, Camera Module v1, 2592x1944 | CSI | n/a | `picamera2` only; legacy `picamera` does not work |

All three I2C devices share SDA/SCL; three breakout pull-up sets are fine. Keep the
magnetometer away from power wires.

### RFM9x LoRa wiring (SPI0)

| RFM pin | Physical pin | BCM |
|---|---|---|
| MOSI | 19 | 10 |
| MISO | 21 | 9 |
| SCK | 23 | 11 |
| CS | 26 | 7 (CE1) |
| RST | 22 | 25 |
| G0 (IRQ) | 29 | 5 |
| VIN | 1 | 3.3 V |
| GND | 6 | GND |

EN is pulled high on the board and stays unconnected. Antenna must be attached
before transmitting (915 MHz whip or 78 mm wire). Current deployable init (the original Grok bench reference used BW125; the user
selected BW500 on 2026-10-27 and both installed enum members were verified):

```python
from pyLoraRFM9x import LoRa, ModemConfig
lora = LoRa(spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0,
            my_address=1, freq=915, tx_power=14,
            modem_config=ModemConfig.Bw500Cr45Sf128, acks=False)
lora.send("hello", 255)   # 255 = broadcast
```

`AssertionError: LoRa initialization failed` means SPI could not read back sleep
mode: a CS/MOSI/MISO/SCK wiring problem, not a frequency problem. Both ends must
use the same `ModemConfig`. "sent" only means the bytes reached the radio.

### UART / GPS configuration (required on the Zero 2 W)

Bluetooth owns the PL011 by default, leaving the clock-unstable mini-UART on the
header. Required one-time setup:

- `/boot/firmware/config.txt` under `[all]`: `enable_uart=1` and `dtoverlay=disable-bt`
- `/boot/firmware/cmdline.txt`: remove `console=serial0,115200` (keep `console=tty1`)
- `sudo systemctl stop serial-getty@ttyAMA0.service && sudo systemctl disable serial-getty@ttyAMA0.service`
- Reboot. Then `/dev/serial0 -> ttyAMA0`, perms `crw-rw---- root dialout`.

Symptoms when this is wrong: binary garbage (`b'\xc0\xe3...'`) instead of `$GPGGA`
(baud/clock mismatch), `Permission denied` on the port (getty resetting perms),
or failed-login log entries containing `$GNGGA` (getty eating the GPS stream).

GPS parsing rules learned the hard way:

- `readline()` returns bytes; decode with `raw.decode("ascii", errors="ignore")`,
  skip lines that don't start with `$`, and catch `pynmea2.ParseError`.
- NMEA lat/lon are degrees + decimal minutes (`2819.24877,N` = 28 + 19.24877/60 =
  28.320813 N). West is negative. Use `msg.latitude` / `msg.longitude` from pynmea2,
  not the raw `msg.lat` / `msg.lon`.
- On GGA: `msg.gps_qual == 0` means no fix; `msg.num_sats` is satellites in the fix.
  `$GNZDA` is time/date only. A baud sweep (4800..115200) finds NMEA if unsure.

### Sensor sanity values

- MPU-6050 at rest: Z accel ~9.8 m/s^2, gyro ~0. Temperature exactly 36.53 C means
  raw register 0 (not yet converted). MPU temperature is diagnostic silicon/die
  temperature, not ambient air; self-heating can make it warmer than the room.
- BMP280: first sample after init may be an incomplete conversion (e.g. 724 hPa,
  3009 m). Wait ~1 s. Good bench values ~1011.7 hPa. At GPS lock capture a fresh
  mean of 10 pressure readings and the current valid GGA altitude. Thereafter
  absolute baro altitude = GPS pad altitude + pressure-relative AGL; before lock
  or without GGA altitude, field 12 is an uncalibrated 1013.25 hPa estimate.
  BMP280 temperature is chip/die temperature and may not equal ambient air.
- HMC5883L heading = `atan2(y, x)` assumes level board; use declination 0 on the
  bench, -0.113 rad in the field (the ElectronicWings example's -0.00669 is wrong
  for Cocoa).

### Camera

`sudo apt install python3-picamera2` (not inside the venv; apt ignores it).
`rpicam-hello --list-cameras` lists the ov5647; `rpicam-still -o file.jpg` tests the
path. libcamera INFO lines on startup are normal. Known-working pattern: `Picamera2()`,
`configure(create_still_configuration())`, `start()`, `sleep(1)`, `capture_file(str(path))`,
`stop()`. Photos go to timestamped folders like `/home/pi/Desktop/photos_YYYY-MM-DD_HH-MM-SS/`.

### Telemetry packet format (current 17-field contract)

Plain ASCII CSV, one LoRa packet per sample, built in `TX_Telemetry.py`, decoded
by `telemetry_decode.decode()` and its inlined copy in `RX_Temenetry.py`.
`NUM_SENSOR_FIELDS = 17` (`NUM_INT_FIELDS = 10`). Fields 0-9 are raw signed
16-bit ints; 10-13 are BMP280 floats; 14 is phase; 15 is the signed T clock;
16 is decimal `status_flags`; everything after the 17th comma is raw GGA.

| Idx | Field | RX conversion |
|---|---|---|
| 0-2 | acc_x, acc_y, acc_z | / 2048.0 -> g (+/-16 g; TX writes ACCEL_CONFIG 0x1C = 0x18) |
| 3-5 | gyro_x, gyro_y, gyro_z | / 16.4 -> deg/s (GYRO_CONFIG = 0x18, +/-2000 dps) |
| 6 | temp | / 340 + 36.53 -> C |
| 7-9 | mag_x, mag_y, mag_z | + offsets X 0.0, Y 167.0, Z 112.5 (from `compass calibration.txt`) |
| 10 | baro_temp_c | float or None |
| 11 | baro_press_hpa | float or None (absolute) |
| 12 | baro_alt_m | float or None (after lock: `pad_gps_alt_m + baro_agl_m`; before lock/fallback: uncalibrated 1013.25 hPa standard atmosphere) |
| 13 | baro_agl_m | float or None (altitude vs fresh mean pad pressure captured at GPS lock) |
| 14 | phase | `P`, `C`, `F`, or `R` |
| 15 | t_clock | signed seconds from T-0, one decimal; blank in PAD |
| 16 | status_flags | decimal bitmask: IMU 1, MAG 2, BARO 4, GPS available 8, GPS locked 16, camera available 32, camera recording 64, barometric altitude GPS-referenced 128 |
| 17.. | GPS | raw `$GNGGA`/`$GPGGA`; decoder splits exactly 17 fixed fields and rejoins the remainder |

Status semantics: IMU/MAG/BARO availability means initialized/known hardware.
Those bits remain set in RECOVERY although readings stop; LiveState retains and
labels the last readings `FROZEN (RECOVERY)`. GPS availability requires a recent
GGA; GPS lock may clear without changing flight phase. Coordinates remain last
known on RX. Camera reports standby, recording, stopped, or unavailable.
`BARO_GPS_REFERENCED` (bit 7/value 128) means this TX process captured both a
fresh pad-pressure mean and valid pad GPS altitude; RX uses this bit, not phase,
to label retained field 12 as `GPS REF` versus `UNCAL`. Radio health is packet
freshness plus RSSI/SNR, not a TX bit.

Example after GPS-referenced calibration: `0,0,2048,0,0,0,-3552,100,-67,0,24.81,1011.73,13.21,0.00,C,-12.3,255,$GNGGA,...`

Altitude formula on the TX (same as Pimoroni `BMP280.get_altitude()`):
`((qnh / p) ** (1 / 5.257) - 1) * (t_c + 273.15) / 0.0065`, once with qnh =
1013.25 and once with qnh = pad baseline (mean of 10 readings after discarding
the first conversion).

No sequence number, TX timestamp, or checksum. pyLoraRFM9x prepends a 4-byte
RadioHead header `[to, from, id, flags]`. Typical packet ~140-150 bytes; at
SF7/BW500/CR4/5 the estimated airtime is ~58-62 ms, and the TX loop targets 7 Hz
with a 0.5 s send timeout. BW500 reduces receiver sensitivity and expected range
relative to BW125; bench delivery is verified, but flight range is not. Heading =
atan2(mag_y, mag_x) + declination (-0.113), integer degrees 0..359; only meaningful
when level (pad orientation).

A 17-field RX rejects older packets (`TelemetryDecodeError`), so TX and RX must
be deployed together. Any format change must be made in BOTH decoder copies and
is an RX-redeploy trigger.

### Radio configuration in the deployable scripts

915 MHz, `ModemConfig.Bw500Cr45Sf128` (SF7, BW500, CR4/5), SPI0 CE1, DIO0 GPIO 5,
RST GPIO 25, library default sync word 0x12. Full telemetry targets 7 Hz; RECOVERY
remains GPS-only at 1 Hz. Node addresses: TX `my_address=2` sends to 1; RX `--node`
default 1 (RX also accepts broadcast 255). Both radio ends must match this exact
preset; old BW125 copies are incompatible. The library ignores the hardware
PAYLOAD_CRC_ERROR flag, so corrupt packets reach `on_recv`.
pyLoraRFM9x >= 1.0 uses `lgpio` + `spidev`, not `RPi.GPIO`; lgpio writes
`.lgd-nfy*` files into the process CWD, which is why `WorkingDirectory=` matters.

### TX-local recovery log and playback

At every TX process startup, create `/home/pi/Desktop/flight_<YYYY-MM-DD_HH-MM-SS>/`
and immediately open `telemetry_<stamp>.csv`; camera output, if enabled later at
GPS lock, uses the same folder and stamp. CSV columns are `tx_epoch, tx_iso,
tx_monotonic, send_ok, payload`; stdlib CSV quoting keeps the comma-filled payload
in one field. Every attempted PAD/COUNTDOWN/FLIGHT/RECOVERY packet is flushed;
`fsync` occurs every 10 rows and at shutdown. Logging fails soft and has its own
error count. No media enters telemetry.

On RX, `playback.py` accepts either SQLite (`--db`, `--session`, `--list`) or a TX
CSV (`--tx-log PATH`). `--gui` reuses `RX_Temenetry.LiveState` and
`run_gui_dashboard`; `--graphs` opens a dependency-free Tkinter Canvas selector
for AGL ft, total acceleration g, gyro magnitude deg/s, and GPS altitude ft,
decimated to at most 1000 points. `--speed` controls replay pacing. TX-log link
quality is n/a. Importing `RX_Temenetry` does not initialize radio because the
hardware import and construction remain inside `main()`.

### SQLite schema (`telemetry_db.py`, duplicated inline in `RX_Temenetry.py`)

Default file `/home/pi/Desktop/RxTelemetry.db`. Single table `telemetry` has
**40 columns**: the previous 39-column timeline schema plus `status_flags
INTEGER`. Older databases are migrated idempotently on open with `PRAGMA
table_info` and `ALTER TABLE ADD COLUMN`; old rows read as NULL.

### Conflicts found between System Scripts and the Grok_* authority (2026-10-08)

STATUS: ALL ROWS FIXED in the 2026-10-08 System Scripts update (workflow
`wf_e93a05d6a7ea64f7`, step `implement`). The table is kept as history of what
was wrong; "System Scripts had" describes the pre-update state.

| Topic | System Scripts had | Authoritative |
|---|---|---|
| GPS device | `/dev/ttyS0` | `/dev/serial0` (-> ttyAMA0) |
| Radio acks | `acks=True` (TX and RX) | `acks=False` |
| Frequency | library default | explicit `freq=915` |
| I2C library | `smbus` | `smbus2` (`import smbus2 as smbus`) |
| GPS sentence filter | `poll_gps()` keeps newest line of ANY type; decoder only parses GGA, so most packets showed "no fix" | filter for `GGA` like `Grok_GPSTest.py` |
| Service interpreter | `/usr/bin/python3` (cannot import venv packages) | `/home/pi/libs-venv/bin/python3` |
| Declination | -0.00669 rad (tutorial value, -0.38 deg) | -0.113 rad (-6.5 deg) for Cocoa, FL. USER CONFIRMED 2026-10-08: Cocoa is the launch site; use this value in the flight/RX code. 0 only for bench heading checks. |
| tx_power | 5 | `Grok_TX_test.py` says 5; bench transcript's final run used 14. USER DECIDED 2026-10-08: flight value 15 dBm |
| `RPi.GPIO` import | TX and RX import it only for `GPIO.cleanup()` | not needed (pyLoraRFM9x uses lgpio); may not exist on Python 3.13 |
| Sign conversion | `if value > 32768` | `>= 32768` (0x8000 must become -32768) |

Other review findings worth remembering (all addressed by 2026-10-11): `rocket-tx.service`
had an ordering cycle (`After=multi-user.target` + `WantedBy=multi-user.target`,
now `After=local-fs.target`); TX exited cleanly after `FLIGHT_DURATION_S` = 8 min
and `Restart=on-failure` would not relaunch it (now the process runs continuously
until stopped, and `Restart=on-failure` handles crashes); hardware
init was unguarded so a loose ribbon killed the whole script (now fail-soft, LoRa
excepted); an exception inside `on_recv` permanently killed the lgpio interrupt
thread on either side (now fully guarded on both); BMP280 was wired but NOT in the
flight code (now fields 10-13); first MPU6050 and BMP280 samples after init are
garbage (now discarded).

Full report: `.agents/tasks/codebase-review-2026-10-08.md` (sections 7-10 are the
conflicts, risks, open questions, and ordered recommendations).

### Decisions made by the user (2026-10-08)

- Magnetic declination = Cocoa, FL value, -6.5 deg (-0.113 rad), in
  `telemetry_decode.py` and the inlined copy in `RX_Temenetry.py` (and anywhere
  else the constant appears). The tutorial's -0.00669 rad is wrong for this site.
- Flight `tx_power` = 15 dBm. This is a deliberate user override of the
  `Grok_TX_test.py` value (5); the Grok file stays untouched as a bench reference.
- Accelerometer range = +/-16 g: write `ACCEL_CONFIG (0x1C) = 0x18` on the TX and
  use 2048.0 LSB/g in BOTH RX decoder copies. (Gyro stays +/-2000 dps, 16.4 LSB/dps.)
- BMP280 is folded into the flight telemetry: temperature, pressure and
  barometric altitude go into the packet, both decoders, the SQLite schema and
  playback. Zero altitude on the pad; discard the first conversion after init.
- All bench testing (the Grok_* scripts and the transcript) was performed on the
  Pi Zero 2 W. The transcript's "Pi 3" wording is misleading; ignore it. The Zero
  2 W is COMPLETELY set up (venv, apt packages, UART `disable-bt`, camera).
- The ground-station Pi 3 has NOT been set up at all. The README RX section must
  be a from-scratch guide: venv (`~/libs-venv`, same layout as the TX), `swig
  python3-dev liblgpio-dev`, `pip install pyLoraRFM9x pynmea2`, `python3-tk`,
  SPI enabled, launchers pointing at the venv interpreter.
- Both Pis are currently named `raspberrypi.local`. Two hosts with the same mDNS
  name on one network collide, so when both are powered on, `.local` lookups are
  ambiguous. Recommended (not yet done): rename the Pi 3 during its setup
  (`sudo raspi-config` -> System -> Hostname, e.g. `rocket-rx`). Until then, use
  IP addresses or power on one at a time when copying files.

### Decisions made in the System Scripts update (2026-10-08, implementer)

Made by the planner/implementer inside the user's decisions above; recorded so
they are not re-litigated. Full rationale: `.agents/tasks/system-scripts-update-plan.md`
(section A2) and `.agents/tasks/system-scripts-update-summary.md`.

- D5 tx_power: TX constant `LORA_TX_POWER_DBM = 15`; RX keeps `tx_power=5` (it
  never transmits).
- D8 Camera: keep H.264 video (1280x720 @ 30, 4 Mbps, 180-degree Transform) via
  picamera2, fail-soft; output `/home/pi/Desktop/flight_<YYYY-MM-DD_HH-MM-SS>/flight_<stamp>.h264`
  (Grok_Cam_Test.py folder convention). Video path is NOT bench-verified.
- D11 BMP280: Pimoroni `bmp280` package, `setup(mode="normal", temperature_oversampling=16,
  pressure_oversampling=16, temperature_standby=62.5)`; sleep 1 s; discard one
  `get_pressure()`; pad baseline = mean of 10 readings 0.1 s apart. 62.5 ms
  standby is a tuning change vs the bench default 500 ms (confirm on the bench).
- D13 Fail-soft: GPS, I2C bus, MPU-6050, HMC5883L, BMP280 and camera init all
  print `<sensor> unavailable: ...` and continue (zeros / empty fields). LoRa
  init is the one hard failure (`sys.exit(1)`), done before the camera starts.
  Loop body guarded per sensor group and around `lora.send`.
- D15 Flight window: `FLIGHT_DURATION_S` from env `TX_FLIGHT_DURATION_S`
  (default 480); the unit sets 480.
- D16 Service: `Restart=on-failure`, `After=local-fs.target` (no ordering
  cycle), `Environment=PYTHONUNBUFFERED=1`, SIGTERM handler raises
  `SystemExit(0)` so `finally:` finalises the video.
- D18 RX launchers: fixed `RX_PYTHON="/home/pi/libs-venv/bin/python3"`, clear
  error + exit 1 if missing (no silent `python3` fallback).
- D20 GPS: both decoders expose `fix_quality` and `num_sats`; `gps_qual == 0`
  forces lat/lon/alt to None; displays show `sats N`. Not stored in the DB.
- D24 DB: 4 `baro_*` REAL columns after `gps_raw`; idempotent PRAGMA-based
  migration; no schema-version table.

### Flight timeline requirement (user, 2026-10-08) - IMPLEMENTED

The TX runs a phase state machine driven by GPS lock, with a T clock that is
transmitted in every packet and shown on every RX display (terminal dashboard,
Tkinter GUI, scroll log, playback). The script never exits on its own; it runs
until power is removed.

| Phase | Entered when | TX behaviour | T clock |
|---|---|---|---|
| PAD | boot | Full sensor telemetry at normal rate so the ground crew can check the pad. Camera NOT recording. Status "waiting for GPS lock". | blank |
| COUNTDOWN | GPS lock acquired | Camera starts recording. BMP280 pad baseline captured here (rocket is definitely on the pad). Full telemetry. | starts at T-30 and counts down |
| FLIGHT | T clock reaches T-0 | Full telemetry continues. Local H.264 video finalizes at T+3 minutes (`TX_CAMERA_STOP_TPLUS_S=180`); telemetry continues. | T+ counting up |
| RECOVERY | T+ reaches the avionics window (`TX_FLIGHT_DURATION_S`, default 480 s = 8 min, measured from T-0) | IMU / magnetometer / barometer no longer read or sent. GPS-only packets at 1 Hz with phase and T clock, forever, until powered off. | T+ keeps counting up until power off |

Rules and decisions:

- GPS lock = `gps_qual >= 1` on 3 consecutive GGA sentences (about 3 s), to
  ignore a single flicker. Once locked, losing the fix later does NOT regress the
  phase or stop the clock; the clock runs on `time.monotonic()` from the lock
  instant.
- Phase transitions are only forward (PAD -> COUNTDOWN -> FLIGHT -> RECOVERY) and
  each is logged to stdout (journal) with the UTC time from the GGA sentence.
- Packet has `phase`, `t_clock`, then `status_flags` after the barometer fields
  and before GPS. `NUM_SENSOR_FIELDS = 17`. RX redeploy required. DB has
  `phase TEXT`, `t_clock REAL`, and `status_flags INTEGER` through the existing
  migration guard.
- Camera video is local to the TX SD card only; no photo/video bytes are ever
  placed in telemetry. Recording begins at GPS lock (T-30) and stops/finalizes at
  T+3 minutes. RX has no camera row or camera visualization; camera status bits
  remain in the packet contract for compatibility but are not displayed.
- RX display format: `T-00:30` counting down, `T+00:00` at zero, then `T+MM:SS`;
  switch to `T+H:MM:SS` once past an hour. PAD shows `T --:--  (waiting for GPS
  lock)`. The GUI gets a large, prominent T clock label plus the phase name.
  When packets stop arriving the last T value stays on screen with the existing
  stale indicator.
- `rocket-tx.service`: `Restart=on-failure` (NOT `always`). The script no longer
  exits cleanly, so a restart only happens after a crash or power glitch.
  `TX_FLIGHT_DURATION_S` keeps its meaning as the avionics window from T-0.
- Operational assumption, stated by the user's design: launch is expected about
  30 s after GPS lock. If the rocket sits on the pad long after lock, the 8-minute
  window may expire before launch; the ground crew sees this on the T clock.
  If GPS never locks, the system stays in PAD: full telemetry but no video and no
  T clock. (Possible future addition: a manual arm/override; not requested.)

### Open decisions for the user

As of the 2026-10-08 System Scripts update:

- Rename the Pi 3 hostname (`rocket-rx`) during its from-scratch setup, then
  verify the RX RFM9x wiring against the TX pinout (unverified on the Pi 3).
- Q10: BMP280 standby 62.5 ms vs bench default 500 ms; confirm `baro=` in
  `journalctl` updates several times per second.
- Q11: RESOLVED by the flight timeline requirement above (continuous run until
  power off, `Restart=on-failure`, 8-minute avionics window starts at T-0).
- Q7: magnetometer X-axis offset was never characterised (heading uses X).
- The H.264 video path has not been bench-verified (stills were).

## External references

- `avionics-conversation.pdf` (workspace root): export of the Grok bench session
  that produced the `Grok_*` scripts. Plain-text extraction at
  `.agents/tasks/grok-conversation.txt`. Original share link (client-rendered, not
  fetchable by the agent): https://grok.com/share/bGVnYWN5_6e204875-245e-43e4-ab77-34aedf597a66
- `3D Prints/RM-MPU-6000A.pdf`: MPU-6000/6050 register map.

## Sensor mounting orientation (user, 2026-10-08)

- MPU-6050 +Z axis points toward the rocket nose. RX `NAV` target uses +Z as
  centered/upright after ZERO ATTITUDE; linear 30/60/90-degree rings map X/Y
  lateral tilt, with the movable dot clamped to the outer 90-degree ring.
- HMC5883L +Z axis points toward the rocket nose. Compass uses the X/Y plane.
- Rocket "front" / zero-heading board edge is not decided. Keep a configurable
  `COMPASS_DISPLAY_OFFSET_DEG = 0.0` in the RX GUI; calibrate after final mounting.
- Nav ball and compass are estimated visualizations, not flight-control data.
  Dynamic acceleration corrupts accelerometer tilt; gyro integration drifts;
  magnetometer X offset is uncharacterized and magnetic heading needs calibration.

## Working conventions

- The development machine is the user's Mac (Python 3.9.6 system interpreter).
  The Raspberry Pi is a separate device; this workspace is edited on the Mac
  and copied to the Pi. Pi hardware libraries (`RPi.GPIO`, `picamera2`, `smbus2`,
  `spidev`, etc.) will not function on the Mac, so don't expect to import them
  here. For one-off utilities prefer macOS built-ins (PDFKit via `swift`, the
  Python standard library) and mention it before installing anything.
- HARD RULE (user, 2026-10-08): NEVER execute any project script on the Mac. Not
  directly, not imported, not with mocked/stubbed hardware modules, not in /tmp.
  No virtual bench tests, no harnesses, no fake `serial`/`smbus2`/`pyLoraRFM9x`
  modules. The user bench-tests on the real hardware and debugs from there. The
  only Mac-side checks allowed are pure syntax checks that do not execute code:
  `python3 -m py_compile <file>` (then delete the `__pycache__` it creates) and
  `bash -n <file>`. Write nothing outside the workspace.
- Process weight (user, 2026-10-08): keep it light. Write the code, syntax-check
  it, hand it over for bench testing. No plan/implement/review loops unless the
  user asks for one. Small edits are made directly by the orchestrator.
- Keep the `Grok_` files untouched as reference implementations unless the user
  explicitly asks to change them.
- Prefer editing the `System Scripts/` versions for real changes; root-level
  scripts are prototypes unless the review says otherwise.
- Use inclusive terminology in code and docs (allowlist/denylist, primary/replica).

## History and decision log

Newest first. One entry per meaningful session or decision.

- 2026-10-27 18:07: Increased only the RX GUI's visible
  `OBSIDIAN-1 TELEMETRY` title from DejaVu Sans Mono 17 bold to exactly 20 bold.
  The existing font-probed bounded Canvas expanded automatically; wording,
  exact Krypton Purple `#3A1C4E`, and all eight one-pixel white outline offsets
  remain unchanged. Mac syntax-only and RX Python 3.7.3 checks passed; local and
  remote SHA-256 matched
  (`27fc46b4574336a59b107800052e249786c5e6da5caddd70f7c12766e313ac46`), and
  both caches were removed. RX backup:
  `System Scripts/rx-title-font-20-backup-20261027-180618/`. Final RX GUI is the
  sole matching process, PID 4833, X11-viewable at 798x436; SQLite advanced
  during both packet-flow checks. The 800x480 capture confirms the 20 pt title
  is unclipped with clear space before LIVE and no title/LIVE/STAGE overlap.
  The existing low-voltage notification still obscures part of the far-right T
  clock; it is not widget overlap. Evidence and details:
  `.agents/tasks/rx-title-font-20-summary.md` and
  `.agents/tasks/rx-title-font-20-verification.png`. TX was not accessed; no
  packet, radio, database schema, telemetry, playback, or persistent service
  configuration changed.

- 2026-10-27 18:04: Increased only the RX GUI's visible
  `OBSIDIAN-1 TELEMETRY` title from DejaVu Sans Mono 15 bold to 17 bold. The
  existing font-probed bounded Canvas expanded automatically; wording,
  top-left placement, exact Krypton Purple `#3A1C4E`, and all eight one-pixel
  white outline offsets remain unchanged. Mac syntax-only and RX Python 3.7.3
  checks passed, local/remote SHA-256 matched
  (`9920f30cd0b6c86d7ef848f47def7c973ffec7cd65d8f4a88c794893410d1494`), and
  both caches were removed. RX backup:
  `System Scripts/rx-title-font-backup-20261027-180134/`. Final RX GUI is the
  sole matching process, PID 4448, X11-viewable at 798x436; SQLite advanced
  from 18493 to 18497 rows over four seconds. The 800x480 capture confirms the
  17 pt title is unclipped with clear space before LIVE and no header-widget
  overlap. The existing low-voltage notification still obscures part of the
  far-right T clock; it is not widget overlap. Evidence and details:
  `.agents/tasks/rx-title-font-summary.md` and
  `.agents/tasks/rx-title-font-verification.png`. TX was not accessed; no
  packet, radio, database schema, telemetry, playback, or persistent service
  configuration changed.

- 2026-10-27 18:00: Added and deployed the RX-only title contrast update.
  The visible `OBSIDIAN-1 TELEMETRY` header is now a bounded, borderless Canvas
  using the existing `DejaVu Sans Mono` 15 bold font, eight white copies at
  one-pixel surrounding offsets, and the unchanged Krypton Purple `#3A1C4E`
  foreground copy. LIVE/STAGE/T-clock columns and all telemetry behavior are
  unchanged. Mac syntax-only and RX Python 3.7.3 checks passed; local and remote
  SHA-256 matched (`c69d9e0979acd2feba4c4b671973afdd129b82de249a2c7b83f5255aea5fdf56`),
  and both caches were removed. RX backup:
  `System Scripts/rx-title-outline-backup-20261027-175838/`. Final RX GUI is the
  sole matching process, PID 4046, X11-viewable at 798x436; SQLite advanced from
  18325 to 18328 rows over three seconds. The captured desktop confirms a thin
  white outline, purple letter centers, lime LIVE, RECOVERY stage, and no header
  overlap. The existing low-voltage notification obscured part of the far-right
  T-clock; it was not widget overlap. Evidence and details:
  `.agents/tasks/rx-title-outline-summary.md` and
  `.agents/tasks/rx-title-outline-verification.png`. TX was not accessed.

- 2026-10-27 17:56: Implemented and deployed the focused RX-only Obsidian-1 GUI
  header/list update. `RX_Temenetry.py` now uses exact Krypton Purple `#3A1C4E`
  for the visible `OBSIDIAN-1 TELEMETRY` title, a separate LIVE label that is
  lime only for fresh live-radio packets and gray before packets/stale/in
  playback, `STAGE: --` before reception and an adjacent decoded stage
  afterward, and an `Obsidian-1 Telemetry LIVE/PLAYBACK` window title. The left
  list is ACCELERATION, GYRO, ALTITUDE, GPS, MAG, RADIO, MPU DIE TEMP, BMP CHIP
  TEMP, placing both diagnostic temperatures at the bottom. Packet/radio/DB/TX
  behavior and the playback renderer signature are unchanged. Mac syntax-only
  and RX Python 3.7.3 checks passed; local and deployed checksums matched and
  both caches were removed. RX backup:
  `System Scripts/rx-header-layout-backup-20261027-175521/`. Final RX GUI is the
  sole matching process, PID 3642, X11-viewable at 798x436 with title
  `Obsidian-1 Telemetry LIVE - flight_20261027_175536`; fresh RECOVERY packets
  arrived at 1 Hz and the captured desktop showed the header LIVE label lime,
  stage RECOVERY, and the requested compact layout. The existing desktop low-
  voltage notification overlaid part of the upper-right T-clock during capture;
  it was not widget overlap. Evidence and details:
  `.agents/tasks/rx-header-layout-summary.md` and
  `.agents/tasks/rx-header-layout-verification.png`. TX was not accessed.

- 2026-10-27 17:40: Implemented, deployed, and real-hardware verified the user's
  7 Hz high-bandwidth telemetry mode and two-decimal critical readouts. Both
  installed libraries were introspected before editing: TX pyLoraRFM9x 1.0.2 and
  RX 0.9.2 both expose `ModemConfig.Bw500Cr45Sf128`. TX and RX now use that exact
  SF7/BW500/CR4/5 preset at 915 MHz; TX remains node 2 -> RX node 1, 15 dBm,
  `acks=False`, with the 0.5 s send timeout. Full telemetry targets 7 Hz and
  RECOVERY remains 1 Hz. Estimated 140-150-byte airtime is ~58-62 ms. BW500's
  reduced sensitivity/expected-range tradeoff is accepted, but flight range is
  explicitly unverified. No packet or DB schema changed. Live GUI, terminal,
  scroll, shared-GUI playback, and textual playback now show two decimals for
  current/max-flight g, gyro axes, barometric absolute/AGL altitude, inHg,
  MPU/BMP die temperatures, NAV tilt values, and heading; GPS coordinates retain
  six decimals. Mac syntax-only checks and both Pi interpreter checks passed.
  Both Pis have timestamped per-file backups at `System Scripts/7hz-bw500-backup-20261027-173422/`.
  Acceptance session `flight_20261027_173451`, synchronized epoch interval
  1793137131..1793137176 (45 s): TX CSV 315 attempts = 7.000 Hz, all send_ok;
  RX SQLite 305 rows = 6.778 Hz, 96.83% delivery. RSSI averaged -21.04 dBm
  (range -22..-20), SNR averaged 5.66 dB (5.0..6.5); all rows were FLIGHT with
  valid flags 239, zero invalid status rows, zero decode/on-receive errors, and
  TX reported zero send/I2C/sensor/local-log errors. GPS lock, pad baseline,
  camera start, COUNTDOWN, and FLIGHT transitions also occurred normally. Final:
  TX service enabled/active PID 1731, RX GUI active PID 2648 on `DISPLAY=:0.0`
  with one Python process and a visible 798x436 window. TX throttling `0x0`; RX
  remains `0x50005`, the known user-accepted undervoltage/throttling condition.
  Report: `.agents/tasks/7hz-bw500-update-summary.md`.

- 2026-10-27 17:15: Deployed and real-hardware verified the focused RX G/altitude/
  temperature presentation update to both Pis. `BARO_GPS_REFERENCED` is status
  bit 7/value 128 without changing the 17-field packet or 40-column DB; TX clears
  both altitude anchors at startup and again before the three-fix capture, then
  sets bit 128 only when this run has both a fresh pressure mean and valid pad GPS
  altitude. RX shows current total g and FLIGHT-only `MAX FLIGHT G`, separate
  `ALTITUDE`, `MPU DIE TEMP`, and `BMP CHIP TEMP` rows, and uses bit 128 for
  `GPS REF`/`UNCAL`. Real prior flight playback proved max G is `--` in PAD and
  COUNTDOWN, starts at the first FLIGHT packet (1.05 g), reached 1.11 g in that
  test, and another FLIGHT->RECOVERY recording retained current 1.05 g and max
  1.39 g through T+09:15. During deployment RX unexpectedly rebooted before any
  remote mutation; after reconnect it was positively identified as Pi 3 B+ with
  fresh uptime, had relaunched the same desktop GUI, and all remote checksums were
  still pre-change. Backups: TX `rx-g-alt-temp-backup-20261027-171257`, RX
  `rx-g-alt-temp-backup-20261027-171258`; follow-up playback backups on both:
  `rx-g-alt-temp-playback-fix-backup-20261027-171532`. TX stopped cleanly with
  2586 packets, zero send/sensor/log failures, then restarted enabled/active as
  PID 1841; gyro offsets were X=-22,Y=9,Z=15 and the new CSV opened under
  `flight_2026-10-27_17-13-42`. RX GUI restarted manually as PID 1087 on
  `DISPLAY=:0.0`, visible at 798x436, with exactly one GUI process; SQLite
  reached 605 new rows by 17:17:11.
  Live PAD data: flags 47 (bit 128 correctly clear), total 1.058 g, MPU die 87.3 F,
  BMP die 87.6 F, 1016.31 hPa, uncalibrated -87.9 ft, RSSI -38 dBm, SNR 9.75 dB.
  GPS remained no-fix, so a live 128 transition was not observed. TX throttling
  was 0x0; RX remained 0x50005, which the user elected not to block on.

- 2026-10-11 14:54: Enabled TX-side-only graphing. Deployed current
  `playback.py`, `RX_Temenetry.py` (renderer only; no radio init on import),
  `telemetry_decode.py`, and `telemetry_db.py` to Zero 2 W. Added executable
  `graph_latest_tx.sh` and desktop icon `/home/pi/Desktop/Rocket-TX-Graphs.desktop`;
  it automatically finds newest `/home/pi/Desktop/flight_*/telemetry_*.csv` and
  opens `playback.py --tx-log ... --graphs`. Python 3.13 syntax passed. Opened
  latest `telemetry_2026-10-11_14-38-26.csv`; graph viewer PID 1903 healthy under
  Wayland. TX service remains inactive/enabled. Backup:
  `System Scripts/tx-graph-backup-20261011-145400`.

- 2026-10-11: Prepared local-only avionics enhancement for real-Pi deployment.
  TX now creates a startup run folder and durable five-column telemetry CSV,
  shares it with camera output, and anchors post-lock barometric altitude to the
  captured GGA pad altitude plus fresh pressure-relative AGL. RX uses a 30/60/90
  degree `TILT TARGET`, clearly labels GPS-referenced/uncalibrated altitude and
  BMP chip temperature. Playback now accepts RX SQLite or TX CSV, reuses the
  live dashboard with completion status, and provides four lightweight Canvas
  graphs decimated to 1000 points. Packet remains 17 fixed fields + GGA; DB
  remains 40 columns. Not deployed or hardware-tested in this step.

- 2026-10-11 13:xx: User authorized TX boot deployment. Installed
  `/etc/systemd/system/rocket-tx.service`, daemon-reloaded and enabled it at
  `multi-user.target`; `systemd-analyze verify` passed. Confirmed enabled but
  inactive and no TX process started. It will run on the NEXT TX boot after a
  10 s delay, using `/home/pi/libs-venv/bin/python3`; T+3 local camera stop,
  T+8 GPS-only recovery, `Restart=on-failure`. Updated desktop command note to
  reflect installed state. Maintenance boot: run
  `sudo systemctl disable --now rocket-tx.service` before rebooting.

- 2026-10-10 00:49: Camera-free normal T-clock test passed. Added persistent
  `TX_DISABLE_CAMERA=1` environment switch and ran TX manually with it (no camera
  initialized, no video file created). GPS locked with 7 sats, PAD->COUNTDOWN at
  T-30 and COUNTDOWN->FLIGHT at T+0; test continued to T+38.9 then stopped cleanly.
  TX sent 210 packets at stable 3.0 Hz with 0 send failures and 0 sensor errors;
  RX stored 205 (P=6,C=88,F=111), RSSI -34.5 dBm, SNR 9.6 dB. All 205 stored rows
  had GPS fixes. Average post-cal gyro raw X=-4.6,Y=-6.6,Z=-4.0 (~-0.28,-0.40,
  -0.24 deg/s); average accel raw -75.5,-106.4,2098.7 (~1.027 g total), normal
  for stationary upright payload. TX stopped; RX GUI remains PID 1098. RX power
  warning intentionally ignored for this user-requested test.

- 2026-10-10 00:43: Second full-hardware test after radio/gyro fixes. TX gyro
  startup offsets X=-23,Y=-1,Z=12 raw; post-calibration RX averages approximately
  -0.01,+0.26,+0.03 deg/s. Stable 3.0 Hz: TX sent 152 packets with ZERO failures;
  RX stored 151 (99.3%), RSSI -33.3 dBm, SNR 9.6 dB. PAD->COUNTDOWN->FLIGHT
  worked; 23 GPS-fix rows at 28.320747,-80.741746, altitude 32.4 m; GPS later lost
  current fix but clock continued. Zero sensor/I2C errors, AGL ~0-0.6 m. Camera
  finalized 13.3 MB H.264 at `flight_2026-10-10_00-43-54/`. TX stopped cleanly;
  RX remains PID 983. TX power clean (`0x0`), but RX returned to ACTIVE
  UNDERVOLTAGE/THROTTLING under operating load (`0x50005`) despite reading `0x0`
  immediately after reboot. RX supply/cable fix is not sufficient under load;
  do not run further tests until corrected.

- 2026-10-09 23:04: Applied first-test fixes. TX full telemetry now 3 Hz and
  `lora.wait_packet_sent_timeout=0.5` (packet airtime ~235 ms); added 100-sample,
  ~1 s stationary gyro bias calibration at startup, subtracting raw X/Y/Z bias.
  RX GUI splits GYRO and MPU TEMP into separate rows; acceleration explicitly
  says 1g at rest and shows total g; added ZERO ATTITUDE button that centers the
  display without altering stored data. Deployed to both Pis; syntax passed on
  TX Python 3.13 and RX Python 3.7. Backups:
  `System Scripts/calibration-backup-20261009-230430`. RX restarted PID 1424;
  TX remains stopped. IMPORTANT: RX Pi reports `vcgencmd get_throttled=0x50005`
  (current and historical undervoltage/throttling). Replace its 5 V supply/cable
  before rerunning TX or trusting follow-up radio/display measurements.

- 2026-10-09 22:48: First full-hardware end-to-end test completed. RX GUI started
  PID 1135/session `flight_20261009_224810`; TX manually started PID 1717 and
  cleanly stopped at T+13.3 (service remained inactive/not installed). Results:
  camera configured ov5647 and recorded/finalized 1280x720 H.264, 17 MB at
  `/home/pi/Desktop/flight_2026-10-09_22-48-50/`; GPS locked (5 sats), triggered
  PAD->COUNTDOWN at T-30 and COUNTDOWN->FLIGHT at T+0, later lost current fix but
  clock/phase correctly continued; 25 RX rows had coordinates 28.320894,
  -80.741566, altitude 29.7 m; MPU/HMC/BMP values valid, zero I2C errors, pad AGL
  ~0.3-0.5 m. RX stored 180 packets (P=4,C=111,F=65), average RSSI -34.3 dBm,
  SNR 9.8 dB. Radio issue found: TX library timeout is fixed 0.2 s while current
  packet airtime is ~0.235 s, so TX falsely counted 183 failures and only 15
  successes even though RX received 180. Recommended next change: increase
  `lora.wait_packet_sent_timeout` and lower telemetry send rate (or deliberately
  change both radios to a faster modem config after range testing). TX currently
  stopped; RX remains running. T+3 camera auto-stop and T+8 recovery were not
  exercised at real duration.

- 2026-10-09: Hardware fully integrated and both Pis powered. Installed a manual
  clickable TX launcher on the Zero 2 W: `/home/pi/Desktop/Rocket-TX.desktop`
  calls executable `System Scripts/start_tx.sh`, which uses the TX venv and keeps
  a terminal open for logs/errors. Added both source files locally and updated
  README. Verified `rocket-tx.service` inactive/not installed and no TX process
  running; boot autostart intentionally deferred. RX did not auto-restart after
  its power cycle and is currently stopped. Next: launch RX first, then click TX
  and perform real end-to-end component/radio/timeline debugging.

- 2026-10-08 end state / RESUME HERE: User approved the RX GUI and current
  behavior. Local files and both Pi `System Scripts` folders are synchronized.
  RX GUI is currently running on Pi 3 as PID 2156; TX service remains stopped
  because external hardware is disconnected. Next session: after MPU-6050,
  BMP280, HMC5883L, GPS, ov5647 camera, both RFM9x radios and antennas are fully
  connected, run the real-hardware checklist in `System Scripts/README.md` and
  debug observed readings/transitions only—no Mac virtual bench harness.

- 2026-10-08 18:10: User removed camera from RX presentation and set local TX
  video stop to T+3 min. TX now uses `TX_CAMERA_STOP_TPLUS_S=180`; camera starts
  at GPS lock/T-30, records locally only, finalizes at T+180 while telemetry runs
  to T+480 and GPS continues forever. Verified packet contains no media—only
  telemetry text/status/GGA. RX attitude and compass canvases shrunk to 190x165
  and stacked higher; CAMERA row removed from GUI/terminal/playback. Synced to
  both Pis; TX service not started; RX GUI restarted PID 2156. Backups on both:
  `System Scripts/update-backup-20261008-181026`.

- 2026-10-08 18:04: Added bottom-left GPS status lamp to RX GUI with satellite
  count: green LOCKED, yellow SEARCHING, red UNAVAILABLE, gray WAITING. Deployed,
  Python 3.7 syntax passed, GUI restarted PID 2048. Backup:
  `RX_Temenetry.py.backup-20261008-gps-light`.

- 2026-10-08 18:02: RX GUI layout changed at user request: seven component
  readouts are in a full-height left panel; attitude ball above compass in a
  stacked right instrument column. Canvases reduced to 205x180 to fit 800x480.
  Deployed to Pi 3, Python 3.7 syntax passed, GUI restarted as PID 1975 and X11
  confirmed 798x436 window. Backup: `RX_Temenetry.py.backup-20261008-right-instruments`.

- 2026-10-08: RX display enhancement prepared locally (not deployed): imperial
  presentation units in GUI/terminal/scroll/formatter/playback, an 800x450
  `ATTITUDE EST.` canvas using +Z upright and a complementary accel/gyro estimate,
  and a compass canvas with `COMPASS_DISPLAY_OFFSET_DEG = 0.0`. Both retain last
  values and indicate RECOVERY freeze/link staleness; attitude warns outside
  0.75-1.25 g and compass remains marked `UNCALIBRATED X`. Wire packet and SQLite
  units are unchanged. GGA has no speed, so MPH is intentionally unavailable.
  RX redeploy required for these display-only changes; parent workflow will deploy.

- 2026-10-08 17:31: RX real-hardware smoke test started on Pi 3. Process PID 1453
  runs `/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui`; RFM9x init succeeded
  far enough for the receiver to remain healthy, SQLite DB opened at
  `/home/pi/Desktop/RxTelemetry.db` (16 KB), and X11 confirms visible Tk window
  `Rocket Telemetry - flight_20261008_173150` (630x340). No packets expected because
  TX is off/disconnected. GUI intentionally left running for user inspection.

- 2026-10-08 17:29: RX Pi 3 setup completed over SSH. Created
  `/home/pi/libs-venv` (Python 3.7.3, system packages visible), verified tkinter,
  pynmea2 1.19.0, pyLoraRFM9x 0.9.2, RPi.GPIO, spidev and serial imports. Updated
  `RX_Temenetry.py` to support both legacy pyLoraRFM9x 0.9.x and modern 1.0+
  constructors; syntax passed locally and in the RX venv. Installed executable
  `Rocket-RX.desktop` and `Rocket-RX-GUI.desktop` launchers. Receiver was NOT
  started. Per-file remote backup:
  `System Scripts/RX_Temenetry.py.backup-20261008-172907`.

- 2026-10-08: Completed and deployed the avionics deliverable: 17-field packet
  with explicit seven-bit component status, 40-column SQLite schema/migration,
  last-known RX state retention, compact component GUI with GPS lock/coordinates
  and link age, timeline documentation, and Pi-specific setup guidance. Mac
  syntax checks passed; deployed to `/home/pi/Desktop/Avionics for Rocket/System
  Scripts` on TX `192.168.1.172` and RX `192.168.1.101`; TX Python 3.13 venv and
  RX `/usr/bin/python3` 3.7 syntax gates passed. Remote backups:
  `System Scripts.backup-20261008-172221` (TX) and
  `System Scripts.backup-20261008-172216` (RX). Apps/services were not started;
  hardware/package behavior remains for user bench testing. Full report:
  `.agents/tasks/final-avionics-deploy-summary.md`.

- 2026-10-08 (end of day): PAUSED. RESUME HERE. State of `System Scripts/`:
  TX_Telemetry.py has all six components + the phase state machine + T clock
  (16-field packet); RX_Temenetry.py / telemetry_decode.py / telemetry_db.py /
  playback.py match the 16-field contract and show phase + T clock; service is
  `Restart=on-failure`. NOT yet done: (a) GUI needs an explicit GPS LOCK indicator
  with coordinates and a per-component last-known-state panel (consider a sensor
  status bitmask field in the packet so camera/IMU/mag/baro/GPS health is
  explicit); (b) README not updated for the timeline/16-field packet; (c) no
  review pass (user chose to bench test instead). Nothing deployed to either Pi.
  Next session: finish (a) and (b) with one coder step (syntax check only), then
  deploy to the Zero 2 W over SSH and bench test on real hardware. User offered
  SSH access to the Pis; see "Pi access" under Working conventions.
- 2026-10-08 (afternoon): User reset the approach. Requirements restated: ONE
  `TX_Telemetry.py` folding in accelerometer+gyro (MPU-6050), altimeter (BMP280),
  camera, GPS, magnetometer (HMC5883L) and radio; RX = receive, record, and a
  Tkinter GUI showing the last known state of every component, a T clock, and a
  GPS lock indicator with coordinates. No virtual bench testing; the user tests on
  real hardware. Workflow `flight-timeline-tclock-2` (wf_88d23dbe931a26bf) was
  aborted after its two code steps completed (FEAT-001 packet/RX, FEAT-002 TX
  state machine); its docs step and review loop were dropped. Harness files in
  `/tmp/avionics_harness` deleted. Mocked-execution-on-Mac is now a hard rule
  violation (see Working conventions).

- 2026-10-08: User specified the flight timeline (GPS-lock-driven phases PAD ->
  COUNTDOWN (T-30) -> FLIGHT (T+) -> RECOVERY (GPS-only until power off), T clock
  in the packet and on every RX display including the GUI, `Restart=on-failure`
  instead of `always`). Recorded under "Flight timeline requirement"; to be
  implemented in a follow-up workflow after the current update's review completes.
- 2026-10-08: System Scripts updated (workflow `wf_e93a05d6a7ea64f7`, step
  `implement`; all 11 files edited in place, no renames). TX_Telemetry.py now
  matches the Grok_* authority (serial0, ascii decode, GGA filter, smbus2,
  keyword LoRa init, acks=False, freq=915, tx_power 15, +/-16 g, BMP280 fields,
  fail-soft init, guarded loop, SIGTERM handler); rocket-tx.service uses the
  venv interpreter, `Restart=always`, `After=local-fs.target`. Packet is now
  14 fields + GGA; DB has 37 columns with in-place migration; RX launchers use
  `/home/pi/libs-venv/bin/python3`. **RX REDEPLOY REQUIRED: YES** (packet
  format, declination, schema migration, launchers); the Pi 3 setup is still
  pending (README has the from-scratch guide). Verified on the Mac: py_compile
  on all 5 .py, bash -n on all 3 .sh, plus mocked-hardware harnesses for TX,
  decoder, DB migration and RX. Summary:
  `.agents/tasks/system-scripts-update-summary.md`.
- 2026-10-08: User decided tx_power 15 dBm, +/-16 g accel, BMP280 folded into the
  packet, declination -0.113 rad; confirmed all bench work was on the Zero 2 W
  (fully set up) and the Pi 3 is untouched; both Pis are `raspberrypi.local`.
  Decisions forwarded to the running planner; scope now includes a packet-format
  and DB-schema change, so RX redeploy is required.
- 2026-10-08: Codebase review completed (`.agents/tasks/codebase-review-2026-10-08.md`).
  Packet format, radio config, DB schema, conflicts table and open decisions
  recorded above. Launched workflow `update-system-scripts-tx-zero2w`
  (wf_e93a05d6a7ea64f7) to update every file in `System Scripts/` to the Grok_*
  authority and the Zero 2 W / venv environment; it backs up the folder to
  `.agents/backup/System Scripts-2026-10-08/` first and must report
  "RX REDEPLOY REQUIRED: YES/NO". User confirmed: TX = Pi Zero 2 W, RX = Pi 3,
  all re-bench-tested hardware is TX-side.
- 2026-10-08: User supplied `avionics-conversation.pdf` (Grok bench session export).
  Extracted with macOS PDFKit to `.agents/tasks/grok-conversation.txt` and distilled
  into the Target platform and Hardware sections above: Pi OS Trixie / Python 3.13,
  `/home/pi/libs-venv`, UART disable-bt setup, I2C addresses 0x68/0x1e/0x76, RFM9x
  SPI0 CE1 pinout and init, picamera2 camera. User confirmed dev work happens on the
  Mac, not the Pi.
- 2026-10-08: User clarified the scripts were written for an original Pi Zero and
  will now run on a Pi Zero 2 W. Added platform migration checklist and the Grok
  hardware bring-up conversation link.
- 2026-10-08: Project onboarded. User set the `Grok_*` source-of-truth rule.
  Launched a read-only codebase review (workflow `investigate-avionics-codebase-review`);
  report goes to `.agents/tasks/codebase-review-2026-10-08.md`. This steering file
  created; hardware section to be filled from the report.
