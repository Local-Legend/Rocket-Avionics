# Avionics for Rocket - Codebase Review (read-only)

Date: 2026-10-08
Scope: every file under `/Users/ajarretm/Desktop/Avionics for Rocket/` (root scripts, `System Scripts/`, `3D Prints/` noted only).
Method: every listed file was read in full. Read-only shell (`ls`, `wc`, `grep`, `unzip -l`) was used for structure and line numbers. No project script was executed. The pyLoraRFM9x and lgpio library sources were read from upstream to resolve radio behaviour; those findings are labelled "library source" below.
Authority rule applied: `Grok_Cam_Test.py`, `Grok_GPSTest.py`, `Grok_TX_test.py` are bench-verified and win every conflict.
Additional evidence: `.agents/tasks/grok-conversation.txt` (an export of the bench session that produced the Grok_ files, placed in the output folder by the parent session during this review) was also read. Facts taken from it are labelled "bench transcript" and are treated as bench-verified context, second only to the Grok_ files themselves. See section 11.

---

## Executive summary

1. This is a two-Raspberry-Pi system: a Pi Zero flight computer (`TX_Telemetry.py`, autostarted by systemd) and a Pi 3 ground station (`RX_Temenetry.py`, launched from a desktop icon). Both are plain Python 3 on Raspberry Pi OS; there is no git repo, no tests, no requirements file.
2. Sensors on the flight side: MPU6050 IMU (I2C 0x68), HMC5883L magnetometer (I2C 0x1E), serial NMEA GPS (9600 baud), ArduCam OV5647 camera (CSI, local video only), and an RFM9x LoRa radio on SPI0 CE1 (DIO0=GPIO5, RST=GPIO25, 915 MHz, SF7/BW125/CR4-5, 5 dBm). A BMP280 barometer (0x76) exists only in `Altimeter.py` and is NOT in the flight code.
3. Packet format is a plain ASCII CSV: 10 raw signed-16-bit sensor integers followed by the last raw NMEA sentence. No sequence number, no checksum, no TX timestamp. RX decodes to g / deg/s / C / heading and stores one row per packet in a single SQLite table `telemetry` (33 columns).
4. Conflict with Grok (authoritative): GPS device path. `Grok_GPSTest.py` uses `/dev/serial0`; `GPSTest.py`, `Telemetry.py` and the deployable `TX_Telemetry.py` all hardcode `/dev/ttyS0`, which is wrong on a non-W Pi Zero (serial0 maps to ttyAMA0 there).
5. Conflict with Grok: radio `acks`. Grok uses `acks=False`; all other radio users (`Radio.py`, `Telemetry.py`, `TX_Telemetry.py`, `RX_Temenetry.py`) use `acks=True`. Reading the library source shows `acks=True` is currently inert because `send()` never sets the request-ACK flag, but the files disagree and should be aligned to `False`.
6. Biggest functional bug: `TX_Telemetry.poll_gps()` keeps the newest NMEA line of ANY type, but the RX decoder only parses `GGA`. With a typical GPS emitting 5-6 sentence types per second, most packets will carry a non-GGA sentence and show "no fix" on the ground even when the GPS has a fix. `Grok_GPSTest.py` filters for GGA; the flight script dropped that filter.
7. Biggest operational risk: the TX exits cleanly after a fixed 8-minute window (`FLIGHT_DURATION_S`), and `Restart=on-failure` does not restart a clean exit. If the rocket sits on the pad >8 min after boot, telemetry stops before launch. The unit file also has an `After=multi-user.target` / `WantedBy=multi-user.target` ordering cycle that may prevent autostart at boot.
8. Physics/config mismatch: the loop targets 10 Hz, but a ~120-byte packet at SF7/BW125 takes ~200 ms on air, so the real ceiling is ~4.5-5 Hz. The accelerometer is left at the +/-2 g default, which will saturate during boost on almost any hobby rocket.
9. No local telemetry log on the TX: if the LoRa link drops (apogee, landing, terrain), the data is gone. The Pi Zero is already writing video to SD; a local CSV is the cheapest high-value fix.
10. Robustness: all hardware init on TX (serial, I2C writes, LoRa assert, camera) is unguarded; any failure crashes the flight script. On both TX and RX, an exception inside `on_recv` silently kills the lgpio interrupt thread (library source), after which RX receives nothing and TX pays a 200 ms timeout per packet.
11. Deployment blocker (bench transcript): the Pi runs Python 3.13 with PEP 668 enforced; `pynmea2`, `pyLoraRFM9x`, `smbus2` and `bmp280` were installed into the venv `/home/pi/libs-venv` (system-site-packages on). `rocket-tx.service` runs `/usr/bin/python3`, which cannot see those packages, so the autostart service will fail at import. The README's `pip3 install ...` instructions also fail on this OS. The bench GPS is `$GNGGA` on `/dev/serial0 -> ttyAMA0` after `dtoverlay=disable-bt`, local declination is about -6.5 deg (code uses -0.38 deg), and a BMP280 IS on the shared I2C bus but unused by the flight code.

---

## 1. Target platform, OS, Python and libraries

Board (from `System Scripts/README.md` lines 7-11): TX = Raspberry Pi Zero (README does not say W/2W), RX = Raspberry Pi 3. Everything uses BCM GPIO numbering (pyLoraRFM9x requires BCM; the `Grok_TX_test.py` comment "CS on CE1, physical pin 26" is consistent with BCM GPIO7/CE1). Bench transcript: all sensor, radio and camera bench tests (the Grok_ files) were run on a **Pi 3**, not a Pi Zero; whether the flight computer is actually a Zero is unconfirmed (open question 1).

OS / Python: Raspberry Pi OS with Python 3.13 (bench transcript names `python3.13` in the venv, which implies the Debian 13 / Trixie-based release; config lives in `/boot/firmware/config.txt` and `/boot/firmware/cmdline.txt`). PEP 668 blocks system-wide pip. The bench environment is a venv at `/home/pi/libs-venv` with `include-system-site-packages = true` (so apt packages such as `python3-picamera2` and `python3-serial` are visible), interpreter `/home/pi/libs-venv/bin/python3`; Thonny is configured to use it. Code-level evidence is consistent: picamera2/libcamera, f-strings, underscore literals, `lxterminal`. **`rocket-tx.service` runs `/usr/bin/python3`, not the venv**, so the service cannot import the pip-installed packages (see section 5).

UART setup required on the bench Pi 3 (bench transcript): `enable_uart=1` and `dtoverlay=disable-bt` under `[all]` in `/boot/firmware/config.txt`; remove `console=serial0,115200` from `/boot/firmware/cmdline.txt`; `systemctl stop/disable serial-getty@ttyAMA0.service`. Result: `/dev/serial0 -> ttyAMA0` (PL011), device `crw-rw---- root dialout`, user `pi` in `dialout`. Before this, the mini-UART produced binary garbage (baud follows core clock) and the serial getty consumed GPS sentences.

| Import | Used by | Install route | Notes |
|---|---|---|---|
| `pyLoraRFM9x` (`LoRa`, `ModemConfig`) | Grok_TX_test, Radio, Telemetry, TX_Telemetry, RX_Temenetry | venv `pip install pyLoraRFM9x` after `sudo apt install swig python3-dev liblgpio-dev` (bench transcript); README's bare `pip3 install` fails under PEP 668 | PyPI 1.0.x depends on `lgpio` + `spidev`, NOT `RPi.GPIO` (upstream changelog 1.0.0; confirmed by the liblgpio build requirement on the bench). Installed in `/home/pi/libs-venv`, not visible to `/usr/bin/python3`. |
| `lgpio`, `spidev` | transitively via pyLoraRFM9x | built by pip from source (needs swig + liblgpio-dev) | lgpio opens a `.lgd-nfy*` notification file in the process CWD (lgpio source), so `WorkingDirectory=` / `cd` matters. |
| `RPi.GPIO` | Telemetry, TX_Telemetry, RX_Temenetry (only for `GPIO.cleanup()`) | apt `python3-rpi.gpio` (older OS) or `python3-rpi-lgpio` shim (newer OS); not mentioned in the bench transcript | Not needed by pyLoraRFM9x >=1.0. If missing, import fails and TX crashes. `cleanup()` with no channels set up only warns. On Python 3.13 / Trixie availability is uncertain. |
| `smbus` | Accelerometer, Telemetry, TX_Telemetry | apt `python3-smbus` (README) | Bench used `smbus2` (`import smbus2 as smbus`), not `smbus`. Whether `python3-smbus` is installed on the Pi is unknown; if not, TX_Telemetry fails at import. |
| `smbus2` | Altimeter, Magnetometer, mpu60501shot | venv `pip install smbus2` (bench transcript: installed) | Bench-proven library for MPU6050, HMC5883L and BMP280. NOT in README. |
| `serial` (pyserial) | GPSTest, Grok_GPSTest, Telemetry, TX_Telemetry | apt `python3-serial` and venv `pip install pyserial` (both done on bench) | |
| `pynmea2` | GPSTest, Grok_GPSTest, Telemetry (import only), TX_Telemetry (import only, unused), RX, telemetry_decode | venv `pip install pynmea2` (bench); not an apt package | Only in the venv. |
| `picamera2`, `libcamera` | Grok_Cam_Test, Camera, TX_Telemetry | apt `python3-picamera2` (README, bench) | System package; visible to the venv only because `include-system-site-packages = true` was set. |
| `mpu6050` (PyPI `mpu6050-raspberrypi`) | AccelTest | pip | Not in README or transcript. Different module from the vendored `mpu60501shot.py`. |
| `bmp280` (Pimoroni) | Altimeter | venv `pip install bmp280` (bench; package is `bmp280`, not `bmp280-python`) | Bench-proven. Not in README. |
| `tkinter` | RX (`--gui` only) | apt `python3-tk` (README) | Not pip-installable. |
| `sqlite3`, `threading`, `argparse`, `datetime`, `time`, `math`, `os`, `sys`, `pathlib` | RX, telemetry_db, playback, etc. | stdlib | |

