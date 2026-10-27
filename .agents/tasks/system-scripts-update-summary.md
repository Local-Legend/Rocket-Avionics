# System Scripts update - change summary

Date: 2026-10-08
Workflow: `wf_e93a05d6a7ea64f7`, step `implement` (first iteration; no `review.json` existed)
Plan followed: `.agents/tasks/system-scripts-update-plan.md`
Backup of the pre-update folder (untouched): `.agents/backup/System Scripts-2026-10-08/`
Authority (untouched): `Grok_Cam_Test.py`, `Grok_GPSTest.py`, `Grok_TX_test.py`,
`.agents/tasks/grok-conversation.txt`, `.kiro/steering/avionics-project.md`

All 11 files in `System Scripts/` were edited in place. No file was renamed,
added or removed (`RX_Temenetry.py` keeps its intentional misspelling).

---

## RX REDEPLOY REQUIRED: YES

The packet format, the decoder constants and the database schema changed, so
the TX and RX must be deployed together. A new RX rejects old 10-field packets
(`TelemetryDecodeError`); an old RX would mis-scale the new accelerometer
values (16384 vs 2048 LSB/g) and parse the BMP280 floats as GPS text.

Because the Pi 3 has never been set up, the RX "redeploy" is in practice a
first-time install (README section "RX side (Pi 3) - first-time setup").

RX files that changed and why:

| File | Why it changed |
|---|---|
| `RX_Temenetry.py` | Packet contract: 14 fields (+4 BMP280 floats); accel scale 2048.0 (+/-16 g); `DECLINATION = -0.113`; GPS `fix_quality`/`num_sats` + `gps_qual == 0` guard; BARO line + `MAX baro AGL` on dashboard, GUI and scroll; DB schema + in-place migration; radio call rewritten in keyword form with explicit `freq=915` and `acks=False` (same pins, same effective behaviour, matches TX); `RPi.GPIO` import and `GPIO.cleanup()` removed; `on_recv` wrapped in a broad `try/except`; ascii decode |
| `telemetry_decode.py` | Same decoder changes (reference copy of the contract); new `_to_float_or_none`; `baro` dict; GPS fix fields; `format_readout` BARO line + sats; self-test uses the real bench GGA sentence |
| `telemetry_db.py` | 4 new `baro_*` REAL columns, `BARO_COLUMNS` + `_ensure_columns()` migration, `_INSERT` updated |
| `playback.py` | `BARO :` line when `baro_agl_m` is not NULL; session max baro AGL; header + venv usage lines |
| `start_rx.sh` | Runs `/home/pi/libs-venv/bin/python3` (fixed path); clear error + wait for Enter + exit 1 if the venv is missing |
| `make_rx_gui_shortcut.sh` | Same venv path in the generated `Exec=`; same error/exit 1 guard |
| `Rocket-RX.desktop` | One comment line only (still launches `start_rx.sh` in lxterminal) |
| `README.md` | RX from-scratch setup, hostname collision warning, redeploy notes |

NOT changed on the RX: LoRa pins (CS CE1, IRQ GPIO 5, RST GPIO 25), frequency
(915), `ModemConfig.Bw125Cr45Sf128`, node address (`--node` default 1), CLI
flags, display modes, dashboard layout apart from the added BARO/MAX lines.
Existing `RxTelemetry.db` files keep working (migrated on open; old rows read
as NULL in the baro columns).

---

## Per-file changes

### TX side (Raspberry Pi Zero 2 W)

