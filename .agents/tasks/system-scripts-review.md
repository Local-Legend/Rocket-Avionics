# System Scripts brought to the Grok_* bench authority and the Zero 2 W venv environment

Independent review, 2026-10-08. Workflow `wf_e93a05d6a7ea64f7`, step `review` (first pass).
Method: `diff -u` of every file in `System Scripts/` against
`.agents/backup/System Scripts-2026-10-08/`, read against `Grok_Cam_Test.py`,
`Grok_GPSTest.py`, `Grok_TX_test.py`, `.agents/tasks/grok-conversation.txt`, the
plan (`system-scripts-update-plan.md`) and the coder's summary
(`system-scripts-update-summary.md`). Duplicated decoder/DB code was compared
structurally (AST, docstrings stripped) rather than by eye.

The change rewrites the flight transmitter so that every hardware constant comes
from the bench-tested `Grok_*` scripts and transcript (`/dev/serial0`, ASCII GPS
decode, GGA-only filter, `smbus2`, keyword-form `LoRa(...)` with `acks=False` and
`freq=915`, same SPI0/CE1/GPIO 5/GPIO 25 pins), applies the user's four decisions
(15 dBm, +/-16 g, BMP280 in the packet, declination -0.113 rad), and makes every
sensor init and the main loop fail soft with the radio as the single hard failure.
The packet grows from 10 to 14 fields, so both decoder copies, the SQLite schema
(with an in-place migration) and playback change in lockstep, and the RX launchers
and systemd unit all point at `/home/pi/libs-venv/bin/python3`. All eleven
filenames are unchanged, including `RX_Temenetry.py`.

Watch for: none blocking. Three informational notes: the TX keeps unicast
addressing (node 2 -> node 1) instead of the Grok bench's `my_address=1` /
broadcast 255 (confirmed, deliberate, both ends agree); `tx_power=15` is the
user's override of the Grok file's 5 and the transcript's 14 (confirmed); and
the three BARO display formatters only null-check `agl_m` before formatting all
four baro values (possible, low impact).

**Verdict**: APPROVED

## High-level view

Every pin, address, device path, baud and radio constant in `TX_Telemetry.py`
matches the authority: GPS `/dev/serial0` at 9600 with `decode("ascii",
errors="ignore")` and a `$`+`GGA` filter; MPU-6050 `0x68` on bus 1 with
`ACCEL_CONFIG 0x1C = 0x18`; HMC5883L `0x1e` read X, Z, Y; BMP280 `0x76` via the
Pimoroni `bmp280` package with the first conversion discarded; RFM9x
`spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0, freq=915,
ModemConfig.Bw125Cr45Sf128, acks=False`. The only departures from
`Grok_TX_test.py` are the user-decided `tx_power=15` and the pre-existing
unicast addressing, neither of which is a hardware fact.

TX and RX agree on the radio (915 MHz, same ModemConfig, `acks=False`, TX node 2
sends to node 1, RX `--node` defaults to 1) and on the packet contract: the
`decode`, `_parse_gps`, `compute_heading`, `_to_int` and `_to_float_or_none`
functions and all scale/offset constants are AST-identical between
`telemetry_decode.py` and the inlined copy in `RX_Temenetry.py`; the 37-column
schema and 36-column insert are identical between `telemetry_db.py` and the RX
copy; `playback.py` reads the new columns through `telemetry_db`.

Robustness is where the plan asked for it. GPS, I2C bus, IMU, magnetometer,
barometer and camera inits each print `<sensor> unavailable` and continue; the
loop guards each sensor group with `except OSError`, the baro read and
`lora.send` with `except Exception`, and the status-line NMEA parse with
`except Exception`. `SystemExit` from the SIGTERM handler is a `BaseException`,
so none of those guards swallow it and `finally:` still finalises the video. Both
`on_recv` callbacks are fully wrapped so the lgpio interrupt thread cannot die.
No file decodes the GPS stream as UTF-8.

The systemd unit runs the venv interpreter from the `System Scripts` working
directory, drops the `After=multi-user.target` ordering cycle in favour of
`After=local-fs.target`, uses `Restart=always`, and exports
`TX_FLIGHT_DURATION_S=480`. The installer parses `ExecStart` to warn about a
missing venv and runs `systemd-analyze verify`.

The README matches the code on every point checked: venv at `~/libs-venv` with
`include-system-site-packages = true`, apt vs pip-in-venv split, the
`swig python3-dev liblgpio-dev` prerequisite, `enable_uart=1` /
`dtoverlay=disable-bt` / `console=serial0,115200` / `serial-getty@ttyAMA0`
steps, the venv interpreter in the service, Zero 2 W as the TX board, and a
from-scratch Pi 3 section with the hostname-collision warning.

RX redeploy: the summary says YES, and the RX diffs confirm it (packet format,
accel scale, declination, schema, launchers). The only RX hardware-adjacent
change is the radio call rewritten in keyword form with `acks=False` and
explicit `freq=915`; pins and ModemConfig are unchanged, so this is a
TX-compatibility fix, not an RX hardware change.