Dead imports: `TX_Telemetry.py` imports `pynmea2` (line 3) and `math` (line 8) and defines `declination`/`pi` (110-111) without using them. `RX_Temenetry.py` imports `os` (line 29) and never uses it.

---

## 2. Hardware inventory and consolidated pinout / bus table

| Device | Part | Bus | Address / device path | GPIO (BCM) / header pins | Driver library | Files | Grok-verified? |
|---|---|---|---|---|---|---|---|
| IMU (accel + gyro + temp) | MPU6050 (register map: `3D Prints/RM-MPU-6000A.pdf`) | I2C-1 | `0x68` (bench: `i2cdetect` shows 0x68, WHO_AM_I 0x75 reads 0x68) | SDA GPIO2 (pin 3), SCL GPIO3 (pin 5) | `smbus` raw registers (TX), `smbus2` (mpu60501shot, bench), `mpu6050` pip lib (AccelTest) | AccelTest, Accelerometer, mpu60501shot, Telemetry, TX_Telemetry | No Grok file; bench transcript confirms address and wake. First read after wake returned raw temp 0 (exactly 36.53 C): discard the first sample. |
| Magnetometer / compass | HMC5883L (genuine; bench scan shows 0x1E, not the QMC5883L clone at 0x0D) | I2C-1 | `0x1E` | shared I2C pins | `smbus2` (Magnetometer, bench), `smbus` (Telemetry, TX) raw registers | Magnetometer, Telemetry, TX_Telemetry | No Grok file; bench transcript confirms chip and X,Z,Y register order. Sample reads 379,-311,34 and 229,145,-101. |
| Barometric altimeter | BMP280 (chip id 0xD0 = 0x58; BME280 would be 0x60) | I2C-1 | `0x76` | shared I2C pins, 3.3 V | `bmp280` (Pimoroni) + `smbus2` | Altimeter.py ONLY | No Grok file; bench transcript confirms it is wired and reads ~1011.7 hPa / ~13 m at Cocoa, FL. First conversion is garbage (724 hPa / 3009 m): discard it. **Not in flight code.** |
| GPS | multi-GNSS NMEA module, talker `$GN` (`$GNGGA`, `$GNZDA`, `$GPTXT ... ANTENNA OK`), 9600 8N1; bench fix with 16 sats at 28.3208 N 80.7416 W | UART (PL011 after `disable-bt`) | **`/dev/serial0 -> ttyAMA0`** (Grok + bench) vs `/dev/ttyS0` (others) | GPS TX -> Pi RXD GPIO15 (pin 10); GPS RX -> Pi TXD GPIO14 (pin 8) | `pyserial` + `pynmea2` | GPSTest, Grok_GPSTest, Telemetry, TX_Telemetry | Yes: `/dev/serial0`, 9600, timeout 0.5. Bench shows the module emits non-GGA sentences too (ZDA, TXT), which matters for bug 8.1.1. |
| Camera | OV5647 Camera Module v1, 2592x1944 (`rpicam-hello --list-cameras`); Camera.py calls it ArduCam OV5647 | CSI | n/a | CSI ribbon | `picamera2` (+ `libcamera.Transform`) | Grok_Cam_Test (stills), Camera (video), TX_Telemetry (video) | Yes for stills via `create_still_configuration` (5 JPEGs saved on bench); H.264 video path is NOT Grok-verified |
| LoRa radio | Adafruit-style RFM9x breakout (labels VIN, GND, EN, G0, SCK, MISO, MOSI, CS, RST); EN left floating (pulled high on board); antenna on ANT pad required | SPI0 | `/dev/spidev0.1` (spi_port 0, CE1) | CS CE1 = GPIO7 (pin 26); DIO0/G0 GPIO5 (pin 29); RESET GPIO25 (pin 22); MOSI GPIO10 (19), MISO GPIO9 (21), SCLK GPIO11 (23); VIN pin 1 (3.3 V), GND pin 6 | `pyLoraRFM9x` | Grok_TX_test, Radio, Telemetry, TX_Telemetry, RX_Temenetry | Yes: spi_channel 1, interrupt 5, reset 25, spi_port 0, 915 MHz, Bw125Cr45Sf128, acks False. tx_power: Grok file says 5, bench transcript's final script says 14 (both transmitted to a second board). Bench saw `AssertionError: LoRa initialization failed` until CS wiring was fixed. |

Pin/address differences between files: GPS device path only (see Conflicts, section 7). I2C addresses, SPI channel, interrupt and reset pins are identical everywhere they appear and match the bench transcript's pin table exactly. The ground station radio uses the identical wiring (RX uses `LoRa(1, 5, node, reset_pin=25)`). Bench transcript confirms MPU6050, HMC5883L and BMP280 share SDA/SCL without address collision.

MPU6050 register configuration written by TX (`TX_Telemetry.py` 119-128, identical in `Telemetry.py` 38-48 and `Accelerometer.py` 27-41):

| Register | Value | Meaning |
|---|---|---|
| `SMPLRT_DIV 0x19` | 7 | sample rate = 8 kHz / (1+7) = 1 kHz (DLPF off) |
| `PWR_MGMT_1 0x6B` | 1 | wake, clock = PLL X-gyro |
| `CONFIG 0x1A` | 0 | DLPF off (260 Hz accel / 256 Hz gyro bandwidth) |
| `GYRO_CONFIG 0x1B` | 24 (0x18) | FS_SEL=3 -> +/-2000 deg/s -> 16.4 LSB per deg/s |
| `ACCEL_CONFIG 0x1C` | never written | default 0 -> **+/-2 g** -> 16384 LSB/g |
| `INT_ENABLE 0x38` | 1 | DATA_RDY interrupt enabled; INT pin not wired or read |

HMC5883L configuration (TX 129-134): Config A = `0x70` (8-sample average, 15 Hz output, normal), Config B = `0xA0` (gain 5, +/-4.7 Ga, 390 LSB/Ga), Mode = `0` (continuous). Data registers read X@0x03, Z@0x05, Y@0x07 (correct order for this chip).

---

## 3. Per-file summary