**`TX_Telemetry.py`** (rewritten in place, module-level flow and function names kept)
- 3-line header (target board, interpreter, authority).
- Imports: `RPi.GPIO` and `math` removed; `sys`, `signal`, `pathlib.Path` added; `import smbus2 as smbus`. `bmp280`/`smbus2.SMBus` and `picamera2` imports moved inside their init functions (fail-soft on a missing package).
- Constants block with one-line sources: `GPS_PORT = "/dev/serial0"`, `GPS_BAUD = 9600`, `I2C_BUS = 1`, `MPU_Address = 0x68`, `MAG_Address = 0x1e`, `BARO_Address = 0x76`, `ACCEL_CONFIG = 0x1C`, `ACCEL_RANGE_16G = 0x18`, `WHO_AM_I = 0x75`, `LORA_SPI_PORT = 0`, `LORA_SPI_CHANNEL = 1`, `LORA_IRQ_PIN = 5`, `LORA_RESET_PIN = 25`, `LORA_FREQ_MHZ = 915`, `LORA_TX_POWER_DBM = 15` (user override, Grok bench value 5 noted), `TX_NODE_ADDRESS = 2`, `RX_NODE_ADDRESS = 1`, `SEA_LEVEL_HPA = 1013.25`, `FLIGHT_DIR_BASE = Path("/home/pi/Desktop")`, `FLIGHT_DURATION_S = float(os.environ.get("TX_FLIGHT_DURATION_S", 480))`. Dead `declination` and `pi` deleted.
- `SIGTERM` handler raises `SystemExit(0)` so `systemctl stop` runs `finally:` and finalises the video.
- GPS: `/dev/serial0` (was `/dev/ttyS0`); init fail-soft (`serialPort = None`). `poll_gps()` decodes `ascii`/`ignore` (was `utf-8`), accepts only lines starting with `$` AND containing `GGA` (was any sentence type, which made the RX show "no fix" most of the time), newest first, buffer reset if >4096 bytes without a newline, catches `OSError`/`SerialException`.
- I2C: bus fail-soft. `init_imu()` writes SMPLRT_DIV/PWR_MGMT_1/CONFIG/GYRO_CONFIG(24)/**ACCEL_CONFIG = 0x18 (+/-16 g, new)**/INT_ENABLE, checks WHO_AM_I, discards one full first sample. `init_mag()` unchanged registers (0x70, 0xa0, 0), X/Z/Y read order kept. Both return ok flags.
- Sign fix `>= 32768` in `read_MPU_data` and `read_MAG_data` (0x8000 now -> -32768).
- BMP280 added: `init_baro()` (`BMP280(i2c_dev=SMBus(1), i2c_addr=0x76)`, `setup(... temperature_standby=62.5)`, sleep 1 s, discard first conversion, pad baseline = mean of 10 readings), `pressure_to_altitude_m()` (Pimoroni formula), `read_baro()` -> `(temp_c, press_hpa, alt_m, agl_m)` or four `None`.
- Radio: keyword-form `LoRa(spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0, my_address=2, freq=915, tx_power=15, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False)` (was positional, `acks=True`, no `freq`, `tx_power=5`). Init failure prints the wiring hint and `sys.exit(1)` (the one hard failure). `on_recv` guarded, ascii decode.
- Camera: `start_camera()` fully guarded, returns `(None, None)` on failure; per-run folder `/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/flight_<stamp>.h264` (Grok_Cam_Test.py convention; was `/home/pi/Desktop/Camera/flight_YYYYmmdd_HHMMSS.h264`); 720p/30/4 Mbps/180-degree kept; started after the radio.
- Packet: 10 ints + 4 `%.2f` baro fields (empty when unavailable) + GGA sentence. Unavailable int sensors send `0` without touching the bus.
- Loop: per-group `try/except OSError` (zeros + `i2c_errors`), `lora.send` guarded (`send_failures`), status line once per second now adds `gps=fix sats=N | nofix`, `baro=<hPa> agl=<m>` or `n/a`, `i2c_err`, `tx_fail`. `time.monotonic()` deadline scheduler kept (comments updated for the 4-core board and the ~4 Hz air-time ceiling).
- `finally:` stops the camera only if it started; `GPIO.cleanup()` removed; one-line run summary.

**`rocket-tx.service`**
- `ExecStart=/home/pi/libs-venv/bin/python3 "..."` (was the system python3, which cannot import the venv packages).
- `After=multi-user.target` + `Wants=multi-user.target` replaced by `After=local-fs.target` (removes the ordering cycle with `WantedBy=multi-user.target`).
- `Restart=always` (was `on-failure`, which never relaunched after the clean 8-minute exit).
- `Environment=PYTHONUNBUFFERED=1`, `Environment=TX_FLIGHT_DURATION_S=480`.
- Description mentions the Zero 2 W; `WorkingDirectory`, `ExecStartPre=/bin/sleep 10`, `RestartSec=5`, journal lines unchanged.

**`install_tx_autostart.sh`**
- Wording: Pi Zero 2 W.
- Non-fatal warning if the `ExecStart` interpreter parsed from the unit is not executable.
- `systemd-analyze verify "$DEST_UNIT" || true` after `daemon-reload`.
- Footer adds the `journalctl -b | grep -i "ordering cycle"` check.

### RX side (Raspberry Pi 3)

See the RX table above. Additional detail:
- `telemetry_decode.py` / `RX_Temenetry.py` decoder copies are byte-for-byte equivalent in behaviour (verified: `decode()` output identical for fix / no-fix / no-baro packets); constants `ACCEL_SCALE_MODIFIER_16G = 2048.0`, `DECLINATION = -0.113`, `NUM_INT_FIELDS = 10`, `NUM_SENSOR_FIELDS = 14`.
- `telemetry_db.py` / RX inline DB: identical 37-column schema (verified by `PRAGMA table_info` comparison), identical migration.
- `LiveState` gains `max_baro_agl`; `_baro_line()` and `_fmt_max_agl()` helpers; `_gps_line()` appends `sats N`.
- `RX_Temenetry.py` radio: `LoRa(spi_channel=1, interrupt_pin=5, my_address=args.node, spi_port=0, reset_pin=25, freq=915, tx_power=5, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False)`.