Verification evidence: the summary records `py_compile` OK x5 and `bash -n` OK
x3 plus mocked-hardware harnesses. No spot-check was needed; the AST comparison
incidentally parsed `telemetry_decode.py`, `RX_Temenetry.py` and
`telemetry_db.py` without error.

<details>
<summary>Issues (3)</summary>

1. **Unicast addressing vs Grok broadcast** (non-blocking, confirmed) — `TX_Telemetry.py` uses `my_address=2` and `send(message, 1)`; `Grok_TX_test.py` used `my_address=1` and `send("hello", 255)`. Addressing is a protocol choice, not a bench-tested hardware fact; both ends agree (RX `--node` default 1, library also accepts 255). No action unless the user prefers broadcast.
2. **tx_power 15 vs Grok 5 / transcript 14** (non-blocking, confirmed) — `TX_Telemetry.py:35` `LORA_TX_POWER_DBM = 15` is the user's recorded flight decision and overrides the authority values; the Grok file itself is untouched. No action.
3. **BARO formatters null-check only `agl_m`** (non-blocking, possible) — `telemetry_decode.format_readout`, `RX_Temenetry._baro_line` and `playback._print_row` format `alt_m`, `press_hpa`, `temp_c` with `:.1f` after checking only `agl_m is not None`. The TX always sends all four or none, so a partial can only come from a corrupt-but-parseable packet. RX is protected by the `on_recv` guard; playback would abort the replay. Optional hardening: check all four.

</details>

<details>
<summary>Details</summary>

### Hardware constants vs the Grok_* authority

`TX_Telemetry.py` lines 15-37 hold the constant block. `GPS_PORT = "/dev/serial0"`
and `GPS_BAUD = 9600` match `Grok_GPSTest.py` line 15. The port is opened with
`timeout=0` and drained via `in_waiting`/`read()` into a line buffer rather than
`readline()` with `timeout=0.5`; that is a loop-design choice for the 10 Hz
scheduler, not a hardware parameter, and the decode rule (`ascii`, `ignore`,
`startswith("$")`) is `Grok_GPSTest.py` lines 20-22 verbatim with the `GGA`
filter from line 5 moved into `poll_gps()`. The buffer is dropped if it exceeds
4 KB without a newline, which is the transcript's "binary garbage" case.

I2C: `MPU_Address = 0x68`, `MAG_Address = 0x1e`, `BARO_Address = 0x76`, bus 1,
`import smbus2 as smbus` (transcript section 6). `init_imu()` writes
`ACCEL_CONFIG (0x1C) = 0x18`, checks `WHO_AM_I == 0x68`, and discards one full
sample; `init_mag()` keeps registers `0x70 / 0xa0 / 0` and the loop reads
`X_axis_H, Z_axis_H, Y_axis_H` in that order. `init_baro()` constructs
`BMP280(i2c_dev=SMBus(1), i2c_addr=0x76)` as in transcript section 8, sleeps 1 s,
discards one `get_pressure()`, then averages 10 readings for the pad baseline.
It opens its own `SMBus(1)` instead of reusing `bus`; harmless (two descriptors
on `/dev/i2c-1`) and it means the barometer still works if the shared bus object
failed to open.

Radio: the `LoRa(...)` call at lines 303-311 passes exactly the keyword set of
`Grok_TX_test.py` (`spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0,
freq=915, modem_config=ModemConfig.Bw125Cr45Sf128, acks=False`). The two
deviations are issues 1 and 2 above. Sign conversion is `>= 32768` in both
`read_MPU_data` and `read_MAG_data`.

### Packet contract and the two decoder copies

```
TX_Telemetry.py  ──14 CSV fields + GGA──▶  RFM9x  ──▶  RX_Temenetry.py (inline decode/DB)
                                                        telemetry_decode.py (same code)
                                                        telemetry_db.py  ◀── playback.py
```

The structural comparison (AST dump with docstrings stripped) reports `decode`,
`_parse_gps`, `compute_heading`, `_to_int`, `_to_float_or_none` and the constants
`ACCEL_SCALE_MODIFIER_16G = 2048.0`, `GYRO_SCALE_MODIFIER_2000DEG = 16.4`,
`DECLINATION = -0.113`, `MAG_OFFSET_{X,Y,Z}`, `NUM_INT_FIELDS = 10`,
`NUM_SENSOR_FIELDS = 14` IDENTICAL between `telemetry_decode.py` and
`RX_Temenetry.py`. The schema regex extracted 37 columns from both `SCHEMA` and
`_SCHEMA` in the same order, and both `_INSERT` statements name 36 columns
(everything but `id`) with 36 placeholders.

A 10-field packet from an old TX fails the `len(parts) < 14` check and is
rejected as `TelemetryDecodeError`, so TX and RX must ship together; an old RX
fed new packets would divide by 16384 instead of 2048 and show 1/8 of the true g.

Issue 3: `format_readout` (`telemetry_decode.py` ~line 262), `_baro_line`
(`RX_Temenetry.py` ~line 353) and `_print_row` (`playback.py` ~line 54) all test
`agl_m is not None` and then format `alt_m`, `press_hpa`, `temp_c` with `:.1f`.
`read_baro()` on the TX returns all four or four `None`s, so a mixed row needs a
corrupted packet whose damage lands exactly inside one of fields 10-12 and still
parses as empty. The RX path is inside the `on_recv` try/except; playback has no
guard and would stop the replay with a `TypeError`. Low probability, optional fix.