| File | Role | Purpose / main symbols | Deps | Notable |
|---|---|---|---|---|
| `AccelTest.py` (30 lines) | standalone bench test | Prints accel/gyro/temp at 0.5 s using the PyPI `mpu6050` class at 0x68. MIT header from MrTijn. | `mpu6050` pip | Default ranges (+/-2 g, +/-250 dps). `#!/usr/bin/env python` (py2-style shebang). Infinite loop. Orphan. |
| `Accelerometer.py` (88) | bench test / prototype | electronicwings MPU6050 tutorial: `MPU_Init()`, `read_raw_data()`, 300-iteration print loop. | `smbus` | Writes GYRO_CONFIG=24 (2000 dps) but divides gyro by 131 (250 dps) at lines 80-82: **wrong gyro scale**. Sign fix uses `> 32768` (should be `>=`). Temp printed in F. Ancestor of the MPU code in TX. |
| `Altimeter.py` (9) | bench test | BMP280 at 0x76: 5 temp/pressure reads then `get_altitude()`. | `smbus2`, `bmp280` | Only barometer reference in the project; not integrated into TX. |
| `Camera.py` (90) | reference module / bench test | `create_camera()`, `start_recording()`, `stop_recording()`; 1080p30 H.264 8 Mbps, 180 deg via `Transform(hflip=1,vflip=1)`, writes `/home/pi/Desktop/Camera/flight_<stamp>.h264`. | `picamera2`, `libcamera` | Not imported by TX; TX re-implements it as `start_camera()` at 720p/4 Mbps. `libcamera` import failure swallowed (no rotation then). |
| `GPSTest.py` (13) | bench test (older) | Reads `/dev/ttyS0` 9600, strict utf-8 decode, parses lines containing GGA, prints fix. | `pyserial`, `pynmea2` | No try/except around `pynmea2.parse` (crashes on truncated sentence); strict decode crashes on garbage; `find('GGA') > 0`. Superseded by `Grok_GPSTest.py`. |
| `Grok_Cam_Test.py` (23) | **authoritative** bench test | Picamera2 still config, 5 JPEGs 2 s apart into `/home/pi/Desktop/photos_<stamp>/`. | `picamera2` | Proves camera + picamera2 work. Does not exercise video/H264Encoder/Transform. |
| `Grok_GPSTest.py` (24) | **authoritative** bench test | `/dev/serial0` 9600 timeout 0.5; ascii decode `errors="ignore"`; only lines starting with `$`; only GGA; `pynmea2.ParseError` caught; prints lat/lon/alt/sats. | `pyserial`, `pynmea2` | Defines the correct device path and the correct GGA filtering/robustness pattern. |
| `Grok_TX_test.py` (14) | **authoritative** bench test | `LoRa(spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0, my_address=1, freq=915, tx_power=5, modem_config=Bw125Cr45Sf128, acks=False)`; `send("hello", 255)` (broadcast). | `pyLoraRFM9x` | Defines the authoritative radio wiring and parameters. |
| `Magnetometer.py` (76) | bench test / prototype | electronicwings HMC5883L tutorial: `Magnetometer_Init()`, `read_raw_data()`, heading = atan2(y,x)+declination, prints integer degrees at 1 Hz. | `smbus2` | Comment says "MPU6050 Registers" (copy-paste). Does NOT apply the compass calibration offsets. `declination=-0.00669` rad is the tutorial's value, not the launch site's. Uses `smbus2` while TX uses `smbus`. |
| `Radio.py` (21) | prototype RX | pyLoraRFM9x README example: `LoRa(1, 5, 10, reset_pin=25, ..., tx_power=5, acks=True)`, `on_recv` prints from/msg/RSSI/SNR, `set_mode_rx()`. | `pyLoraRFM9x` | Address 10, `acks=True` (conflicts with Grok). `send_to_wait` commented out. Script exits immediately after `set_mode_rx()` so it never actually receives (no sleep/loop). Orphan. |
| `Telemetry.py` (129) | prototype TX (direct ancestor of TX_Telemetry) | Same sensor init/reads as TX; blocking `serialPort.readline()`; 3-iteration loop sending CSV to address 2 from address 10 at 1 Hz. | `RPi.GPIO`, `pyserial`, `pynmea2`, `smbus`, `pyLoraRFM9x` | `/dev/ttyS0`; 1 s sleeps between each init write (8 s init); `acks=True`; address scheme (10 -> 2) incompatible with RX node 1; strict utf-8 decode on GPS and on received payload. Superseded. |
| `mpu60501shot.py` (273) | vendored library | m-rtijn/mpu6050 class on `smbus2`: range set/read, scale selection by reading config regs, `get_accel_data(g=)`, `get_gyro_data`, `get_temp`, `get_all_data`. | `smbus2` | Correct `>= 0x8000` sign handling; correct per-range scale factors. Not imported by anything (AccelTest imports the PyPI module named `mpu6050`, not this file). Good reference for a +/-16 g change. |
| `compass calibration.txt` (1 line + 1) | data notes | "Y -411 to 76 add 167 = -244 to 243", "Z -333 to 108 add 112.5 = -221 to 220". | | X axis not characterised. Consumed by `telemetry_decode.py` / `RX_Temenetry.py` as `MAG_OFFSET_Y=167.0`, `MAG_OFFSET_Z=112.5`, `MAG_OFFSET_X=0.0`. |
| `System Scripts/README.md` (150) | ops doc | File-to-Pi table, deploy via scp to `/home/pi/Desktop/`, apt/pip installs, systemd install/test, RX desktop launcher, run modes, playback, DB notes. | | Says TX runs "a fixed number of times" (stale: now a fixed 8-minute duration). Omits `make_rx_gui_shortcut.sh`, `smbus2`, `--break-system-packages`. |
| `System Scripts/TX_Telemetry.py` (256) | **deployable flight TX** | Module-level init: serial, smbus, LoRa, MPU/MAG register writes, camera start; `poll_gps()` non-blocking newest-line buffer; 10 Hz deadline-scheduled loop for 8 min building CSV and `lora.send(message, 1)`; rate meter print; `finally` stops camera + `GPIO.cleanup()`. | all of the above + `picamera2` | `/dev/ttyS0` (conflict); `acks=True` (conflict); GPS line not filtered to GGA; no local log; no error handling around init; `status` from send ignored; `> 32768` sign bug; +/-2 g range; 8-min clean exit. |
| `System Scripts/RX_Temenetry.py` (471) | **deployable ground RX** | Inlines decode + DB; `LiveState` (lock-protected latest/stats); terminal dashboard (ANSI clear, 5 Hz), Tkinter GUI, scroll mode; `main()` argparse `--db --node --gui --scroll`; `LoRa(1, 5, args.node, reset_pin=25, ..., acks=True)`; `on_recv` -> decode -> `db.insert` -> `state.update`. | `pynmea2`, `RPi.GPIO`, `pyLoraRFM9x`, `sqlite3`, `tkinter` opt | Decode/DB are duplicated from the two modules (maintenance hazard). `on_recv` catches only `TelemetryDecodeError`; DB or other errors escape into the lgpio callback thread. Filename misspelling is real and referenced by `start_rx.sh`, `make_rx_gui_shortcut.sh`, README. |
| `System Scripts/telemetry_decode.py` (223) | RX module (used by playback indirectly, and for tests) | Constants (16384, 16.4, declination, MAG offsets, `NUM_SENSOR_FIELDS=10`), `decode()`, `compute_heading()`, `_parse_gps()`, `format_readout()`, synthetic self-test in `__main__`. | `pynmea2` | Logic identical to the inlined copy in RX (verified by reading). Only GGA parsed. pynmea2 checksum not validated (`parse(..., check=False)` default). |
| `System Scripts/telemetry_db.py` (137) | RX module | `TelemetryDB`: schema, `insert()`, `sessions()`, `rows_for_session()`, `latest_session()`. Default path `/home/pi/Desktop/RxTelemetry.db`. | `sqlite3` | No `check_same_thread=False` (fine for single-threaded playback). Commit per insert. Replaces an older ACL/GYR/TMP/MAG/GPS multi-table schema (docstring). |
| `System Scripts/playback.py` (110) | post-flight tool | `--list`, `--session`, `--speed`, `--db`; replays rows with original inter-packet gaps capped at 10 s. | `telemetry_db` | No hardware deps; runs anywhere with the DB. Imports `telemetry_db` from CWD (must run from `System Scripts/`). |
| `System Scripts/rocket-tx.service` (28) | systemd unit | `User=pi`, `WorkingDirectory`/`ExecStart` hardcoded to `/home/pi/Desktop/Avionics for Rocket/System Scripts`, `/usr/bin/python3`, `ExecStartPre=/bin/sleep 10`, `Restart=on-failure`, `RestartSec=5`, journal output, `After=multi-user.target` + `WantedBy=multi-user.target`. | | Ordering cycle (see section 5). Clean exit after 8 min is not restarted. |
| `System Scripts/install_tx_autostart.sh` (53) | installer | Requires root + Linux + systemctl; copies unit to `/etc/systemd/system/`, `daemon-reload`, `enable`. `set -euo pipefail`. | bash | Sound. Does not `start` the service (README does that manually). |
| `System Scripts/start_rx.sh` (27) | launcher | cd to own dir; `python3 RX_Temenetry.py` (terminal dashboard); waits for Enter so the summary stays visible. | bash | Fine. Uses `python3` from PATH. |
| `System Scripts/make_rx_gui_shortcut.sh` (64) | generator | Writes `~/Desktop/Rocket-RX-GUI.desktop` with auto-detected absolute path, `Exec=python3 "<path>/RX_Temenetry.py" --gui`, `Path=` set, `Terminal=false`; marks trusted via `gio`. | bash | Path-independent (good). Not documented in README. |
| `System Scripts/Rocket-RX.desktop` (12) | launcher | `Exec=lxterminal --title=... -e "/home/pi/Desktop/Avionics for Rocket/System Scripts/start_rx.sh"`, `Terminal=false`. | lxterminal | Hardcoded path and terminal emulator. |
| `3D Prints/` | CAD | `GPS.SLDPRT`, `Telemetry.SLDPRT`, `Telemetry Base.SLDPRT`, `Ground Station v2.SLDPRT`, `bolt.SLDPRT`, `infill.SLDPRT`; `Flight hardware STL files.zip` (bolt, GPS, infill, Telemetry Base, Telemetry Component tree, Telemetry test Stand); `Ground Station STL files.zip` (v2 base, battery strap, LED clip, screen clip, shell); `RM-MPU-6000A.pdf` (MPU-6000/6050 register map). | | Binaries, not parsed. |

