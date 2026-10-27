# System Scripts update plan (TX = Pi Zero 2 W, RX = Pi 3)

Date: 2026-10-08 (revised after six user decisions, see section A0)
Workflow: `wf_e93a05d6a7ea64f7` (step `plan`)
Backup of the folder before any edit: `.agents/backup/System Scripts-2026-10-08/`
Workspace root (contains spaces, always quote): `/Users/ajarretm/Desktop/Avionics for Rocket`
Target folder: `/Users/ajarretm/Desktop/Avionics for Rocket/System Scripts`

Authority order used for every constant below:
1. User decisions recorded in `.kiro/steering/avionics-project.md` ("Decisions made by the user (2026-10-08)").
2. `Grok_Cam_Test.py`, `Grok_GPSTest.py`, `Grok_TX_test.py` (bench-tested on the Zero 2 W; win every file-vs-file conflict).
3. `.agents/tasks/grok-conversation.txt` (bench transcript; cited as "transcript §N"). Its "Pi 3" wording is wrong: every bench test ran on the Zero 2 W (user decision).
4. `.agents/tasks/codebase-review-2026-10-08.md` (verified against 1-3 before use).
5. Library source read for exact APIs: pyLoraRFM9x `lora.py` (constructor keywords, `send()`, callback thread) and Pimoroni `bmp280/__init__.py` (`setup()`, `get_temperature()`, `get_pressure()`, `get_altitude()` formula).

**RX REDEPLOY REQUIRED: YES.** Packet format changes (+/-16 g scale, four new BMP280 fields),
the declination constant, the DB schema (4 new columns with migration), and the RX launchers
all change. The Pi 3 has never been set up, so the README RX section becomes a from-scratch
guide. See section D for the full RX file list. No RX wiring is changed (and the RX RFM9x
wiring is assumed identical to the TX pinout but unverified).

---

## A0. User decisions that drive this plan (verbatim intent, from the steering file)