### `README.md`
Rewritten (same section structure plus new ones): both boards named correctly; venv interpreter everywhere; hostname/mDNS callout; TX one-time setup (UART `enable_uart=1` + `dtoverlay=disable-bt`, `console=serial0,115200` removal, `serial-getty@ttyAMA0` stop/disable; apt + venv + `include-system-site-packages = true` + pip list; import check; Thonny path); service behaviour (`Restart=always`, 8-minute window via `TX_FLIGHT_DURATION_S`, 15 s gap, pad re-zero); TX hardware + RFM9x pin tables; radio params; +/-16 g, BMP280, GPS, camera notes with `ffmpeg` conversion and the "video not bench-verified" caveat; troubleshooting table; RX from-scratch setup (OS, SPI, hostname `rocket-rx`, apt incl. `python3-tk`, venv, pip, scp, RFM9x wiring "unverified on the Pi 3", launchers, run modes, playback); "What changed on the RX in this update (RX redeploy)"; Database section with the 4 new columns and auto-migration.

### Project memory
`.kiro/steering/avionics-project.md`: packet-format section replaced with the 14-field layout; SQLite section updated to 37 columns + migration; conflicts table marked fixed; "Decisions made in the System Scripts update" block added (D5, D8, D11, D13, D15, D16, D18, D20, D24); open decisions refreshed; history entry added.

---

## Verification performed (Mac, static + mocked-hardware; nothing installed)

Run from `"/Users/ajarretm/Desktop/Avionics for Rocket/System Scripts"`:

| Check | Command | Result |
|---|---|---|
| Syntax, every .py | `python3 -m py_compile RX_Temenetry.py TX_Telemetry.py playback.py telemetry_db.py telemetry_decode.py` | OK x5 |
| Syntax, every .sh | `bash -n install_tx_autostart.sh make_rx_gui_shortcut.sh start_rx.sh` | OK x3 |
| TX smoke (plan item 1 harness, fake serial/smbus2/bmp280/pyLoraRFM9x/pynmea2) plain | `python3 /tmp/tx_harness.py` | `TX smoke OK: 15 packets`; field 0 = -32768 (sign fix), ACCEL_CONFIG written 0x18, baro `24.81,1011.73,13.09,0.00`, field 14 `$GNGGA...`; LoRa kwargs asserted exact |
| TX smoke `FAIL=i2c,gps,baro` | same, with env | `TX smoke OK`; zeros, four empty baro fields, empty GPS field; loop survived |
| TX smoke `FAIL=lora` | same, with env | prints `LoRa init failed (check CS/MOSI/MISO/SCK wiring, not frequency)`, exit 1 |
| Unit file | configparser assertions (ExecStart venv path, `Restart=always`, WorkingDirectory, no `multi-user.target` in After/Wants, WantedBy) | `unit OK` |
| Installer guard | `bash install_tx_autostart.sh` (no sudo) | "run with sudo" error, exit 1, nothing installed |
| Decoder | plan item 4 assertions (constants, g/temp/heading 38 deg, baro dict, lat/lon, fix fields, no-fix, no-baro, bytes input, 10-field rejection, `format_readout` BARO + sats) | `decode OK`; self-test prints `HEAD : 38 deg` and a `BARO :` line |
| DB migration | backup 33-column schema -> new module opens it, adds 4 columns, inserts fix + no-baro rows, re-opens idempotently | `db OK` |
| Playback | `--list` and `--session flight_test --speed 0` against the migrated temp DB | lists 2 packets; first block has `BARO :`, second has none; ends `End of session 'flight_test'.` |
| RX copies | `telemetry_decode.decode == RX_Temenetry.decode` for 3 packets; constants; `_gps_line` sats; `_baro_line`; `LiveState.max_baro_agl`; dashboard contains `BARO`, `MAX baro AGL`, `sats 16`; `--help` lists `--gui --scroll --node`; inline DB schema == module schema (37 cols), migration + insert via RX copy readable by the module | `copies agree; dashboard OK; --help OK` |
| Launchers | `bash make_rx_gui_shortcut.sh` and `printf '' \| bash start_rx.sh` on the Mac | both print the "not found ... README.md" error, exit 1, no shortcut written; `.desktop` Exec still `lxterminal ... start_rx.sh` |
| Stale tokens | `grep -rln "RPi.GPIO\|ttyS0\|acks=True\|0\.00669\|16384\|NUM_SENSOR_FIELDS = 10\|utf-8\|/usr/bin/python3" .` | empty |
| README sweeps | 7 stale strings all 0; 20 required strings all >= 1 | pass |
| Filenames | `diff <(ls) <(ls "../.agents/backup/System Scripts-2026-10-08")` | identical 11 names; `__pycache__` removed |