---

## 4. Telemetry system architecture

### 4.1 Data path

```
MPU6050 (I2C 0x68) ----\
HMC5883L (I2C 0x1E) ----+--> TX_Telemetry.py loop (target 10 Hz, real ~4.5 Hz)
GPS NMEA (UART 9600) ---/        |  builds ASCII CSV string
                                 v
                        pyLoraRFM9x LoRa.send(msg, header_to=1)      [TX node addr 2]
                                 |  RadioHead 4-byte header + payload, 915 MHz SF7/BW125
                                 v
                        RFM9x on Pi 3 ---> lgpio DIO0 interrupt thread
                                 |
                        RX_Temenetry.main.on_recv(payload)            [RX node addr 1]
                                 |-> decode()   (inlined telemetry_decode logic)
                                 |-> TelemetryDB.insert()  (inlined telemetry_db logic, commit per row)
                                 |-> LiveState.update()
                                 v
          terminal dashboard (5 Hz redraw) | Tkinter GUI (200 ms) | --scroll log
                                 .
          later: playback.py --> telemetry_db.TelemetryDB.rows_for_session() --> console replay
Camera (CSI) --> picamera2 H264Encoder --> /home/pi/Desktop/Camera/flight_<stamp>.h264  (local only, never transmitted)
```

### 4.2 Packet format (ASCII CSV, one LoRa packet per sample)

Built at `TX_Telemetry.py` line 225; parsed at `telemetry_decode.decode()` / RX lines 101-153. Field separator `,`. No framing beyond the LoRa packet, no sequence number, no timestamp, no checksum. The GPS field itself contains commas, so the decoder splits off exactly the first 10 fields and rejoins the rest.

| Index | Field | Type on the wire | Source register | RX conversion |
|---|---|---|---|---|
| 0 | acc_x | signed int (decimal text) | MPU 0x3B/0x3C | / 16384.0 -> g; * 9.80665 -> m/s^2 |
| 1 | acc_y | int | 0x3D/0x3E | same |
| 2 | acc_z | int | 0x3F/0x40 | same |
| 3 | gyro_x | int | 0x43/0x44 | / 16.4 -> deg/s |
| 4 | gyro_y | int | 0x45/0x46 | same |
| 5 | gyro_z | int | 0x47/0x48 | same |
| 6 | temp | int | 0x41/0x42 | /340 + 36.53 -> C; *1.8+32 -> F |
| 7 | mag_x | int | HMC 0x03/0x04 | + 0.0 (no X calibration) |
| 8 | mag_y | int | HMC 0x07/0x08 | + 167.0 |
| 9 | mag_z | int | HMC 0x05/0x06 | + 112.5 (unused for heading) |
| 10.. | GPS | raw NMEA sentence text (may be any type; may be empty before first line) | UART | parsed only if it contains `GGA`: timestamp, lat/lon (decimal degrees via pynmea2 `.latitude/.longitude`), N/S, E/W, altitude (m MSL), units |

Heading = `int(degrees(atan2(mag_y+167, mag_x+0) + (-0.00669 rad)))` wrapped to 0..359.

RadioHead header (library source, `send()`): `[header_to, header_from, header_id=0, header_flags=0]` prepended by pyLoraRFM9x; TX sends to 1 from 2; `Grok_TX_test.py` sends to 255 (broadcast) from 1. RX accepts packets whose `header_to` is its own address or 255.

Packet size: typical ~115-130 bytes (46 chars of sensor CSV + ~70-80 chars of GGA + 4 header). Worst case under the 255-byte LoRa limit.

Packet rate: target `SAMPLE_RATE_HZ = 10` (TX 190). Calculated air time at SF7/BW125/CR4-5, explicit header, CRC on, 8-symbol preamble (Semtech formula): 120 B = 200 ms, 130 B = 215 ms. `LoRa.send()` blocks until TX_DONE or its 0.2 s `wait_packet_sent_timeout`, so the achievable rate is about **4.5-5 Hz**, not 10. The deadline scheduler (TX 244-248) handles the overrun by resetting `next_sample`, so there is no drift, just a lower rate; the on-screen rate meter will show it. At BW500 the same packet is ~50 ms (10 Hz feasible).

### 4.3 Radio configuration (effective values; authoritative = Grok_TX_test.py)

| Parameter | Value | Where set |
|---|---|---|
| Frequency | 915 MHz | explicit in Grok (`freq=915`); library default elsewhere |
| Modem config | `Bw125Cr45Sf128` = BW 125 kHz, CR 4/5, SF7, explicit header, RX payload CRC on, AGC auto, LDRO off (regs 0x72/0x74/0x04) | all files |
| TX power | 5 dBm (library minimum; clamps 5..23) | all files |
| Preamble | 8 symbols | library |
| Sync word | not set by project or library (library source) -> SX127x default 0x12 | inferred |
| SPI | port 0, channel 1 (`/dev/spidev0.1`), 5 MHz | all files |
| Interrupt (DIO0) | BCM GPIO5, rising edge via lgpio | all files |
| Reset | BCM GPIO25 (pulsed low 10 ms at init) | all files |
| Node addresses | Grok TX: 1 -> 255 broadcast. TX_Telemetry: 2 -> 1. RX: 1 (`--node`). Telemetry.py: 10 -> 2. Radio.py: 10 (RX). | per file |
| acks | **False** (Grok) vs True (all others) | conflict |
| default_mode | 0 = return to RX-continuous after TX (so the TX radio also listens between packets) | library default |
| CRC error handling | library reads only RX_DONE and ignores PAYLOAD_CRC_ERROR; corrupt packets reach `on_recv` (library source) | n/a |

### 4.4 DB schema (identical in `telemetry_db.py` 21-53 and RX 163-186)

Default file `/home/pi/Desktop/RxTelemetry.db`. One table plus one index. One row per received packet; `session_id` = `flight_%Y%m%d_%H%M%S` from the RX Pi clock at receiver start.

| Column | Type | Meaning |
|---|---|---|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| session_id | TEXT NOT NULL | one receiver run = one flight |
| recv_epoch | REAL NOT NULL | `time.time()` on RX at insert |
| recv_iso | TEXT NOT NULL | ISO form of the above |
| raw_acc_x, raw_acc_y, raw_acc_z | INTEGER | as transmitted |
| raw_gyro_x, raw_gyro_y, raw_gyro_z | INTEGER | |
| raw_temp | INTEGER | |
| raw_mag_x, raw_mag_y, raw_mag_z | INTEGER | |
| accel_g_x, accel_g_y, accel_g_z | REAL | g (+/-2 g scale) |
| gyro_dps_x, gyro_dps_y, gyro_dps_z | REAL | deg/s |
| temp_c, temp_f | REAL | |
| heading_deg | INTEGER | 0..359 |
| gps_time | TEXT | NMEA UTC time-of-day (no date) |
| gps_lat, gps_lon | REAL | signed decimal degrees |
| gps_lat_dir, gps_lon_dir | TEXT | N/S, E/W (redundant with sign) |
| gps_alt | REAL | GGA altitude (m MSL) |
| gps_alt_units | TEXT | "M" |
| gps_raw | TEXT | the raw NMEA text as received |
| rssi, snr | REAL | from the RFM9x (dBm, dB) |
| index `idx_session_time` | (session_id, recv_epoch) | |