| # | Decision | Effect in this plan |
|---|---|---|
| U1 | Declination = -6.5 deg = -0.113 rad (Cocoa, FL) | both decoder copies; dead TX constant removed (D10) |
| U2 | Flight `tx_power` = 15 dBm (override of Grok's 5; Grok file untouched) | TX constant `LORA_TX_POWER_DBM = 15` (D5) |
| U3 | Accelerometer +/-16 g: TX writes `ACCEL_CONFIG (0x1C) = 0x18`; RX scale 2048.0 LSB/g in both copies; gyro unchanged; sign fix `>= 32768` | D23, packet section A1 |
| U4 | BMP280 in the flight telemetry: fail-soft init, discard first conversion, temp/pressure/altitude each loop, pad-zero, absolute values too; GPS NMEA stays LAST; bump `NUM_SENSOR_FIELDS`; decoders, schema (with migration), dashboards, playback updated | D11, A1, C.1, C.4-C.7 |
| U5 | All bench work was on the Zero 2 W (fully set up). Pi 3 has NEVER been set up: README RX = from-scratch guide; launchers point at `/home/pi/libs-venv/bin/python3`; RX wiring assumed = TX pinout, unverified | D18, C.8-C.11 |
| U6 | Both Pis are `raspberrypi.local`: README uses it for both scp examples + mDNS collision warning + rename recommendation (`sudo raspi-config` -> System Options -> Hostname, e.g. `rocket-rx`) or use IPs / one Pi at a time | D21, C.11 |

---

## A1. New packet layout (exact; this is the contract both sides implement)

ASCII CSV, one LoRa packet per sample, built in `TX_Telemetry.py`, parsed by `telemetry_decode.decode()` and its inlined copy in `RX_Temenetry.py`. `NUM_SENSOR_FIELDS = 14` (was 10). Everything after the 14th comma is the raw GPS sentence (it contains commas, so the decoder splits off exactly 14 fields and rejoins the rest). pyLoraRFM9x prepends its 4-byte RadioHead header.

| Idx | Field | On the wire | Unavailable sensor | RX conversion |
|---|---|---|---|---|
| 0-2 | acc_x, acc_y, acc_z | signed int (raw MPU, +/-16 g) | `0` | `/ 2048.0` -> g (U3) |
| 3-5 | gyro_x, gyro_y, gyro_z | signed int (raw, +/-2000 dps) | `0` | `/ 16.4` -> deg/s (unchanged) |
| 6 | temp | signed int (raw MPU temp) | `0` | `/ 340 + 36.53` -> C (unchanged) |
| 7-9 | mag_x, mag_y, mag_z | signed int (raw HMC5883L) | `0` | `+ (0.0, 167.0, 112.5)` offsets (unchanged) |
| 10 | baro_temp_c | `"{:.2f}"` | empty string | `float` or `None` |
| 11 | baro_press_hpa | `"{:.2f}"` (absolute, hPa) | empty string | `float` or `None` |
| 12 | baro_alt_m | `"{:.2f}"` altitude vs 1013.25 hPa standard sea level | empty string | `float` or `None` |
| 13 | baro_agl_m | `"{:.2f}"` altitude relative to the pad baseline pressure | empty string | `float` or `None` |
| 14.. | GPS | raw `$GNGGA`/`$GPGGA` sentence, may be empty | empty string | parsed only if it contains `GGA` |

Example: `0,0,2048,0,0,0,-3552,100,-67,0,24.81,1011.73,13.21,0.00,$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E`
Size: ~140-150 bytes typical (max well under the 255-byte LoRa payload limit); ~235 ms air time at SF7/BW125, so the real packet ceiling drops to ~4 Hz (loop still targets 10 Hz; the deadline scheduler absorbs it).
Compatibility: a 14-field RX rejects old 10-field packets (`TelemetryDecodeError`), so TX and RX must be redeployed together. Empty baro fields decode to `None` and are stored as `NULL`.

Altitude formula (same as Pimoroni `BMP280.get_altitude()`, so the absolute value matches the bench's ~13 m): `alt = ((qnh / p) ** (1 / 5.257) - 1) * (t_c + 273.15) / 0.0065`, computed on the TX from one `get_pressure()` + one `get_temperature()` per loop, once with `qnh = 1013.25` (absolute) and once with `qnh = pad_pressure_hpa` (AGL).

---

## A2. Decisions (made here so the implementer never has to choose)

| # | Topic | Decision | Rationale / source |
|---|---|---|---|
| D1 | GPS device | `/dev/serial0`, 9600 baud | `Grok_GPSTest.py` L15; transcript §2-3 (`serial0 -> ttyAMA0` after `disable-bt`). |
| D2 | GPS decode | `raw.decode("ascii", errors="ignore").strip()`, accept only lines starting with `$`, keep only the newest line containing `GGA`. Never utf-8. | `Grok_GPSTest.py` L5, L20-22; transcript §1. Fixes the "no fix on most packets" bug (TX kept any sentence type; decoder parses only GGA). |
| D3 | I2C library | `import smbus2 as smbus` (keeps the `smbus.SMBus(1)` call sites unchanged) | transcript §6 ("Import with import smbus2 as smbus"). |
| D4 | Radio init (TX) | `LoRa(spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0, my_address=2, freq=915, tx_power=15, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False)`; `lora.send(message, 1)` | `Grok_TX_test.py` L3-13 for pins/port/freq/modem/acks; U2 for power. Addresses stay TX=2 -> RX=1 because `RX_Temenetry.py --node` defaults to 1 and the library accepts own-address or 255. |
| D5 | `tx_power` | `LORA_TX_POWER_DBM = 15` on TX (U2). RX keeps `tx_power=5` (it never transmits). | Comment in code: "user override 2026-10-08; Grok_TX_test.py bench value was 5; library clamps 5..23". |
| D6 | Radio init (RX) | Same pins (1, 5, reset 25). Change only `acks=True` -> `acks=False` and add explicit `freq=915`; keyword form. | Consistency with `Grok_TX_test.py` L9, L12. Same effective behaviour. Not a hardware change. |
| D7 | `RPi.GPIO` | Remove import and `GPIO.cleanup()` on TX and RX. | pyLoraRFM9x >= 1.0 uses lgpio (transcript §4; library source imports `lgpio`, `spidev`). `Grok_TX_test.py` has no RPi.GPIO. |
| D8 | Camera | Keep H.264 video with the picamera2 video API (1280x720 @ 30, 4 Mbps, 180-degree `Transform`), fail-soft. Output: per-run folder `/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/flight_YYYY-MM-DD_HH-MM-SS.h264`. | TX records video today, so the Grok stills pattern applies at the library/lifecycle level (`Grok_Cam_Test.py` L11-14). Folder convention and stamp `%Y-%m-%d_%H-%M-%S` from `Grok_Cam_Test.py` L7-9 / transcript §10. Video path is not bench-verified; README says so. |
| D9 | Compass calibration | Keep applying `compass calibration.txt` offsets on the RX (Y +167.0, Z +112.5, X 0.0) in both decoder copies; TX sends raw. | Already in `telemetry_decode.py` L40-42 / `RX_Temenetry.py` L48-50; raw on the wire allows re-processing. X uncharacterised (Q7). |
| D10 | Declination | `DECLINATION = -0.113` in both decoder copies; delete dead `declination`/`pi` from TX. | U1; transcript §7. Comment: use 0.0 for bench heading checks. |
| D11 | BMP280 (U4) | Init: `from smbus2 import SMBus; from bmp280 import BMP280; chip = BMP280(i2c_dev=SMBus(1), i2c_addr=0x76); chip.setup(mode="normal", temperature_oversampling=16, pressure_oversampling=16, temperature_standby=62.5)`; `sleep(1)`; discard one `get_pressure()`; then pad baseline = mean of 10 `get_pressure()` readings 0.1 s apart. All inside one try/except -> `baro = None` on failure. Loop: one `get_temperature()` + one `get_pressure()`, two altitudes via the A1 formula. | transcript §8 (constructor, address, first-sample garbage, zero on the pad). `setup()` kwargs verified in library source; 62.5 ms standby (valid lookup value) gives ~7 Hz updates vs 500 ms default so AGL tracks a rocket (default was bench-verified; this is a tuning change, flagged in README). Baseline is re-captured on every service (re)start; README notes this. |
| D12 | First-sample garbage | After MPU init: `sleep(0.1)` + one throwaway read of all 7 values. BMP280: see D11. | transcript §6 (raw temp 0 = 36.53 C), §8 (724 hPa / 3009 m). |
| D13 | Fail-soft policy (TX) | GPS, I2C bus, MPU-6050, HMC5883L, BMP280, camera: try/except at init, print `"<sensor> unavailable: <err>"`, continue (ints -> `0`, baro -> empty fields). **LoRa init is the one hard failure**: print and `sys.exit(1)`. Loop body: per-group try/except around sensor reads and around `lora.send`; errors counted and summarised once per second; loop never dies. | Without the radio there is no telemetry; the radio is initialised before the camera starts, so a restart loop never thrashes video files; `AssertionError: LoRa initialization failed` is a wiring fault (transcript §9) the journal must surface. |
| D14 | Loop pacing | Keep the `time.monotonic()` deadline scheduler and `SAMPLE_RATE_HZ = 10`; keep the 0.2 s `sleep()`s between register writes. Update comments: 4-core Zero 2 W; real ceiling ~4 Hz from air time. | Nothing relies on single-core slowness; the scheduler is already absolute-time based. |
| D15 | Flight window | Keep `FLIGHT_DURATION_S` default 8 min, read from env `TX_FLIGHT_DURATION_S` (unit file sets 480). | Lets the unit tune the window and lets the Mac harness run the script for 1.5 s. Default unchanged. |
| D16 | Service Restart policy | `Restart=on-failure` -> `Restart=always`; keep `RestartSec=5` and `ExecStartPre=/bin/sleep 10`; replace `After=multi-user.target` + `Wants=multi-user.target` with `After=local-fs.target` (removes the ordering cycle with `WantedBy=multi-user.target`); add `Environment=PYTHONUNBUFFERED=1`. | The script exits cleanly after 8 min and `on-failure` never relaunches it, so a pad wait > 8 min ends telemetry before launch (review §8.2.1); README already offered `always`. Documented in README (15 s gap and pad re-zero every 8 min). |
| D17 | SIGTERM | `signal.signal(SIGTERM, handler)` raising `SystemExit(0)` so `systemctl stop` runs `finally:` and finalises the video. | Review §8.2.10; small, behaviour-preserving. |
| D18 | RX interpreter (U5) | `start_rx.sh` and `make_rx_gui_shortcut.sh` use the fixed path `RX_PYTHON="/home/pi/libs-venv/bin/python3"`; if it is not executable they print `ERROR: ... not found. Run the "RX side (Pi 3) - first-time setup" steps in README.md` and exit 1 (start_rx.sh still waits for Enter so the message stays visible). `Rocket-RX.desktop` keeps `Exec=lxterminal ... start_rx.sh` (so the end-of-run summary stays on screen) and gains a comment line stating that start_rx.sh runs `/home/pi/libs-venv/bin/python3`. | A silent `python3` fallback would surface as a confusing `ModuleNotFoundError`; the user decided the Pi 3 gets the same venv. |
| D19 | Decoder duplication | Keep `RX_Temenetry.py` self-contained; every decoder/DB change applied to BOTH copies; equality verified by the Mac check in item 5. | Existing design (RX docstring). |
| D20 | GPS fix fields | Both decoder copies gain `fix_quality` and `num_sats` in the `gps` dict; lat/lon/alt forced to `None` when `gps_qual == 0`; `_gps_line` shows `sats N`. Not stored in the DB. | transcript §1 (`msg.gps_qual`, `msg.num_sats`). Pad go/no-go indicator. |
| D21 | Hostnames (U6) | README uses `pi@raspberrypi.local` in both scp examples plus the collision warning and rename recommendation. | U6. |
| D22 | Header block | Every modified Python file gets a 3-line `#` header right after the shebang/first line: target board, interpreter path, authority sources. | Step requirement. |
| D23 | Accel range (U3) | TX writes `ACCEL_CONFIG = 0x18` in `init_imu()`; both decoders use `ACCEL_SCALE_MODIFIER_16G = 2048.0` (rename the constant; delete `_2G`). Dashboard/GUI accel format widths unchanged (`{:+7.3f}` fits +/-16.000). | U3; `mpu60501shot.py` L25, L36 (`ACCEL_SCALE_MODIFIER_16G = 2048.0`, `ACCEL_RANGE_16G = 0x18`). |
| D24 | DB migration (U4) | `SCHEMA` gains `baro_temp_c REAL, baro_press_hpa REAL, baro_alt_m REAL, baro_agl_m REAL` (after `gps_raw`, before `rssi`). After `executescript(SCHEMA)`, read `PRAGMA table_info(telemetry)` and `ALTER TABLE telemetry ADD COLUMN <col> REAL` for each missing column (`_ensure_columns()`); same in the RX inline copy. No schema-version table (the PRAGMA guard is sufficient and idempotent). `_INSERT` gains the 4 columns/placeholders. | Existing `RxTelemetry.db` files keep working (old rows get `NULL`). |
| D25 | Baro on the displays (U4) | `LiveState` adds `max_baro_agl`; dashboard and GUI gain a `BARO` line (`alt <abs> m  AGL <agl> m  <hPa> hPa  <C> C`, or `BARO        n/a`) and the MAX line gains `MAX baro AGL`; scroll line gains `agl <x>m`; `telemetry_decode.format_readout` gains the same BARO line; `playback._print_row` prints `BARO :` when `baro_agl_m` is not `NULL`. | U4. |

---

## B. Conflicts found: current System Scripts vs Grok_* / transcript / user decisions

| # | File:line (current) | Current value | Authoritative value | Source | Action |
|---|---|---|---|---|---|
| C1 | `TX_Telemetry.py:55` | `serial.Serial("/dev/ttyS0", 9600, timeout=0)` | `/dev/serial0` | `Grok_GPSTest.py` L15; transcript §2-3, §11 | Change. |
| C2 | `TX_Telemetry.py:82` | `ln.decode("utf-8", errors="replace")` | `decode("ascii", errors="ignore")` | `Grok_GPSTest.py` L20; transcript §1 | Change. |
| C3 | `TX_Telemetry.py:65-87` (`poll_gps`) | keeps newest line of ANY NMEA type; no `$` check | only `$`-prefixed lines, only `GGA` | `Grok_GPSTest.py` L5-6, L21-22 | Change (high impact: decoder parses only GGA). |
| C4 | `TX_Telemetry.py:113` | `acks=True` | `acks=False` | `Grok_TX_test.py` L12 | Change. |
| C5 | `TX_Telemetry.py:113` | no `freq=` | `freq=915` explicit | `Grok_TX_test.py` L9; transcript §9 | Change. |
| C6 | `TX_Telemetry.py:113` | positional `LoRa(1, 5, 2, reset_pin=25, ...)`, no `spi_port` | keyword form with `spi_port=0` | `Grok_TX_test.py` L3-8 | Rewrite (same pins). |
| C7 | `TX_Telemetry.py:113` | `tx_power=5` | `15` | U2 (overrides Grok's 5) | Change TX only. |
| C8 | `TX_Telemetry.py:4` | `import smbus` | `import smbus2 as smbus` | transcript §6 | Change. |
| C9 | `TX_Telemetry.py:1,256`; `RX_Temenetry.py:412,466` | `import RPi.GPIO as GPIO` / `GPIO.cleanup()` | not needed (lgpio) | `Grok_TX_test.py`; transcript §4 | Remove both sides. |
| C10 | `TX_Telemetry.py:110` (dead), `telemetry_decode.py:33`, `RX_Temenetry.py:45` | `-0.00669` rad | `-0.113` rad | U1; transcript §7 | Change decoders; delete dead TX constant. |
| C11 | `TX_Telemetry.py:157,171` | `if value > 32768` | `>= 32768` | U3; `mpu60501shot.py` L88 | Change. |
| C12 | `rocket-tx.service:13` | `ExecStart=/usr/bin/python3 ...` | `/home/pi/libs-venv/bin/python3` | transcript §4, §11 | Change (service dies at import otherwise). |
| C13 | `README.md:42-44` | apt `python3-smbus` + `pip3 install pynmea2 pyLoraRFM9x` | venv `~/libs-venv`; apt `swig python3-dev liblgpio-dev python3-picamera2 i2c-tools python3-serial`; pip-in-venv `pynmea2 pyserial smbus2 bmp280 pyLoraRFM9x`; `include-system-site-packages = true` | transcript §4, §6, §8, §10 | Rewrite section (TX is already set up; README documents it for rebuilds). |
| C14 | `README.md`, `install_tx_autostart.sh:5,10`, `rocket-tx.service:11`, `TX_Telemetry.py:12-13,22-25` | "Raspberry Pi Zero", "the Zero's single core" | Raspberry Pi Zero 2 W (quad-core) | user | Update wording everywhere. |
| C15 | `README.md:46-47` | "enable serial port (raspi-config)" | `enable_uart=1` + `dtoverlay=disable-bt` under `[all]` in `/boot/firmware/config.txt`; remove `console=serial0,115200` from `/boot/firmware/cmdline.txt`; stop/disable `serial-getty@ttyAMA0.service`; reboot; `/boot/config.txt` is a stub | transcript §2-3 | Rewrite. |
| C16 | `README.md:83-86` | "runs ... a fixed number of times" | runs for `FLIGHT_DURATION_S` (8 min), relaunched by `Restart=always` | `TX_Telemetry.py:184`; D16 | Fix wording. |
| C17 | `start_rx.sh:22`; `make_rx_gui_shortcut.sh:44,64` | bare `python3` | `/home/pi/libs-venv/bin/python3` | U5; transcript §4 | Change (D18). |
| C18 | `TX_Telemetry.py:30-52` | H.264 video (not bench-verified) | Grok verifies stills with the same library | `Grok_Cam_Test.py`; transcript §10 | Keep video (D8); align lifecycle/folder; fail-soft. |
| C19 | `TX_Telemetry.py:20,50` | `/home/pi/Desktop/Camera/flight_%Y%m%d_%H%M%S.h264` | per-run folder, stamp `%Y-%m-%d_%H-%M-%S` | `Grok_Cam_Test.py` L7-9 | Change (D8). |
| C20 | `TX_Telemetry.py` (absent) | no BMP280 | BMP280 at 0x76 in the packet | U4; transcript §8, §11 | Add (D11, A1). |
| C21 | `TX_Telemetry.py` (absent) | no first-sample discard | first MPU and BMP280 samples invalid | transcript §6, §8 | Add (D12). |
| C22 | `TX_Telemetry.py:141-144` | `on_recv` does unguarded `MSG.decode('utf-8')` (an exception kills the lgpio callback thread that also services TX_DONE) | robust, ascii | transcript §1 rule; library source | Guard. |
| C23 | `RX_Temenetry.py:420`, `telemetry_decode.py:132`, `RX_Temenetry.py:106` | `.decode("utf-8", errors="replace")` | `"ascii", errors="replace"` | consistency | Change. |
| C24 | `RX_Temenetry.py:422-440` | `on_recv` catches only `TelemetryDecodeError` | broad guard | library source (callback thread has no try/except) | Wrap whole body. |
| C25 | `RX_Temenetry.py:24-25`, `README.md:102` | "Needs RPi.GPIO" | not needed | D7 | Update text. |
| C26 | `rocket-tx.service:4-5,28` | `After=multi-user.target` + `WantedBy=multi-user.target` (ordering cycle) | `After=local-fs.target` | systemd semantics | Change (D16). |
| C27 | `TX_Telemetry.py:12` | "ArduCam OV5647" | ov5647 Camera Module v1, 2592x1944 | transcript §10 | Comment. |
| C28 | `README.md:32,92` | `raspberrypi-zero.local` (TX) / `raspberrypi.local` (RX) | both are `raspberrypi.local` today | U6 | Use `raspberrypi.local` for both + collision warning + rename advice. |
| C29 | `TX_Telemetry.py` (ACCEL_CONFIG never written), `telemetry_decode.py:14-15,31`, `RX_Temenetry.py:42` | +/-2 g, 16384.0 | +/-16 g: `ACCEL_CONFIG = 0x18`, 2048.0 | U3 | Change TX + both decoders (packet semantics change). |
| C30 | `telemetry_decode.py:46`, `RX_Temenetry.py:52` | `NUM_SENSOR_FIELDS = 10` | `14` | U4 / A1 | Change both. |
| C31 | `telemetry_db.py:21-53`, `RX_Temenetry.py:163-186` | 33-column schema, no baro | + 4 baro columns with migration | U4 | Change both (D24). |
| C32 | `README.md:88-110` (RX section) | assumes a working Pi 3 with `pip3` | Pi 3 never set up; from-scratch guide; RX wiring unverified | U5 | Rewrite section. |
| C33 | `.kiro/steering/avionics-project.md` (Target platform, review §9 Q1) | "bench run on a Pi 3" | bench run on the Zero 2 W | U5 | Correct in the steering update (C.12). |

Verified CONSISTENT with the authority (no change needed): MPU-6050 `0x68`; HMC5883L `0x1e`, X, Z, Y register order (TX L105-107, L197-199; transcript §7); 9600 baud; SPI channel 1 / IRQ GPIO 5 / RST GPIO 25 / SPI port 0 on both ends; `ModemConfig.Bw125Cr45Sf128` both ends; 915 MHz effective; picamera2 as the camera library; RX node 1 accepts TX `send(..., 1)`; compass offsets Y 167.0 / Z 112.5 present in both decoders; gyro scale 16.4 for `GYRO_CONFIG = 24`.

---

## C. Per-file change list

### C.1 `System Scripts/TX_Telemetry.py` (TX, Zero 2 W) — rewrite in place, keep script style

Keep: module-level flow (no `main()`), register constant block and names, function names `read_MPU_data` / `read_MAG_data` / `poll_gps` / `start_camera`, 10 Hz deadline scheduler, once-per-second rate line, `try/finally` camera shutdown, comment style.

1. Header block (D22):
   ```python
   # Target board : Raspberry Pi Zero 2 W (rocket flight computer, TX side)
   # Interpreter  : /home/pi/libs-venv/bin/python3 (venv, include-system-site-packages = true)
   # Authority    : Grok_GPSTest.py, Grok_TX_test.py, Grok_Cam_Test.py, bench transcript, user decisions 2026-10-08
   ```
2. Imports: remove `RPi.GPIO`, `math`; add `sys`, `signal`, `from pathlib import Path`; `import smbus2 as smbus` (C8); keep `os`, `serial`, `pynmea2` (status line), `datetime`, `sleep`, `monotonic`, `LoRa, ModemConfig`. `from smbus2 import SMBus` / `from bmp280 import BMP280` and the picamera2 imports move inside their init functions (fail-soft on missing packages).
3. Constants with one-line source comments: `GPS_PORT = "/dev/serial0"`, `GPS_BAUD = 9600`; `I2C_BUS = 1`, `MPU_Address = 0x68`, `MAG_Address = 0x1e`, `BARO_Address = 0x76`; `ACCEL_CONFIG = 0x1C`, `ACCEL_RANGE_16G = 0x18`, `WHO_AM_I = 0x75`; `LORA_SPI_PORT = 0`, `LORA_SPI_CHANNEL = 1`, `LORA_IRQ_PIN = 5`, `LORA_RESET_PIN = 25`, `LORA_FREQ_MHZ = 915`, `LORA_TX_POWER_DBM = 15`, `TX_NODE_ADDRESS = 2`, `RX_NODE_ADDRESS = 1`; `SEA_LEVEL_HPA = 1013.25`; `FLIGHT_DIR_BASE = Path("/home/pi/Desktop")`; `FLIGHT_DURATION_S = float(os.environ.get("TX_FLIGHT_DURATION_S", 8 * 60))`. Remove `declination`, `pi`.
4. `signal.signal(signal.SIGTERM, _on_sigterm)` raising `SystemExit(0)` (D17).
5. GPS init (C1, D13): `serialPort = None` on `(OSError, serial.SerialException)`.
6. `poll_gps()` (C2, C3): return `last_gps_line` if `serialPort is None`; decode `ascii`/`ignore`; newest-first, accept only `text.startswith("$") and "GGA" in text`; reset `_gps_buffer` if it exceeds 4096 bytes; catch `(OSError, serial.SerialException)`.
7. I2C: `bus = None` on failure. `init_imu()`: writes `SMPLRT_DIV 7`, `PWR_MGMT_1 1`, `CONFIG 0`, `GYRO_CONFIG 24`, **`ACCEL_CONFIG 0x18` (U3, comment: "+/-16 g -> 2048 LSB/g; decoders in telemetry_decode.py AND RX_Temenetry.py must match")**, `INT_ENABLE 1`, each followed by `sleep(.2)`; `WHO_AM_I` check (warn if not `0x68`); `sleep(0.1)` + throwaway read (D12); returns `imu_ok`. `init_mag()`: `0x70`, `0xa0`, `0` -> `mag_ok`. Both `try/except OSError`.
8. `init_baro()` (D11): returns `(chip, pad_pressure_hpa)` or `(None, None)`; helper `pressure_to_altitude_m(p_hpa, t_c, qnh_hpa)` implementing the A1 formula; `read_baro()` returns `(t_c, p_hpa, alt_m, agl_m)` or `(None,)*4` on any exception (`baro_errors += 1`).
9. `read_MPU_data` / `read_MAG_data`: `>= 32768` (C11). Two-byte reads kept.
10. Radio (D4, D13): exact keyword call; on exception print `"LoRa init failed (check CS/MOSI/MISO/SCK wiring, not frequency): <err>"` and `sys.exit(1)`. `on_recv`: `try: print("RX:", payload.message.decode("ascii", errors="replace")) except Exception as e: print("on_recv error:", e)` (C22).
11. Camera (D8): `start_camera()` returns `(picam2, path)` or `(None, None)`; folder `FLIGHT_DIR_BASE / f"flight_{stamp}"`, `stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")`, file `flight_{stamp}.h264`; picamera2 imports inside; whole body guarded. Keep 720p/30/4 Mbps/180-degree transform. Comments updated (C14, C27). Started AFTER the radio.
12. Message (A1): `fmt = lambda v: "" if v is None else "{:.2f}".format(v)`; `message = ",".join(str(v) for v in (acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp, mag_x, mag_y, mag_z)) + "," + ",".join(fmt(v) for v in (baro_t, baro_p, baro_alt, baro_agl)) + "," + GPS_strg`. Sensor groups unavailable -> zeros / empties without touching the bus.
13. Loop guards (D13): per-group `try/except OSError` (zeros + `i2c_errors += 1`); `try: status = lora.send(message, RX_NODE_ADDRESS) except Exception: status = False`; `send_failures` counted. Status line once per second extended with `gps=<fix/nofix> sats=<n>` (via `pynmea2.parse(last_gps_line)` guarded), `baro=<hPa>hPa agl=<m>m` (or `n/a`), `i2c_err`, `tx_fail`. Comments for the 4-core board and ~4 Hz real ceiling (D14).
14. `finally:`: stop/close camera if present; no `GPIO.cleanup()`; one-line summary (packets sent, failures, video path).

### C.2 `System Scripts/rocket-tx.service` (TX)

- `Description=Rocket TX Telemetry on Raspberry Pi Zero 2 W (sensors + camera + LoRa transmit)`.
- `[Unit]`: drop `After=multi-user.target` / `Wants=multi-user.target`; add `After=local-fs.target` with a comment on the cycle (C26).
- `ExecStart=/home/pi/libs-venv/bin/python3 "/home/pi/Desktop/Avionics for Rocket/System Scripts/TX_Telemetry.py"` (C12); `WorkingDirectory` unchanged (lgpio writes `.lgd-nfy*` into CWD).
- `Environment=PYTHONUNBUFFERED=1`, `Environment=TX_FLIGHT_DURATION_S=480` (D15, D16).
- `Restart=always` (comment: relaunch after the 8-minute window; ~15 s gap; pad baseline re-zeroed on each start); `RestartSec=5`, `ExecStartPre=/bin/sleep 10`, journal lines unchanged.

### C.3 `System Scripts/install_tx_autostart.sh` (TX)

- Comments "Pi Zero" -> "Pi Zero 2 W" (C14).
- After the unit-file check: `PY_INTERP="$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' "$SRC_UNIT")"`; if not executable print a non-fatal `WARNING: $PY_INTERP not found; create the venv per README.md before starting the service`.
- After `daemon-reload`: `systemd-analyze verify "$DEST_UNIT" || true`; footer adds `journalctl -b | grep -i "ordering cycle"`.

### C.4 `System Scripts/telemetry_decode.py` (RX module; the packet contract reference)

- Header block (D22): `# Target board : Raspberry Pi 3 ground station (RX side; pure Python, no hardware access)` / `# Interpreter  : /home/pi/libs-venv/bin/python3 (see README.md, RX first-time setup)` / `# Packet source: TX_Telemetry.py on the Pi Zero 2 W (layout documented below; keep identical to the copy in RX_Temenetry.py)`.
- Docstring: the A1 layout table (14 fields + GPS), `$GNGGA`/`$GPGGA` talkers, +/-16 g wording (C29).
- Constants: `ACCEL_SCALE_MODIFIER_16G = 2048.0` (replace `_2G`), `DECLINATION = -0.113` with the Cocoa comment (C10), `NUM_SENSOR_FIELDS = 14` (C30), `NUM_INT_FIELDS = 10`.
- New `_to_float_or_none(text)`: `None` for empty/whitespace, else `float(text)`; `ValueError` -> `TelemetryDecodeError`.
- `decode()`: ints from `parts[:10]`, baro floats from `parts[10:14]`, `gps_sentence = ",".join(parts[14:])`; bytes -> `ascii`/`replace` (C23); result gains `"baro": {"temp_c", "press_hpa", "alt_m", "agl_m"}`.
- `_parse_gps`: `fix_quality`, `num_sats`, `gps_qual == 0` guard (D20).
- `format_readout`: `BARO :` line and ` sats N`.
- `__main__` self-test uses the A1 example packet (real bench GGA sentence from transcript §3).

### C.5 `System Scripts/RX_Temenetry.py` (RX; filename unchanged)

- Header block (D22); docstring: "Needs pyLoraRFM9x + pynmea2 (RPi.GPIO is not needed)"; run lines say `start_rx.sh` uses `/home/pi/libs-venv/bin/python3`.
- Remove unused `import os`.
- Inlined decoder: mirror C.4 exactly (constants, `_to_float_or_none`, 14 fields, baro dict, `_parse_gps` fields, `ascii`).
- Inlined DB: mirror C.6 exactly (schema + `_ensure_columns()` migration + `_INSERT` with baro columns).
- `LiveState`: `max_baro_agl`; `update()` tracks it when `data["baro"]["agl_m"]` is not `None`; `snapshot()` exposes it.
- Displays (D25): `_baro_line(baro)` helper; dashboard, GUI and scroll use it; MAX line adds `MAX baro AGL`; `_gps_line` adds ` sats N`.
- `main()`: delete `RPi.GPIO` import and `GPIO.cleanup()` (C9); `on_recv` body inside `try/except Exception` (decode errors counted, printed in `--scroll`; other exceptions -> `state.note_error()` + one print) (C24); `payload.message.decode("ascii", errors="replace")`; radio in keyword form `LoRa(spi_channel=1, interrupt_pin=5, my_address=args.node, spi_port=0, reset_pin=25, freq=915, tx_power=5, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False)` (D6; pins unchanged).
- CLI flags, display modes, dashboard layout otherwise unchanged.

### C.6 `System Scripts/telemetry_db.py` (RX module)

- Header block (D22).
- `SCHEMA`: add `baro_temp_c REAL, baro_press_hpa REAL, baro_alt_m REAL, baro_agl_m REAL` under a `-- barometer (BMP280)` comment, before the link-quality columns (D24).
- `BARO_COLUMNS = ("baro_temp_c", "baro_press_hpa", "baro_alt_m", "baro_agl_m")`; `_ensure_columns(self)` runs `PRAGMA table_info(telemetry)` and `ALTER TABLE telemetry ADD COLUMN <c> REAL` for each missing one; called from `__init__` after `executescript(SCHEMA)`.
- `_INSERT`: 4 more columns + placeholders; `insert()` passes `decoded["baro"]["temp_c"]`, `["press_hpa"]`, `["alt_m"]`, `["agl_m"]`.
- Docstring: mention the 2026-10-08 baro columns and that older DBs are migrated in place.

### C.7 `System Scripts/playback.py` (RX tool)

- Header block (D22); usage lines use `/home/pi/libs-venv/bin/python3`.
- `_print_row`: after `HEAD`, print `BARO : alt {:.1f} m  AGL {:+.1f} m  {:.1f} hPa  {:.1f} C` when `"baro_agl_m" in row.keys() and row["baro_agl_m"] is not None`.
- `replay()` summary line: add `max baro AGL` for the session (max over rows, ignoring `None`).

### C.8 `System Scripts/start_rx.sh` (RX launcher)

- `RX_PYTHON="/home/pi/libs-venv/bin/python3"`; existence check with the D18 error (then `read -r _` so the window stays open; exit 1); run `"$RX_PYTHON" RX_Temenetry.py`; comments updated.

### C.9 `System Scripts/make_rx_gui_shortcut.sh` (RX launcher generator)

- Same `RX_PYTHON` constant + check (exit 1 with the README pointer); `Exec=${RX_PYTHON} "${RX_SCRIPT}" --gui`; footer hint uses `"$RX_PYTHON"`.

### C.10 `System Scripts/Rocket-RX.desktop` (RX launcher)

- Keep `Exec=lxterminal ... start_rx.sh`; add the comment line `# start_rx.sh runs the receiver with /home/pi/libs-venv/bin/python3 (see README.md, RX first-time setup).` (D18).

### C.11 `System Scripts/README.md`

Rewrite; keep the structure (intro, Files table, TX side, RX side, Database) and add the sections below.

- Intro/Files table: "TX = Raspberry Pi Zero 2 W"; `Goes to` = `Pi Zero 2 W`; add `make_rx_gui_shortcut.sh` row (C14).
- "Hostnames" callout right after the intro (U6): both Pis are currently `raspberrypi.local`; two hosts with one mDNS name collide when both are on the network; rename the Pi 3 during its setup (`sudo raspi-config` -> System Options -> Hostname, e.g. `rocket-rx`) or use IP addresses / power on one Pi at a time; all scp examples use `pi@raspberrypi.local`.
- TX side: state the Zero 2 W is already fully set up (U5) and keep the recipe "for a rebuild": one-time OS setup (C15 UART steps, I2C/SPI via `raspi-config`, camera auto-detected via `rpicam-hello --list-cameras`), venv recipe (C13: apt list, `python3 -m venv ~/libs-venv`, `sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' ~/libs-venv/pyvenv.cfg`, `pip install pynmea2 pyserial smbus2 bmp280 pyLoraRFM9x`, why bare `pip3` fails), import check one-liner, Thonny interpreter path.
- TX service: venv interpreter, `Restart=always`, 8-minute window via `TX_FLIGHT_DURATION_S`, ~15 s relaunch gap, pad baseline re-zero on each start, `systemd-analyze verify`, `journalctl -b | grep -i "ordering cycle"` (C16, D15, D16).
- TX sensors table (bus/address/pins from transcript §6-9, §11) + RFM9x pin table; `tx_power` 15 dBm note (U2); accelerometer +/-16 g (U3); BMP280 in the packet, pad-zero behaviour, standby tuning note (D11); camera output folder and `ffmpeg -framerate 30 -i in.h264 -c copy out.mp4`; video path not bench-verified (D8).
- Troubleshooting table: binary GPS bytes; `Permission denied` on `/dev/ttyAMA0`; `AssertionError: LoRa initialization failed`; temperature exactly 36.53 C; `ModuleNotFoundError` in journalctl; "no fix" on RX (transcript §1-3, §6, §9).
- RX side (Pi 3) - first-time setup, from scratch (U5, C32): flash current Raspberry Pi OS (desktop) and enable SSH; `sudo raspi-config` -> Interface Options -> SPI enable (I2C/serial not needed on RX); rename hostname here (U6); `sudo apt update && sudo apt install -y swig python3-dev liblgpio-dev python3-tk`; `python3 -m venv ~/libs-venv` + the same `sed` for `include-system-site-packages = true` (so `tkinter` from apt is visible); `source ~/libs-venv/bin/activate && pip install pyLoraRFM9x pynmea2`; check `~/libs-venv/bin/python3 -c 'import pyLoraRFM9x, pynmea2, tkinter; print("ok")'`; scp the project; `chmod +x start_rx.sh make_rx_gui_shortcut.sh`; copy `Rocket-RX.desktop` to the Desktop; optional `./make_rx_gui_shortcut.sh`. RX RFM9x wiring: "assumed identical to the TX pinout (SPI0 CE1 = pin 26, G0 = GPIO 5 pin 29, RST = GPIO 25 pin 22, MOSI 19, MISO 21, SCK 23, 3.3 V pin 1, GND pin 6) but NOT verified on the Pi 3; `AssertionError: LoRa initialization failed` on first run means check CS/MOSI/MISO/SCK". Remove the RPi.GPIO sentence (C25). Run modes use `/home/pi/libs-venv/bin/python3`.
- "What changed on the RX in this update / RX redeploy" subsection = section D below.
- Database: schema gained 4 baro columns; existing DBs migrate automatically on first open; new `BARO` line in playback.

### C.12 `.kiro/steering/avionics-project.md` (project memory; append/correct only)

- Correct "Target platform" / "Hardware facts" wording: all bench tests ran on the Zero 2 W; the Pi 3 has never been set up (C33). Update the "Telemetry packet format" subsection to the A1 layout and the "SQLite schema" subsection to 37 columns. Mark the "Conflicts found" table rows as fixed 2026-10-08. Add "Decisions made in the System Scripts update (2026-10-08)" with D5, D8, D11, D13, D15, D16, D18, D20, D24 one-liners. Add a History entry: "System Scripts updated; RX REDEPLOY REQUIRED: YES (packet format, declination, schema migration, launchers); Pi 3 setup pending".

---

## D. RX redeploy summary (report to the user)

RX REDEPLOY REQUIRED: **YES** — and because the Pi 3 has never been set up, the RX side is a first-time install (README "RX side (Pi 3) - first-time setup"), then copy the updated `System Scripts/` and launch.

RX files changed and why:
- `RX_Temenetry.py`: packet layout 14 fields (+4 BMP280 floats), accel scale 2048.0 (+/-16 g), `DECLINATION -0.113`, `fix_quality`/`num_sats` + `gps_qual == 0` guard, baro on dashboard/GUI/scroll + `MAX baro AGL`, DB schema + migration, `acks=False` + explicit `freq=915` (same effective radio behaviour), `RPi.GPIO` removed, `on_recv` hardened, `ascii` decode.
- `telemetry_decode.py`: same decoder changes (14 fields, 2048.0, -0.113, baro dict, GPS fix fields, `ascii`), new self-test packet.
- `telemetry_db.py`: 4 new `baro_*` columns, `_ensure_columns()` migration, insert updated.
- `playback.py`: `BARO :` line, max baro AGL in the summary, header.
- `start_rx.sh`, `make_rx_gui_shortcut.sh`: fixed interpreter `/home/pi/libs-venv/bin/python3` with a clear error if missing.
- `Rocket-RX.desktop`: comment line only (still launches `start_rx.sh`).
- `README.md`: RX from-scratch setup, hostname warning, redeploy notes.

Not changed on the Pi 3: LoRa pins (CS CE1, IRQ GPIO 5, RST GPIO 25; assumed, unverified), frequency, ModemConfig, node address, CLI flags, display modes. Existing `RxTelemetry.db` files remain readable (old rows get `NULL` baro columns). Old 10-field TX packets are rejected by the new decoder, so deploy TX and RX together.

---

## E. Open items for the user (carry forward; none block implementation)

- Q7 Magnetometer X-axis offset was never characterised (heading uses X).
- Q9 Rename the Pi 3 hostname during its setup (U6); run the from-scratch RX setup; verify the RX RFM9x wiring against the TX pinout.
- Q10 BMP280 standby 62.5 ms (D11) is a tuning change from the bench default 500 ms; confirm on the bench that `baro=` values in `journalctl` update ~7x/s.
- Q11 Flight window: 8 min + relaunch (15 s gap and pad re-zero every 8 min) or run continuously until shutdown?

---

# Implementation Plan

Run every command from the Mac with the workspace root quoted. The Mac has Python 3.9.6 and none of the Pi libraries; do not install anything. `py_compile` is the syntax gate; the stub harnesses below (fake `serial`, `smbus2`, `bmp280`, `pyLoraRFM9x`, `pynmea2` modules injected into `sys.modules`, nothing written into the repo) are the behavioural gate. Keep all code Python-3.9-compatible so the Mac checks run. Shared stub for items 4-6 (save nothing; paste it at the top of each `python3 - <<'EOF'` block):

```python
import sys, types
class ParseError(Exception): pass
class _Msg:
    def __init__(self, s):
        f = s.split(",")
        self.timestamp, self.lat, self.lat_dir, self.lon, self.lon_dir = f[1], f[2], f[3], f[4], f[5]
        self.gps_qual, self.num_sats = int(f[6]), f[7]
        self.altitude, self.altitude_units = float(f[9]), f[10]
        self.latitude = (int(self.lat[:2]) + float(self.lat[2:]) / 60) * (1 if self.lat_dir == "N" else -1)
        self.longitude = (int(self.lon[:3]) + float(self.lon[3:]) / 60) * (1 if self.lon_dir == "E" else -1)
def parse(s, check=False):
    if not (s.startswith("$") and "GGA" in s): raise ParseError(s)
    return _Msg(s)
m = types.ModuleType("pynmea2"); m.parse = parse; m.ParseError = ParseError; sys.modules["pynmea2"] = m
PKT = "0,0,2048,0,0,0,-3552,100,-67,0,24.81,1011.73,13.21,0.00,$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E"
NOFIX = PKT.replace(",W,1,16,", ",W,0,00,")
NOBARO = PKT.replace("24.81,1011.73,13.21,0.00", ",,,")
```

- [ ] 1. Rewrite `System Scripts/TX_Telemetry.py` per section C.1 (A1 packet; D1-D5, D7, D8, D11-D15, D17, D22, D23; conflicts C1-C11, C14, C18-C22, C27, C29).
      Files: `System Scripts/TX_Telemetry.py`
      Verify (a): `cd "/Users/ajarretm/Desktop/Avionics for Rocket/System Scripts" && python3 -m py_compile TX_Telemetry.py && echo COMPILED`.
      Verify (b): run the harness below from `System Scripts` three times: plain; `FAIL=i2c,gps,baro`; `FAIL=lora`. Expected: runs 1 and 2 print `TX smoke OK`; run 1 asserts field 0 is `-32768` (sign fix), field 10 is `24.81`, field 13 is `0.00` (AGL at pad), field 14 starts with `$GNGGA`; run 2 asserts zeros for ints, four empty baro fields and an empty GPS field; run 3 exits non-zero with the "LoRa init failed" message. Camera reports "unavailable" on the Mac (no picamera2), which exercises that fail-soft path; the camera success path is Pi-only.
      ```bash
      python3 - <<'EOF'
      import os, sys, types, runpy
      os.environ["TX_FLIGHT_DURATION_S"] = "1.5"
      fail = set(filter(None, os.environ.get("FAIL", "").split(",")))
      sent = []
      def mod(name, **attrs):
          m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m
      class SerialException(Exception): pass
      class FakeSerial:
          def __init__(self, port, baud, timeout=0):
              assert (port, baud) == ("/dev/serial0", 9600), (port, baud)
              if "gps" in fail: raise OSError("no serial")
              self._d = (b"$GNZDA,145743.000,07,10,2026,,*5F\r\n"
                         b"$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E\r\n"
                         b"\xc0\xe3\x9c\x88SEE\r\n$GPTXT,01,01,01,ANTENNA OK*35\r\n")
          @property
          def in_waiting(self): return len(self._d)
          def read(self, n): d, self._d = self._d[:n], self._d[n:]; return d
      mod("serial", Serial=FakeSerial, SerialException=SerialException)
      written = {}
      class FakeBus:
          def __init__(self, n):
              if "i2c" in fail: raise OSError("no i2c")
          def write_byte_data(self, a, r, v): written[(a, r)] = v
          def read_byte_data(self, a, r):
              if a == 0x68 and r == 0x75: return 0x68
              return 0x80 if (a == 0x68 and r == 0x3B) else 0x00   # ACCEL_XOUT raw 0x8000 -> -32768
      mod("smbus2", SMBus=FakeBus)
      class FakeBMP:
          def __init__(self, i2c_dev=None, i2c_addr=None):
              assert i2c_addr == 0x76
              if "baro" in fail: raise OSError("no bmp280")
              self.calls = 0
          def setup(self, mode="normal", temperature_oversampling=16, pressure_oversampling=16, temperature_standby=500):
              assert temperature_standby in (0.5, 62.5, 125, 250, 500, 1000, 2000, 4000)
          def get_temperature(self): return 24.81
          def get_pressure(self):
              self.calls += 1
              return 724.0 if self.calls == 1 else 1011.73      # first conversion is garbage
      mod("bmp280", BMP280=FakeBMP)
      class ModemConfig: Bw125Cr45Sf128 = "Bw125Cr45Sf128"
      class FakeLoRa:
          def __init__(self, **kw):
              if "lora" in fail: raise AssertionError("LoRa initialization failed")
              assert kw == dict(spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0, my_address=2,
                                freq=915, tx_power=15, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False), kw
          def send(self, msg, to): sent.append((msg, to)); return True
      mod("pyLoraRFM9x", LoRa=FakeLoRa, ModemConfig=ModemConfig)
      class ParseError(Exception): pass
      class _Msg:
          def __init__(self, s):
              f = s.split(","); self.gps_qual = int(f[6]); self.num_sats = f[7]
      def parse(s, check=False):
          if not (s.startswith("$") and "GGA" in s): raise ParseError(s)
          return _Msg(s)
      mod("pynmea2", parse=parse, ParseError=ParseError)
      runpy.run_path("TX_Telemetry.py", run_name="__main__")
      assert sent, "no packets sent"
      msg, to = sent[-1]; f = msg.split(",")
      assert to == 1 and len(f) >= 14, (to, msg)
      if "i2c" not in fail: assert written.get((0x68, 0x1C)) == 0x18, "ACCEL_CONFIG must be 0x18"
      assert f[0] == ("0" if "i2c" in fail else "-32768"), f[0]
      if "baro" in fail: assert f[10:14] == ["", "", "", ""], f[10:14]
      else: assert f[10] == "24.81" and f[11] == "1011.73" and f[13] == "0.00" and 10 < float(f[12]) < 20, f[10:14]
      if "gps" in fail: assert f[14:] == [""], msg
      else: assert f[14].startswith("$GNGGA"), msg
      print("TX smoke OK:", len(sent), "packets; last:", msg[:80])
      EOF
      ```

- [ ] 2. Update `System Scripts/rocket-tx.service` per section C.2 (C12, C26, D15, D16).
      Files: `System Scripts/rocket-tx.service`
      Verify: from `System Scripts`, `python3 - <<'EOF'` with: `import configparser; c = configparser.ConfigParser(strict=False, interpolation=None); c.optionxform = str; c.read("rocket-tx.service"); assert c["Service"]["ExecStart"].startswith('/home/pi/libs-venv/bin/python3 "/home/pi/Desktop/Avionics for Rocket/System Scripts/TX_Telemetry.py"'); assert c["Service"]["Restart"] == "always"; assert c["Service"]["WorkingDirectory"] == "/home/pi/Desktop/Avionics for Rocket/System Scripts"; assert "multi-user.target" not in c["Unit"].get("After", "") and "Wants" not in c["Unit"]; assert c["Install"]["WantedBy"] == "multi-user.target"; print("unit OK")` prints `unit OK`. (Note: configparser keeps only the last `Environment=` line; that is fine for this check. The real check is `systemd-analyze verify` on the Pi, added to the installer in item 3.)

- [ ] 3. Update `System Scripts/install_tx_autostart.sh` per section C.3.
      Files: `System Scripts/install_tx_autostart.sh`
      Verify: `bash -n install_tx_autostart.sh && echo SYNTAX_OK`; then `bash install_tx_autostart.sh; echo "exit=$?"` on the Mac (no sudo) prints the "run with sudo" error and `exit=1` (the guard still works; nothing installed).

- [ ] 4. Update `System Scripts/telemetry_decode.py` per section C.4 (A1, C10, C23, C29, C30, D20, D22, D23).
      Files: `System Scripts/telemetry_decode.py`
      Verify: `python3 -m py_compile telemetry_decode.py`; then from `System Scripts`, shared stub + :
      ```python
      import telemetry_decode as td
      d = td.decode(PKT)
      assert td.NUM_SENSOR_FIELDS == 14 and abs(td.DECLINATION + 0.113) < 1e-9 and td.ACCEL_SCALE_MODIFIER_16G == 2048.0
      assert abs(d["accel_g"]["z"] - 1.0) < 1e-9 and abs(d["temp_c"] - 26.08) < 0.01
      assert d["heading_deg"] == 38, d["heading_deg"]          # atan2(100, 100) = 45 deg - 6.47 deg
      assert d["baro"] == {"temp_c": 24.81, "press_hpa": 1011.73, "alt_m": 13.21, "agl_m": 0.0}
      assert abs(d["gps"]["lat"] - 28.320827) < 1e-5 and abs(d["gps"]["lon"] + 80.741626) < 1e-5
      assert d["gps"]["altitude"] == 6.2 and d["gps"]["num_sats"] == 16 and d["gps"]["fix_quality"] == 1
      n = td.decode(NOFIX); assert n["gps"]["lat"] is None and n["gps"]["fix_quality"] == 0
      b = td.decode(NOBARO); assert all(v is None for v in b["baro"].values()) and b["gps"]["lat"] is not None
      assert td.decode(PKT.encode("ascii"))["heading_deg"] == 38
      try: td.decode("1,2,3,4,5,6,7,8,9,10,$GNGGA,x"); raise SystemExit("old 10-field packet must be rejected")
      except td.TelemetryDecodeError: pass
      out = td.format_readout(d); assert "BARO" in out and "sats 16" in out, out
      print("decode OK")
      ```
      prints exactly `decode OK`. Also the module self-test, run with the stub prepended (`import runpy; runpy.run_path("telemetry_decode.py", run_name="__main__")` appended to the stub block), must print a readout containing `HEAD : 38 deg` and a `BARO :` line.

- [ ] 5. Update `System Scripts/RX_Temenetry.py` per section C.5 (A1, C9, C10, C23-C25, C29-C31, D6, D7, D20, D22-D25). Depends on items 4 and 6 (mirror their decoder/DB edits exactly).
      Files: `System Scripts/RX_Temenetry.py`
      Verify: `python3 -m py_compile RX_Temenetry.py`; shared stub + :
      ```python
      import contextlib, io, telemetry_decode as td, RX_Temenetry as rx
      for p in (PKT, NOFIX, NOBARO): assert td.decode(p) == rx.decode(p), p
      assert rx.NUM_SENSOR_FIELDS == 14 and rx.ACCEL_SCALE_MODIFIER_16G == 2048.0 and abs(rx.DECLINATION + 0.113) < 1e-9
      d = rx.decode(PKT); assert "sats 16" in rx._gps_line(d["gps"]) and "1011.7" in rx._baro_line(d["baro"])
      s = rx.LiveState(); s.update(d, -60, 9.5); s.update(rx.decode(PKT.replace("13.21,0.00", "113.21,100.00")), -61, 9.0)
      snap = s.snapshot(); assert snap["max_baro_agl"] == 100.0 and snap["count"] == 2
      text = rx.render_dashboard(snap, "flight_test"); assert "BARO" in text and "MAX baro AGL" in text and "sats 16" in text
      buf = io.StringIO()
      with contextlib.redirect_stdout(buf):
          try: rx.main(["--help"])
          except SystemExit as e: assert e.code == 0
      assert "--gui" in buf.getvalue() and "--scroll" in buf.getvalue() and "--node" in buf.getvalue()
      print("copies agree; dashboard OK; --help OK")
      ```
      prints exactly that line. Also `grep -c "RPi.GPIO" RX_Temenetry.py` prints `0`.

- [ ] 6. Update `System Scripts/telemetry_db.py` and `System Scripts/playback.py` per C.6/C.7 (D24, D25, D22). Do this BEFORE item 5 so the RX inline DB copy can mirror it (order: 4, 6, 5 is fine; the list is numbered by file).
      Files: `System Scripts/telemetry_db.py`, `System Scripts/playback.py`
      Verify: `python3 -m py_compile telemetry_db.py playback.py`; shared stub + migration test:
      ```python
      import importlib.util, os, telemetry_decode as td, telemetry_db
      DB = "/tmp/rocket_plan_check.db"
      if os.path.exists(DB): os.remove(DB)
      spec = importlib.util.spec_from_file_location("old_db", "/Users/ajarretm/Desktop/Avionics for Rocket/.agents/backup/System Scripts-2026-10-08/telemetry_db.py")
      old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
      old.TelemetryDB(DB, session_id="flight_old").close()                 # 33-column schema from the backup
      db = telemetry_db.TelemetryDB(DB, session_id="flight_test")          # must migrate in place
      cols = {r[1] for r in db.conn.execute("PRAGMA table_info(telemetry)")}
      assert {"baro_temp_c", "baro_press_hpa", "baro_alt_m", "baro_agl_m"} <= cols, cols
      db.insert(td.decode(PKT), rssi=-60, snr=9.5); db.insert(td.decode(NOBARO), rssi=-61, snr=9.0)
      rows = list(db.rows_for_session("flight_test"))
      assert len(rows) == 2 and rows[0]["heading_deg"] == 38 and rows[0]["baro_agl_m"] == 0.0 and rows[1]["baro_agl_m"] is None
      assert abs(rows[0]["gps_lat"] - 28.320827) < 1e-5 and rows[0]["accel_g_z"] == 1.0
      db.close(); telemetry_db.TelemetryDB(DB).close()                     # second open: migration idempotent
      print("db OK")
      ```
      then `python3 playback.py --db /tmp/rocket_plan_check.db --list` lists `flight_test` with 2 packets; `python3 playback.py --db /tmp/rocket_plan_check.db --session flight_test --speed 0` prints two packet blocks, the first containing `BARO :` and the second without it, ending with `End of session 'flight_test'.`; finally `rm /tmp/rocket_plan_check.db`. (playback imports `telemetry_db` only, so no stub is needed for it.)

- [ ] 7. Update `System Scripts/start_rx.sh`, `System Scripts/make_rx_gui_shortcut.sh` and `System Scripts/Rocket-RX.desktop` per C.8-C.10 (D18, C17).
      Files: `System Scripts/start_rx.sh`, `System Scripts/make_rx_gui_shortcut.sh`, `System Scripts/Rocket-RX.desktop`
      Verify: `bash -n start_rx.sh && bash -n make_rx_gui_shortcut.sh && echo SYNTAX_OK`; `bash make_rx_gui_shortcut.sh; echo "exit=$?"` on the Mac prints the "not found ... README.md" error and `exit=1` (no venv here); `printf '' | bash start_rx.sh; echo "exit=$?"` prints the same error and `exit=1`; `python3 -c "import configparser; c=configparser.ConfigParser(interpolation=None); c.optionxform=str; c.read('Rocket-RX.desktop'); e=c['Desktop Entry']['Exec']; assert e.startswith('lxterminal') and e.endswith('start_rx.sh\"'), e; print('desktop OK')"` prints `desktop OK`; `grep -c "libs-venv/bin/python3" start_rx.sh make_rx_gui_shortcut.sh Rocket-RX.desktop` shows >= 1 for each file.

- [ ] 8. Rewrite `System Scripts/README.md` per section C.11 (C13-C16, C25, C28, C32, D8, D11, D15, D16, D18, D21, section D). Depends on items 1-7.
      Files: `System Scripts/README.md`
      Verify: stale-string sweep, each must print `0`: `grep -c "pip3 install" README.md; grep -c "/usr/bin/python3" README.md; grep -c "fixed number of times" README.md; grep -c "python3-smbus" README.md; grep -c "raspberrypi-zero.local" README.md; grep -cE "Raspberry Pi Zero( |$)[^2]" README.md; grep -c "RPi.GPIO ships" README.md`. Required-string sweep, each >= 1: `libs-venv`, `disable-bt`, `enable_uart=1`, `serial-getty@ttyAMA0`, `liblgpio-dev`, `include-system-site-packages`, `console=serial0,115200`, `Restart=always`, `python3-tk`, `rocket-rx`, `raspi-config`, `mDNS`, `unverified` (or `NOT verified`), `2048`, `16 g`, `BMP280`, `baro_agl_m`, `RX redeploy`, `make_rx_gui_shortcut.sh`, `ffmpeg`. Then read the file once top to bottom against C.11 (sweeps only catch regressions).

- [ ] 9. Final sweep and project-memory update per C.12.
      Files: `.kiro/steering/avionics-project.md` (append/correct only); `System Scripts/` only if the sweep fails.
      Verify: from `System Scripts`: `python3 -m py_compile TX_Telemetry.py RX_Temenetry.py telemetry_decode.py telemetry_db.py playback.py && bash -n start_rx.sh install_tx_autostart.sh make_rx_gui_shortcut.sh && echo ALL_OK`; `grep -n "utf-8" *.py` prints nothing; `grep -rln "RPi.GPIO\|ttyS0\|acks=True\|0\.00669\|16384\|NUM_SENSOR_FIELDS = 10" .` prints nothing; `ls` shows exactly the same 11 filenames as `.agents/backup/System Scripts-2026-10-08/` (no renames, no new files; `rm -rf __pycache__`); `diff <(ls) <(ls "../.agents/backup/System Scripts-2026-10-08")` prints nothing; re-run the item-1 harness (plain) and the item-5 check one last time. Then apply C.12 to the steering file and read its tail to confirm it is well-formed markdown.

Deployment after implementation (user-run on the Pis, not part of this workflow): TX: `scp -r "/Users/ajarretm/Desktop/Avionics for Rocket" pi@raspberrypi.local:/home/pi/Desktop/` (TX only powered on), then `cd "/home/pi/Desktop/Avionics for Rocket/System Scripts" && sudo ./install_tx_autostart.sh && sudo systemctl restart rocket-tx.service && journalctl -u rocket-tx.service -f`. RX: perform the README "RX side (Pi 3) - first-time setup" (rename hostname, SPI, venv, pip), scp the project, launch the desktop icon. **RX REDEPLOY REQUIRED: YES** (first-time install).