Temporary files used: `/tmp/tx_harness.py`, `/tmp/rx_stub.py`, `/tmp/rocket_plan_check.db`, `/tmp/rocket_rx_check.db*` (DBs deleted; harness files are outside the repo).

Not verifiable on the Mac (needs the hardware): real picamera2 video path, real pyLoraRFM9x init and air time, real BMP280 update rate at 62.5 ms standby, systemd behaviour (`systemd-analyze verify`, restart timing), lgpio `.lgd-nfy*` behaviour, Tkinter GUI rendering.

---

## Open questions for the user

1. Hostnames: both Pis are `raspberrypi.local`. Rename the Pi 3 (`sudo raspi-config` -> System Options -> Hostname -> `rocket-rx`) during its setup, or always power on one Pi at a time / use IPs when copying files.
2. RX RFM9x wiring on the Pi 3 is assumed identical to the TX pinout but has never been checked; the first run will tell (`AssertionError: LoRa initialization failed` = CS/MOSI/MISO/SCK).
3. BMP280 standby was set to 62.5 ms (~7 Hz) instead of the bench default 500 ms so AGL can track the rocket. Please confirm on the bench that `baro=` in `journalctl -u rocket-tx.service -f` changes several times per second; if it misbehaves, set `temperature_standby=500` in `init_baro()`.
4. Flight window: the service now relaunches every 8 minutes (`Restart=always`, ~15 s gap, pad baseline re-zeroed and a new video file on each restart). If you prefer one continuous run, raise `TX_FLIGHT_DURATION_S` in `rocket-tx.service` (e.g. 7200) and re-run the installer.
5. The H.264 video path is not bench-verified (only stills were, via `Grok_Cam_Test.py`). A 30-second service run on the bench followed by `ffmpeg -framerate 30 -i flight_....h264 -c copy test.mp4` will confirm it.
6. Magnetometer X-axis offset was never characterised (heading uses X); the Y/Z offsets from `compass calibration.txt` are applied as before.
7. There is still no local telemetry log on the TX (only the LoRa stream and the journal status line). Say if you want one added.

---

## What to do on the Pi next

TX (Pi Zero 2 W), with the RX powered off or using the TX's IP:
1. `scp -r "/Users/ajarretm/Desktop/Avionics for Rocket" pi@raspberrypi.local:/home/pi/Desktop/`
2. `ssh pi@raspberrypi.local`, then `cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"`
3. `/home/pi/libs-venv/bin/python3 -c 'import serial, pynmea2, smbus2, bmp280, pyLoraRFM9x; from picamera2 import Picamera2; print("ok")'`
4. `chmod +x install_tx_autostart.sh && sudo ./install_tx_autostart.sh` (watch for the `systemd-analyze verify` output)
5. `sudo systemctl restart rocket-tx.service && journalctl -u rocket-tx.service -f` -> expect `BMP280 pad baseline: ...`, `Camera recording locally to: ...`, then once-per-second `t=... gps=fix sats=N baro=...` lines; no `unavailable` lines.
6. `journalctl -b | grep -i "ordering cycle"` must print nothing.
7. After a short run: `sudo systemctl stop rocket-tx.service` and check the `.h264` under `/home/pi/Desktop/flight_*/` plays (`ffmpeg ... -c copy`).

RX (Pi 3), first-time install:
1. Follow README "RX side (Pi 3) - first-time setup" steps 1-2 (flash OS, enable SPI, rename to `rocket-rx`, apt `swig python3-dev liblgpio-dev python3-tk`, venv, `pip install pyLoraRFM9x pynmea2`, import check).
2. Wire the RFM9x per the TX pin table; attach the antenna.
3. `scp -r` the project (step 3), `chmod +x start_rx.sh make_rx_gui_shortcut.sh`, copy `Rocket-RX.desktop` to the Desktop, optionally `./make_rx_gui_shortcut.sh`.
4. Double-click the icon with the TX running: the dashboard must show ACCEL z ~ +1.000 g on the pad, a BARO line with AGL ~ +0.0 m, HEADING, and GPS with `sats N` once the TX has a fix.
5. After the test: `/home/pi/libs-venv/bin/python3 playback.py --list` and replay the session.