Not stored although computed by `decode()`: `accel_ms2`, `mag_cal`. Not extracted although present in GGA: fix quality, satellite count, HDOP.

---

## 5. Deployment and operations

TX (Pi Zero):
- `install_tx_autostart.sh` (run with sudo on the Pi) copies `rocket-tx.service` to `/etc/systemd/system/`, runs `systemctl daemon-reload` and `systemctl enable`. Refuses to run as non-root or on non-Linux/no-systemctl hosts.
- `rocket-tx.service`: `User=pi`; `WorkingDirectory=/home/pi/Desktop/Avionics for Rocket/System Scripts`; `ExecStart=/usr/bin/python3 ".../TX_Telemetry.py"`; `ExecStartPre=/bin/sleep 10`; `Restart=on-failure`, `RestartSec=5`; stdout/stderr to journal.
- **Interpreter mismatch (high).** `ExecStart=/usr/bin/python3` (unit line 13) is the system interpreter. The bench transcript shows `pynmea2`, `pyLoraRFM9x`, `smbus2`, `bmp280` and `pyserial` were installed only into `/home/pi/libs-venv` (PEP 668 blocks system pip on this OS). The service will therefore die with `ModuleNotFoundError` on `pyLoraRFM9x`/`pynmea2` at import, restart every ~15 s, and never transmit. Fix: `ExecStart=/home/pi/libs-venv/bin/python3 "...TX_Telemetry.py"` (the venv has system-site-packages enabled so `picamera2` and `serial` from apt remain visible). The same applies to `start_rx.sh` / both `.desktop` launchers, which call bare `python3` (system) on the Pi 3 unless that Pi has a differently configured environment.
- Hardcoded assumptions that break on another host: user `pi` and `/home/pi/...` (newer images no longer default to user `pi`); camera recordings go to `/home/pi/Desktop/Camera` (TX 20); README's scp examples are Mac-side, while the bench transcript shows a Windows PowerShell (`$env:USERPROFILE`) workflow was also used.
- Ordering problem: a unit that is `WantedBy=multi-user.target` is implicitly ordered Before that target, while this unit also declares `After=multi-user.target` (lines 4-5, 28). That is an ordering cycle; systemd breaks cycles by deleting a job, which can be the service's start job, so autostart at boot may silently not happen even though `systemctl start` works (manual start has no cycle because the target is already active). Verify on the Pi with `journalctl -b | grep -i "ordering cycle"` or `systemd-analyze verify`. Recommended fix: `After=local-fs.target` (or `basic.target`/`network.target`) and drop `Wants=multi-user.target`.
- Exit behaviour: TX ends its loop after 8 minutes (TX 184) and exits with status 0; `Restart=on-failure` does not relaunch it. README line 83-86 acknowledges this and offers `Restart=always`.
- SIGTERM from `systemctl stop` kills Python without running the `finally` block (Python has no default SIGTERM handler), so the camera file is not finalised and `GPIO.cleanup()` does not run.
- lgpio creates `.lgd-nfy*` files in the process CWD (lgpio source line 273), which is why `WorkingDirectory=` is required and why the `System Scripts/` folder on the Pi will accumulate those files.

RX (Pi 3):
- `start_rx.sh`: cd to its own directory, `python3 RX_Temenetry.py` (terminal dashboard), then `read` to hold the window open. `Rocket-RX.desktop` runs it inside `lxterminal` using the hardcoded `/home/pi/...` path; README says copy it to `/home/pi/Desktop/` and `chmod +x`.
- `make_rx_gui_shortcut.sh`: generates `Rocket-RX-GUI.desktop` with the real install path (`Exec=python3 ".../RX_Temenetry.py" --gui`, `Path=` set, `Terminal=false`) and marks it trusted via `gio`. This is the portable launcher; the static `.desktop` is not.
- Run modes: default terminal dashboard; `--gui` Tkinter; `--scroll` plain log; `--db PATH`; `--node ADDR` (default 1, must match TX `send(..., 1)`).
- Playback: `python3 playback.py [--list | --session SID] [--speed N] [--db PATH]` from `System Scripts/` (imports `telemetry_db` from CWD).

README summary: scp the whole project folder from the Mac to `/home/pi/Desktop/` on each Pi; TX: `apt install python3-picamera2 python3-smbus python3-serial i2c-tools`, `pip3 install pynmea2 pyLoraRFM9x`, enable I2C/SPI/serial/camera in `raspi-config`, run the installer, test with `systemctl start` + `journalctl -f`, reboot. RX: `pip3 install pynmea2 pyLoraRFM9x` (+ `python3-tk` for GUI), make launcher executable, copy `.desktop` to Desktop, double-click.

README gaps versus what actually worked on the bench (transcript): bare `pip3 install` is blocked by PEP 668 on this OS (the working path is `python3 -m venv ~/libs-venv`, activate, `pip install pynmea2 pyserial smbus2 bmp280 pyLoraRFM9x`, with `sudo apt install swig python3-dev liblgpio-dev` first and `include-system-site-packages = true` in `pyvenv.cfg`); UART setup (`enable_uart=1`, `dtoverlay=disable-bt`, remove `console=serial0,115200`, disable `serial-getty@ttyAMA0`) is not in the README but `raspi-config` "serial" alone does not disable Bluetooth's claim on the PL011; `smbus2`/`bmp280`/`mpu6050-raspberrypi` for root scripts are missing; stale "fixed number of times" wording; `make_rx_gui_shortcut.sh` undocumented; the service interpreter path does not match the venv.

---

## 6. Root-level scripts vs System Scripts

| Root file | Relationship to System Scripts |
|---|---|
| `Telemetry.py` | Direct ancestor of `TX_Telemetry.py`: same constants, same `read_MPU_data`/`read_MAG_data`, same CSV message. TX_Telemetry replaced the 3-iteration 1 Hz loop with a timed 10 Hz loop, blocking `readline()` with `poll_gps()`, 1 s init sleeps with 0.2 s, address 10->2 with 2->1, and added the camera. Superseded; keep only as history. |
| `Accelerometer.py`, `Magnetometer.py` | Tutorial prototypes whose init/read code was pasted into `Telemetry.py` -> `TX_Telemetry.py`. The heading math from `Magnetometer.py` moved to `telemetry_decode.compute_heading()`. Superseded. |
| `Camera.py` | Near-duplicate of `TX_Telemetry.start_camera()` (1080p/8 Mbps vs 720p/4 Mbps). Not imported. Reference/bench. |
| `GPSTest.py` | Older version of `Grok_GPSTest.py` (ttyS0, no error handling). Superseded by the Grok file. |
| `Grok_GPSTest.py`, `Grok_Cam_Test.py`, `Grok_TX_test.py` | Authoritative bench references. Their robustness patterns (GGA filter, ascii-ignore decode, ParseError catch, `acks=False`, `/dev/serial0`) were only partially carried into TX_Telemetry. |
| `Radio.py` | pyLoraRFM9x README example in RX mode; a proto-ground-station. Superseded by `RX_Temenetry.py`. Orphan. |
| `AccelTest.py`, `mpu60501shot.py` | Alternative MPU6050 drivers (pip class / vendored class). Not used by the system. Orphans, but `mpu60501shot.py` is the only place with correct multi-range scaling. |
| `Altimeter.py` | Orphan: BMP280 is never integrated. The flight system has no barometric altitude. |
| `compass calibration.txt` | Consumed by `telemetry_decode.py`/RX constants. Not used by `Magnetometer.py` or TX. |
| `telemetry_decode.py`, `telemetry_db.py` | Duplicated verbatim (compacted) inside `RX_Temenetry.py`; only `playback.py` imports the modules. Two copies of the packet contract. |

---

## 7. Conflicts list