### SIGTERM, guards and the lgpio callback thread

The non-obvious part of the fail-soft design is the interaction between the
SIGTERM handler and the loop guards. `_on_sigterm` raises `SystemExit(0)`;
because `SystemExit` derives from `BaseException`, none of the
`except Exception` / `except OSError` guards around the sensor groups,
`read_baro()`, `lora.send` and the status-line `pynmea2.parse` intercept it, so
`systemctl stop` reaches `finally:` and `camera.stop_recording()` runs. Had any
guard been `except BaseException`, the video would never be finalised. The radio
is initialised before the camera, so a wiring fault (`sys.exit(1)` + systemd
restart loop) never leaves orphaned `.h264` files.

Both `on_recv` bodies are fully wrapped (`TX_Telemetry.py` line 320,
`RX_Temenetry.py` line 500). `TelemetryDB` is opened with
`check_same_thread=False`, which the interrupt-thread insert requires.

### systemd unit

Verified values: `ExecStart=/home/pi/libs-venv/bin/python3 "/home/pi/Desktop/Avionics for Rocket/System Scripts/TX_Telemetry.py"`,
`WorkingDirectory=/home/pi/Desktop/Avionics for Rocket/System Scripts`,
`After=local-fs.target` with no `multi-user.target` in `After`/`Wants`,
`Restart=always`, `RestartSec=5`, `ExecStartPre=/bin/sleep 10`,
`Environment=TX_FLIGHT_DURATION_S=480`, `WantedBy=multi-user.target`. Device
nodes (`/dev/serial0`, `/dev/i2c-1`, `/dev/spidev0.1`) are created by udev, not
guaranteed by `local-fs.target`; the 10 s pre-start sleep is what covers that gap.

### README vs code

Checked against the scripts: venv recipe and `include-system-site-packages = true`
`sed`; apt list `swig python3-dev liblgpio-dev python3-picamera2 i2c-tools
python3-serial` vs pip-in-venv `pynmea2 pyserial smbus2 bmp280 pyLoraRFM9x`;
UART steps (`enable_uart=1`, `dtoverlay=disable-bt`, `console=serial0,115200`
removal, `serial-getty@ttyAMA0` stop/disable, `/boot/config.txt` stub warning)
match transcript sections 2-3; the import-check one-liner covers the TX's actual
imports; the RX section's `pip install pyLoraRFM9x pynmea2` plus apt `python3-tk`
covers every import in the four RX Python files; the radio paragraph (915 MHz,
Bw125Cr45Sf128, `acks=False`, node 2 -> 1, 15 dBm) matches both scripts; the
"RX redeploy required: YES" subsection lists the same files as the summary.

### Test coverage

The summary records `python3 -m py_compile` on all five `.py` files and
`bash -n` on all three `.sh` files, plus mocked-hardware harnesses for the TX
loop (plain, `FAIL=i2c,gps,baro`, `FAIL=lora`), decoder, DB migration, playback,
RX copies, launchers and unit file. Not tested, and not testable on the Mac: the
H.264 recording path, real `pyLoraRFM9x` init and air time, BMP280 update rate at
62.5 ms standby, systemd restart timing, lgpio behaviour, Tkinter rendering. The
plan and README already flag the video path as unverified on the bench.

</details>

<details>
<summary>File map</summary>

- `System Scripts/TX_Telemetry.py` — rewritten in place: authority constants, fail-soft inits, +/-16 g, BMP280 fields 10-13, keyword LoRa init, guarded loop, SIGTERM handler.
- `System Scripts/rocket-tx.service` — venv interpreter, `After=local-fs.target`, `Restart=always`, env vars.
- `System Scripts/install_tx_autostart.sh` — Zero 2 W wording, interpreter warning, `systemd-analyze verify`.
- `System Scripts/telemetry_decode.py` — 14-field contract, 2048 LSB/g, -0.113 rad, baro dict, GPS fix fields, ASCII decode.
- `System Scripts/RX_Temenetry.py` — same decoder/DB changes inlined, BARO display lines, guarded `on_recv`, keyword LoRa init with `acks=False`/`freq=915`, `RPi.GPIO` removed.
- `System Scripts/telemetry_db.py` — 4 `baro_*` columns, `_ensure_columns()` migration.
- `System Scripts/playback.py` — `BARO :` line, session max AGL, venv usage lines.
- `System Scripts/start_rx.sh`, `make_rx_gui_shortcut.sh` — fixed venv interpreter with error-and-exit guard.
- `System Scripts/Rocket-RX.desktop` — comment line only.
- `System Scripts/README.md` — rewritten: both boards, venv, UART setup, service behaviour, hardware tables, troubleshooting, Pi 3 from-scratch guide, RX redeploy section.

Full diff: `diff -ru ".agents/backup/System Scripts-2026-10-08" "System Scripts"` from the workspace root.

</details>