| # | Topic | File A says | File B says | Grok_ / authoritative value | Severity |
|---|---|---|---|---|---|
| 1 | GPS serial device | `GPSTest.py` 9, `Telemetry.py` 9, `TX_Telemetry.py` 55: `/dev/ttyS0` | | `Grok_GPSTest.py` 15: **`/dev/serial0`** (bench: `serial0 -> ttyAMA0` after `dtoverlay=disable-bt`) | High, confirmed. On the bench-configured Pi 3, `ttyS0` is the now-unassigned mini-UART (it produced binary garbage before the config change), so `TX_Telemetry.py` would open the wrong port or fail. On a non-Bluetooth Pi Zero `serial0 -> ttyAMA0` as well. `serial0` is correct on every model. |
| 2 | Radio acks | `Radio.py` 11, `Telemetry.py` 35, `TX_Telemetry.py` 113, `RX_Temenetry.py` 443: `acks=True` | | `Grok_TX_test.py` 12: **`acks=False`** | Medium. Functionally inert with current library + `send()` (no REQ_ACK flag is ever set), but it is a documented disagreement and could change behaviour with a different library version or if `send_to_wait` is ever used (10 Hz ACK traffic would halve air time). |
| 3 | Radio frequency | all other files: not specified (library default 915) | | Grok: explicit `freq=915` (bench: "US frequency is 915 MHz") | Low. Same effective value; make it explicit. |
| 4 | Node addressing | `Telemetry.py` 35/125: self 10, send to 2; `Radio.py`: self 10; `TX_Telemetry.py` 113/227: self 2, send to 1; RX: self 1 | | Grok: self 1, send to 255 (broadcast) | Low for TX/RX (compatible: RX accepts addr 1 and 255). `Telemetry.py`/`Radio.py` are incompatible with the RX and are prototypes. |
| 5 | GPS sentence selection | `TX_Telemetry.poll_gps()` 65-87: keeps newest line of ANY type | RX/decoder 84/97: parses only `GGA` | `Grok_GPSTest.py` 5: filter `"GGA" in line` before parsing | High (functional bug, see 8.1). |
| 6 | GPS decode robustness | `GPSTest.py` 13, `Telemetry.py` 121: strict `.decode('utf-8')`, no ParseError catch | `TX_Telemetry.py` 82: `errors="replace"` | Grok: `decode("ascii", errors="ignore")`, `startswith("$")`, `except pynmea2.ParseError` | Low (TX already tolerant; RX catches ParseError). |
| 7 | Camera configuration | `Camera.py`: 1920x1080 @30, 8 Mbps; `TX_Telemetry.py`: 1280x720 @30, 4 Mbps; both H.264 + `Transform(hflip,vflip)` | | `Grok_Cam_Test.py`: `create_still_configuration()`, JPEG stills to `/home/pi/Desktop/photos_<stamp>` | Not a parameter conflict (different use), but the video path, `H264Encoder`, `FileOutput`, `Transform`, and `controls["FrameRate"]` are NOT Grok-verified on this Pi Zero. |
| 8 | Gyro scale factor | `Accelerometer.py` 80-82: /131 (250 dps) while writing GYRO_CONFIG=24 | `telemetry_decode.py` 32 / RX 43: /16.4 (2000 dps) | n/a (no Grok). Register value 24 => 16.4 is correct; `Accelerometer.py` is wrong. | Low (bench script only). |
| 9 | Sign conversion | `Accelerometer.py` 52, `Magnetometer.py` 43, `Telemetry.py` 79/93, `TX_Telemetry.py` 157/171: `if value > 32768` | `mpu60501shot.py` 88: `if value >= 0x8000` | n/a. `>=` is correct; raw 0x8000 must become -32768. | Low-Medium (only hits at exact negative full scale, which is exactly where a saturating accelerometer lands). |
| 10 | I2C library | `Accelerometer.py`, `Telemetry.py`, `TX_Telemetry.py`: `smbus` | `Altimeter.py`, `Magnetometer.py`, `mpu60501shot.py`: `smbus2` | Bench transcript: `smbus2` installed in the venv and used for all three I2C chips (`import smbus2 as smbus`). | Medium. `smbus` (apt `python3-smbus`) may not be installed on the Pi; standardise the flight code on `smbus2`, which is bench-proven. |
| 11 | Compass calibration | `compass calibration.txt`: Y +167, Z +112.5, X unknown | `Magnetometer.py`, `TX_Telemetry.py`: no offsets applied; `telemetry_decode.py` 40-42 / RX 48-50: Y=167.0, Z=112.5, X=0.0 applied on RX | n/a | Medium. Offsets are applied, but heading uses X (uncalibrated) and Y; Z offset is applied but unused. X must be characterised. |
| 12 | Magnetic declination | `Magnetometer.py` 17, `Telemetry.py` 32, `TX_Telemetry.py` 110, decoder/RX: `-0.00669` rad (= -0.38 deg) | | Bench transcript: local (Cocoa, FL) declination is about **-6.5 deg = -0.113 rad**; the -0.00669 value is the ElectronicWings tutorial constant. | Medium, confirmed. Decoder heading is off by ~6 deg even with perfect calibration. |
| 16 | Radio TX power | `Grok_TX_test.py` 10: `tx_power=5` | bench transcript final script: `tx_power=14` | Both bench-proven on the same wiring. The user's rule makes the Grok_ file authoritative (5), but 14 is the library default and a more realistic flight value. | Low on the bench, high for flight range. Needs a user decision. |
| 17 | Python interpreter | `rocket-tx.service` 13: `/usr/bin/python3`; `start_rx.sh`, both `.desktop` files: bare `python3` | | Bench transcript: `/home/pi/libs-venv/bin/python3` is the only interpreter with `pynmea2`, `pyLoraRFM9x`, `smbus2`, `bmp280`. | High. Service and launchers will fail at import unless pointed at the venv. |
| 18 | GPS talker / sentence set | `telemetry_decode.py` self-test and docstrings use `$GPGGA`; `_parse_gps` checks `"GGA" in` (talker-agnostic, fine) | | Bench: `$GNGGA`, plus `$GNZDA` and `$GPTXT` sentences. | None for decode (substring match), but confirms multiple sentence types are on the wire (bug 8.1.1). |
| 13 | TX run length | `Telemetry.py`: 3 packets; README 83: "fixed number of times" | `TX_Telemetry.py` 184: fixed 8 minutes | n/a | Doc drift; operational risk in 8.2. |
| 14 | Hardware init pacing | `Telemetry.py`: `sleep(1)` after each register write (8 s) | `TX_Telemetry.py`: `sleep(.2)` (1.6 s); `Accelerometer.py`: none | n/a | None. |
| 15 | GPIO library for radio | `Telemetry.py`, `TX_Telemetry.py`, RX import `RPi.GPIO` and call `GPIO.cleanup()` | pyLoraRFM9x >=1.0 uses `lgpio` (library source/changelog) | Grok: no RPi.GPIO import at all | Low. Unneeded dependency; harmless warning on cleanup. |

---

## 8. Known issues and risks (prioritised)

### 8.1 Functional bugs
1. **GPS sentence type mismatch (TX 65-87 vs decoder 84/97).** `poll_gps()` returns the newest complete line regardless of type. Typical modules emit GGA, GLL, GSA, GSV (multiple), RMC, VTG every second, and a full burst at 9600 baud takes ~0.4-0.5 s, so most ~200 ms polls land on a non-GGA line. Result: `gps.lat/lon/altitude = None` ("no fix") for most rows, `max_alt` rarely updates. Fix: keep the newest line containing `GGA` (optionally also keep newest `RMC` for speed/course/date), or configure the GPS to emit GGA only.
2. **Accelerometer range +/-2 g.** `ACCEL_CONFIG` is never written (TX), decoder divides by 16384. Boost acceleration on hobby rockets is routinely 5-20 g; the sensor will clip at +/-2 g and the "MAX |accel|" readout will be meaningless. Fix: write `ACCEL_CONFIG=0x18` (+/-16 g) and change the decoder scale to 2048.0 in BOTH decode copies (or transmit the range).
3. **Sign conversion boundary** `> 32768` vs `>= 32768` in TX 157/171 (also Accelerometer/Magnetometer/Telemetry). Raw 0x8000 is returned as +32768 instead of -32768. Rare but exactly the saturated-negative case.
4. **Torn 16-bit reads.** High and low bytes are read in two separate I2C transactions (TX 154-155, 168-169) while the MPU updates at 1 kHz; a value can straddle an update. Use `read_i2c_block_data(0x68, 0x3B, 14)` (one transaction for accel+temp+gyro) and `read_i2c_block_data(0x1E, 0x03, 6)` for the mag. Also cuts I2C time from ~20 transactions to 2 per loop.
5. **`Radio.py` never receives**: it calls `set_mode_rx()` and exits (no loop/sleep). Prototype only.
6. **`_parse_gps` treats altitude 0.0 as None** (`float(msg.altitude) if msg.altitude`). Negligible.
7. **First sample after power-up is invalid** for both the MPU6050 (raw temp 0 -> exactly 36.53 C; bench transcript) and the BMP280 (724 hPa / 3009 m first conversion; bench transcript). TX starts transmitting immediately after init with no warm-up read; the first packet(s) of every session carry garbage and `LiveState.max_alt`/`max_accel_g` on the RX would latch onto them if a BMP280 is added. Discard the first N reads after init.

### 8.2 Operational / data-loss risks
0. **Service interpreter cannot import the radio/GPS libraries** (unit line 13 vs bench venv `/home/pi/libs-venv`). As deployed, the autostart service will crash-loop at import and never transmit. This must be fixed before any flight. See section 5.
1. **8-minute clean exit + `Restart=on-failure`** (TX 184, unit line 20). Pad holds longer than 8 minutes after power-on end telemetry before launch. Fix: `Restart=always` (README suggests it) and/or run until shutdown with periodic camera file rotation.
2. **systemd ordering cycle** (`After=multi-user.target` with `WantedBy=multi-user.target`) may delete the start job at boot. Verify on hardware; change `After=`.
3. **No local telemetry log on the TX.** Every packet exists only on the air. Append each CSV line (with `monotonic()` and the loop counter) to a file next to the video; it costs microseconds.
4. **Unguarded hardware init on TX** (serial open 55, I2C writes 119-134, `LoRa()` assertion 113, camera start 179). A loose camera ribbon or absent magnetometer crashes the flight script before any telemetry is sent; systemd then restart-loops every ~15 s. Wrap camera and each sensor in try/except with degraded operation (send zeros / flag bits) rather than exit.
5. **I2C error inside the loop** (any `OSError 121 Remote I/O`) propagates, runs `finally`, exits non-zero, service restarts after 5 s + 10 s `ExecStartPre` = ~15 s gap, new video file. Catch per-iteration and continue.
6. **Exception in `on_recv` kills the lgpio interrupt thread permanently** (lgpio source: `_callback_thread.run()` calls `cb.func` with no try/except). RX: `on_recv` (RX 422-440) only catches `TelemetryDecodeError`; a `sqlite3` error (disk full, locked DB) ends all reception while the dashboard keeps running and just shows "stale". TX: `on_recv` (TX 141-144) does strict `.decode('utf-8')` on anything addressed to node 2/255, including CRC-corrupt packets the library does not filter; a `UnicodeDecodeError` there ends TX_DONE handling, after which every `send()` waits the full 0.2 s timeout. Wrap both callbacks in broad try/except.
7. **No CRC/sequence/TX-time in the packet**, and the library ignores the hardware CRC-error flag (library source `_handle_rx_done`). A corrupted field that still parses as an int is stored as valid data. Add a loop counter (already `x`), a TX monotonic timestamp, and an 8/16-bit checksum (or at least check `PAYLOAD_CRC_ERROR`).
8. **RX wall clock.** `session_id`, `recv_epoch`, `recv_iso` come from the Pi 3 clock; with no network at the field and no RTC the time is wrong, and two flights could collide on `session_id` if the clock repeats. The TX camera filename has the same issue. Consider using GPS time from packets, or set the clock before launch.
9. **`status` from `lora.send()` is discarded** (TX 227). Repeated `False` (TX timeout) is the only symptom of a hung radio; count and log it, re-init the radio on N consecutive failures.
10. **SIGTERM does not run `finally`** (TX 249-257): `systemctl stop` leaves the H.264 stream unfinalised. Install a `signal.signal(SIGTERM, ...)` handler that raises `SystemExit`.
11. **Busy-wait in `wait_packet_sent()`** (library source) spins a CPU core for up to 200 ms per packet; on the single-core Zero that competes with picamera2 and may drop frames. Lower priority.
12. **Raw `.h264` elementary stream** has no container/timestamps; it must be wrapped (`ffmpeg -framerate 30 -i in.h264 -c copy out.mp4`) and cannot be time-aligned to telemetry without a TX timestamp.

### 8.3 Unit and calibration issues
- Accel: g and m/s^2 both produced (decoder) but only g stored; fine. Gyro deg/s; temp C and F; GPS altitude m MSL; heading integer degrees; declination in radians added to radians (consistent). No feet anywhere.
- Heading is only valid when the sensor is level; on a rocket it is meaningless in flight. Fine for pad orientation only.
- Declination -0.00669 rad is a tutorial constant; X-axis mag offset unknown; Z offset unused. Heading should be treated as uncalibrated.
- MPU DLPF off (CONFIG=0) with ~5 Hz sampling aliases motor vibration into every sample; `CONFIG=3` (44 Hz) or higher sample rate would help. Design choice, not a bug.
- `Accelerometer.py` gyro scale /131 is wrong for FS_SEL=3 (bench only).

### 8.4 Code-health issues
- Decode and DB logic exist twice (RX inline vs modules). Any packet-format change (e.g. +/-16 g) must be made in both or playback and live will disagree.
- Dead imports/constants (section 1). `pi` constant hand-written instead of `math.pi`.
- Tutorial copy-paste comments ("#some MPU6050 Registers" in Magnetometer.py; "#Read Accelerometer raw value" above the mag reads in Telemetry.py).
- `RX_Temenetry.py` filename typo is load-bearing (referenced by 3 files + README); renaming requires touching all of them.
- No tests, no `requirements.txt`, no pinned versions, no git.

---

## 9. Open questions (not resolvable from code)

Resolved by the bench transcript (kept for the record): pyLoraRFM9x is the lgpio-based 1.0.x line (liblgpio-dev was required to build); the magnetometer is a genuine HMC5883L at 0x1E; the BMP280 is physically wired at 0x76 (chip id 0x58); the GPS talker is `$GN` and the module emits at least GGA, ZDA and TXT; the OS is a Python 3.13 Raspberry Pi OS with PEP 668 and the venv `/home/pi/libs-venv`; local declination is about -6.5 deg.

Still open:
1. **Which board flies?** All bench work (Grok_ files) was done on a Pi 3; the README says the TX is a Pi Zero. If a separate Pi Zero is the flight computer, none of its OS/venv/UART configuration has been verified, and it needs the same `enable_uart=1` + console removal (plus `disable-bt` if it is a Zero W / 2 W). If the bench Pi 3 flies, the README's TX/RX board assignment is wrong and the ground station hardware is unverified.
2. Is the ground-station Pi 3 the same board as the bench Pi 3, and does it have its own venv with `pyLoraRFM9x`/`pynmea2`? `start_rx.sh` calls bare `python3`.
3. Is `python3-smbus` (apt) installed anywhere, or only `smbus2` (venv)? Decides whether `TX_Telemetry.py`'s `import smbus` works at all.
4. Is `RPi.GPIO` importable under Python 3.13 on this OS (`python3-rpi.gpio` vs the `python3-rpi-lgpio` shim)? If not, TX and RX crash at import; the dependency is unnecessary and should be removed regardless.
5. Was the +/-2 g accelerometer range a deliberate choice (e.g. for a low-power rocket) or an oversight?
6. TX power for flight: Grok file 5 dBm, transcript 14 dBm, library max 23 dBm. Which value does the user want as the flight setting, and what antennas are on each end?
7. Has 720p30 H.264 recording plus the telemetry loop ever been run for 8 minutes on the flight board (thermal, SD write, frame drops)? Only stills are bench-verified.
8. Expected pad-wait duration and flight profile (max altitude, time to apogee) to size `FLIGHT_DURATION_S`, packet rate and radio settings.
9. Was the ordering-cycle warning ever observed in `journalctl -b`, i.e. has autostart from cold boot been confirmed on the flight board?
10. Was the earlier multi-table schema (ACL/GYR/TMP/MAG/GPS, per `telemetry_db.py` docstring) ever deployed, and does an old `RxTelemetry.db` with that schema exist on the Pi 3? `CREATE TABLE IF NOT EXISTS telemetry` would coexist with it, not migrate it.
11. The GPS burst order (which sentence is emitted last each second) is still unknown; it determines how often the current `poll_gps()` happens to capture GGA. The fix (filter for GGA) is needed regardless.

---

## 10. Recommendations (nothing implemented; ordered by value/risk)

0. Point every launcher at the venv interpreter: `ExecStart=/home/pi/libs-venv/bin/python3 ...` in `rocket-tx.service`; `/home/pi/libs-venv/bin/python3` (or `"$HOME/libs-venv/bin/python3"`) in `start_rx.sh` and `make_rx_gui_shortcut.sh`; update the README install section to the venv + `swig python3-dev liblgpio-dev` + UART steps from the bench transcript. Without this nothing autostarts.
1. Align `TX_Telemetry.py` with the Grok files: `/dev/serial0` (TX 55), `acks=False`, explicit `freq=915` (TX 113). Do the same in `RX_Temenetry.py` 442-443. Switch `import smbus` to `import smbus2 as smbus` (bench-proven). Drop `RPi.GPIO`. Treat `GPSTest.py`, `Telemetry.py`, `Radio.py` as deprecated.
2. Fix `poll_gps()` to keep the newest GGA line (mirror `Grok_GPSTest.py`'s `"GGA" in line` check and `ascii`/`ignore` decode). Optionally also carry the newest RMC.
3. Make the flight script never stop: remove or greatly extend `FLIGHT_DURATION_S`, set `Restart=always`, fix `After=` in the unit, add a SIGTERM handler, and rotate the video file every N minutes.
4. Log every packet locally on the TX (CSV with `monotonic()`, counter, and the same string sent over the air).
5. Add a sequence number, a TX timestamp and a simple checksum to the packet; update both decoder copies; store the new fields in the DB.
6. Set `ACCEL_CONFIG=0x18` (+/-16 g) and `GYRO_CONFIG` as now; switch to burst reads; fix `>=` sign handling; update both decoder scale constants.
7. Wrap TX init and loop body in try/except per subsystem so a dead camera or magnetometer degrades rather than kills telemetry; wrap `on_recv` on both sides in a broad try/except so the lgpio thread survives.
8. Remove the duplicate decode/DB code from `RX_Temenetry.py` and import `telemetry_decode`/`telemetry_db` (the README's "no local-module dependency" goal can be met by shipping the folder, which it already does), or at least add a test that asserts both copies agree.
9. Consider `Bw500Cr45Sf128` and a higher `tx_power` after a range test; with BW500 the current packet fits in ~50 ms and 10 Hz becomes real. This is a deliberate deviation from the Grok bench settings, so it needs the user's sign-off and a new bench test.
10. Integrate the BMP280 (confirmed wired at 0x76) into the packet for barometric altitude and apogee detection; zero it on the pad; discard its first conversion.
11. Set `DECLINATION` to the launch-site value (about -0.113 rad for Cocoa, FL per the bench transcript; 0 for bench tests), characterise the X-axis mag offset, discard the first MPU sample after wake.
12. Housekeeping: `requirements.txt` (venv) + apt list (`python3-picamera2 python3-serial i2c-tools swig python3-dev liblgpio-dev`); fix README drift; remove dead imports; initialise git.

---

## 11. Bench transcript facts (from `.agents/tasks/grok-conversation.txt`)

This is the session that produced the three Grok_ files. Facts here are second only to the Grok_ files themselves. Where the transcript and a Grok_ file differ (only `tx_power`), the Grok_ file is treated as authoritative per the user's rule and the difference is logged in conflict #16.

| Topic | Bench-verified fact |
|---|---|
| Board used | Raspberry Pi 3 (not a Pi Zero). |
| OS / Python | Raspberry Pi OS with Python 3.13; PEP 668 enforced; `/boot/firmware/config.txt` and `/boot/firmware/cmdline.txt` are the real config files (the `/boot/config.txt` "DO NOT EDIT" file is a stub). |
| Python environment | `python3 -m venv ~/libs-venv`; `pip install pynmea2 pyserial smbus2 bmp280 pyLoraRFM9x`; `sudo apt install swig python3-dev liblgpio-dev` needed before `pyLoraRFM9x`; `include-system-site-packages = true` set in `pyvenv.cfg` so `python3-picamera2` (apt) is importable; interpreter `/home/pi/libs-venv/bin/python3` (also set in Thonny). An unused `gps-venv` also exists. |
| UART | `enable_uart=1`, `dtoverlay=disable-bt`, remove `console=serial0,115200`, `systemctl stop/disable serial-getty@ttyAMA0.service`; after reboot `/dev/serial0 -> ttyAMA0`, perms `crw-rw---- root dialout`, `pi` in `dialout`. Mini-UART (`ttyS0`) gave binary garbage; serial getty was eating GGA sentences as login attempts. |
| GPS | 9600 baud; `$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E`; also `$GPTXT,01,01,01,ANTENNA OK*35` and `$GNZDA`. Location Cocoa, FL (28.3208 N, 80.7416 W). pynmea2 `.latitude/.longitude` give decimal degrees; `.lat/.lon` are raw ddmm.mmmm. Decode as ascii with `errors="ignore"`, skip lines not starting with `$`, catch `pynmea2.ParseError`; `msg.num_sats`, `msg.gps_qual` (0 = no fix). |
| MPU6050 | `i2cdetect -y 1` shows 0x68; WHO_AM_I (0x75) = 0x68; PWR_MGMT_1 reads 0 after wake; first temperature read was raw 0 (36.53 C exactly), next raw -3552 (~26.1 C). Import as `import smbus2 as smbus`. |
| HMC5883L | 0x1E (genuine, not QMC5883L at 0x0D); register order X, Z, Y; sample reads 379,-311,34 and 229,145,-101; heading assumes level board; local declination about -6.5 deg (-0.113 rad), tutorial value -0.00669 rad is wrong for this site; use 0 on the bench. Keep away from power wires. |
| BMP280 | 0x76, chip id 0x58; `pip install bmp280` (not `bmp280-python`); first sample bogus (724 hPa / 3009 m), then ~24.8 C, 1011.7 hPa, ~13 m vs 1013.25 default sea-level; zero altitude on the pad. 3.3 V supply. Shares SDA/SCL with the other two chips. |
| RFM9x LoRa | Adafruit-style breakout (VIN, GND, EN, G0, SCK, MISO, MOSI, CS, RST); SPI0 (`/dev/spidev0.0` and `0.1` present); MOSI 19/GPIO10, MISO 21/GPIO9, SCK 23/GPIO11, CS 26/GPIO7 (CE1), RST 22/GPIO25, G0 29/GPIO5, VIN pin 1 (3.3 V), GND pin 6; EN may float. Antenna required (915 MHz whip or 78 mm wire). First init raised `AssertionError: LoRa initialization failed` until CS wiring was fixed. Final working call: `LoRa(spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0, my_address=1, freq=915, tx_power=14, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False)`; `send("hello", 255)` was received by a second board using the same modem config. |
| Camera | `rpicam-hello --list-cameras` -> ov5647 Camera Module v1, 2592x1944; `rpicam-still` produced a 154 KB JPEG; `python3-picamera2` via apt; the Grok_Cam_Test.py script saved cam_1..5.jpg to `/home/pi/Desktop/photos_2026-10-07_20-04-38/`. libcamera INFO lines are normal. |
| File transfer | `scp -r` from a Windows PC (`$env:USERPROFILE\Desktop\Avionics for Rocket`) to `pi@raspberrypi.local:/home/pi/Desktop/`; quotes required because of the space. |
| Summary table in transcript | GPS UART `/dev/serial0 -> ttyAMA0` 9600; MPU-6050 I2C-1 0x68; HMC5883L I2C-1 0x1E; BMP280 I2C-1 0x76 (id 0x58); RFM9x SPI0 CE1 915 MHz G0 GPIO5 RST GPIO25; Camera CSI ov5647; Python `/home/pi/libs-venv/bin/python3`. |

---

## Sources consulted outside the workspace (for library behaviour)

- pyLoraRFM9x README and `lora.py` (constructor signature, lgpio dependency since 1.0.0, `send()`/ACK logic, interrupt handling): https://github.com/epeters13/pyLoraRFM9x
- lgpio 0.2.2.0 Python source (`lgpio_extra.py` `_callback_thread.run`, notification file in CWD): https://pypi.org/project/lgpio/
- Raspberry Pi UART naming (`/dev/serial0` -> `ttyAMA0` on non-Bluetooth models, `ttyS0` on Bluetooth models): https://github.com/RealVNC/raspi-documentation/blob/master/configuration/uart.md and https://www.chiark.greenend.org.uk/~ijackson/quicksand/d/doc/rppal/uart/index.html
- systemd target ordering (units `WantedBy=` a target are started before it): https://www.freedesktop.org/software/systemd/man/latest/systemd.target.html ; cycle-breaking behaviour: https://knowledge.broadcom.com/external/article/440058
- LoRa air-time figures were calculated from the standard Semtech SX127x formula (SF7, BW125/500, CR4/5, explicit header, CRC on, 8-symbol preamble); they are estimates, not measurements.

Content from external sources was paraphrased for compliance with licensing restrictions.
