# Local Log, Calibrated Altitude, Playback, and Graphs Handoff

Date: 2026-10-11

## Result

Implemented the requested local workspace enhancement without changing the LoRa packet layout or SQLite schema. No files were deployed to either Raspberry Pi and no project module or script was executed/imported on the Mac.

RX REDEPLOY REQUIRED: **YES** (dashboard, playback, labels, and graphing changed).  
TX REDEPLOY REQUIRED: **YES** (GPS-referenced barometric altitude and local CSV logging changed).

## Changed files

- `System Scripts/TX_Telemetry.py`
  - Creates one startup run folder and durable telemetry CSV before GPS lock.
  - Uses the same run folder/stamp for optional H.264 camera output.
  - Logs every attempted packet after `lora.send()` with send result.
  - Captures valid GGA pad altitude and a fresh mean pressure baseline at the existing three-fix lock transition.
  - Sends GPS-referenced absolute barometric altitude after lock.
  - Reports local-log errors in periodic status and shutdown summary.
- `System Scripts/RX_Temenetry.py`
  - Replaces artificial-horizon drawing with compact 30/60/90-degree `TILT TARGET`.
  - Keeps the complementary estimate, ZERO ATTITUDE, stale/frozen, and acceleration-reliability cues.
  - Adds explicit altitude-source and BMP chip-temperature labels.
  - Makes the existing GUI renderer reusable for playback with an optional mode title and completion indicator.
  - Live receiver behavior and radio initialization remain in `main()`.
- `System Scripts/telemetry_decode.py`
  - Updates field-12 semantics and readout labels; decoding contract remains unchanged.
- `System Scripts/telemetry_db.py`
  - Updates barometric-column comments only; schema remains 40 columns.
- `System Scripts/playback.py`
  - Replays RX SQLite sessions or TX-local CSV through `telemetry_decode.decode()`.
  - Reuses `RX_Temenetry.LiveState` and `run_gui_dashboard` for `--gui`.
  - Adds dependency-free Tkinter Canvas post-flight graphs via `--graphs`.
- `System Scripts/README.md`
  - Documents altitude calibration/fallback, BMP chip-temperature caveat, target semantics, TX log recovery, playback, graphing, and deployment checks.
  - Corrects boot-service and completed RX test status.
- `.kiro/steering/avionics-project.md`
  - Records durable architecture, paths, CLI modes, calibration semantics, and implementation history; corrects stale RX setup and local-log facts.

## TX local log

Startup path:

`/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/telemetry_YYYY-MM-DD_HH-MM-SS.csv`

Camera path, when enabled, uses the same folder and stamp:

`/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/flight_YYYY-MM-DD_HH-MM-SS.h264`

Exact CSV header/schema:

1. `tx_epoch` — wall-clock Unix timestamp with microseconds
2. `tx_iso` — local ISO wall-clock timestamp
3. `tx_monotonic` — monotonic timestamp used for stable replay pacing
4. `send_ok` — `1` when `lora.send()` returned truthy, otherwise `0`
5. `payload` — complete 17-fixed-field-plus-GGA payload, CSV-quoted as one field

The CSV exists even with `TX_DISABLE_CAMERA=1`. PAD, COUNTDOWN, FLIGHT, and RECOVERY attempts are logged. Every row is flushed; every tenth row and clean shutdown are `fsync`ed. Creation/write/close failures are fail-soft, increment `telemetry_log_errors`, and do not stop transmission. Media bytes are never written to the payload or sent over LoRa.

## Barometric calibration

At the same three-consecutive-valid-GGA event that enters COUNTDOWN:

1. Clear `pad_pressure_hpa` and `pad_gps_alt_m`.
2. Parse the current valid GGA altitude as `pad_gps_alt_m` when available.
3. Capture a fresh mean of 10 BMP280 pressure readings, 0.1 seconds apart, as `pad_pressure_hpa`.

Pressure-relative AGL remains:

`baro_agl_m = pressure_to_altitude_m(current_pressure, chip_temp, pad_pressure_hpa)`

After lock, absolute altitude field 12 becomes:

`baro_alt_m = pad_gps_alt_m + baro_agl_m`

Before lock, or when the lock GGA has no usable altitude, field 12 remains the uncalibrated standard-atmosphere estimate using 1013.25 hPa. Packet field positions are unchanged. The GUI labels post-PAD altitude `ALT (GPS REF)` and PAD altitude `ALT (UNCAL)`. BMP temperature is labeled `BMP CHIP TEMP` because self-heating/enclosure conditions can differ from ambient air.

## Target-style tilt display

- MPU +Z remains the upright/nose axis.
- ZERO ATTITUDE stores the current pitch/roll offsets and centers the dot.
- Rings represent 30, 60, and 90 degrees.
- X and Y crosshairs are labeled.
- Pitch/roll map linearly to target X/Y coordinates and the dot is radially clamped to the outer 90-degree ring.
- Numeric X, Y, and total tilt remain visible.
- Existing complementary filtering and `FROZEN`, `STALE`, and `ACCEL UNRELIABLE` indications remain.
- Canvas remains 190x165 within the existing 800x450/800x480-compatible layout.

## Playback CLI

From `/home/pi/Desktop/Avionics for Rocket/System Scripts` on RX:

```bash
# Existing database behavior
/home/pi/libs-venv/bin/python3 playback.py --list
/home/pi/libs-venv/bin/python3 playback.py --session flight_YYYYMMDD_HHMMSS
/home/pi/libs-venv/bin/python3 playback.py --db /path/to/RxTelemetry.db --speed 4

# Same dashboard as live RX
/home/pi/libs-venv/bin/python3 playback.py --gui
/home/pi/libs-venv/bin/python3 playback.py --session flight_YYYYMMDD_HHMMSS --gui
/home/pi/libs-venv/bin/python3 playback.py --tx-log /path/to/telemetry_YYYY-MM-DD_HH-MM-SS.csv --gui

# Lightweight post-flight graphs
/home/pi/libs-venv/bin/python3 playback.py --session flight_YYYYMMDD_HHMMSS --graphs
/home/pi/libs-venv/bin/python3 playback.py --tx-log /path/to/telemetry_YYYY-MM-DD_HH-MM-SS.csv --graphs
```

For SQLite input, playback reconstructs all 17 fixed fields plus stored `gps_raw`, then decodes that payload. For TX CSV, it decodes the quoted payload directly. Receive/monotonic timestamps control pacing; `--speed 0` removes delay and long replay pauses remain capped at 10 seconds. GUI playback retains final values and visibly shows `PLAYBACK COMPLETE`. TX logs supply no RSSI/SNR, so those values display as unavailable rather than fabricated values.

## Graphs

Tkinter Canvas only; no matplotlib, numpy, or new dependency. One selectable trace is shown at a time:

- Barometric AGL (ft)
- Total acceleration (g)
- Gyro magnitude (deg/s)
- GPS altitude (ft)

The horizontal axis uses T-clock when present and extends that timeline backward through PAD using stored timing. If no T-clock exists, elapsed record time is used. Missing measurements are skipped/broken rather than converted to zero. Constant ranges receive display padding. Each trace is uniformly decimated to at most 1000 points for Pi 3 responsiveness.

## Validation

Permitted Mac-side syntax validation only:

```text
python3 -m py_compile System Scripts/TX_Telemetry.py
python3 -m py_compile System Scripts/RX_Temenetry.py
python3 -m py_compile System Scripts/telemetry_decode.py
python3 -m py_compile System Scripts/telemetry_db.py
python3 -m py_compile System Scripts/playback.py
```

All five changed Python files passed with exit code 0. Generated bytecode cache was removed; no `__pycache__` remains. No shell files changed, so `bash -n` was not applicable. No code was executed/imported and no dependencies were installed.

## Real-Pi deployment and test checklist

1. Keep TX service stopped during file replacement. Back up current `System Scripts` on each Pi.
2. Deploy `TX_Telemetry.py` and README to TX. Deploy `RX_Temenetry.py`, `telemetry_decode.py`, `telemetry_db.py`, `playback.py`, and README to RX.
3. Syntax-check with TX Python 3.13 venv and RX Python 3.7 venv.
4. Start RX GUI first, then start TX manually for the initial test; do not reboot into the service until behavior is verified.
5. Immediately confirm one TX run folder and five-column telemetry CSV exist before GPS lock, including PAD rows and quoted payloads.
6. With camera disabled, confirm the CSV still exists. With camera enabled in a later test, confirm H.264 and CSV share exactly one run folder/stamp.
7. Hold the payload vertical/stationary through gyro calibration and three valid GGA fixes. Confirm fresh pressure baseline, logged GPS pad altitude, AGL near zero, and displayed `ALT (GPS REF)` near GPS pad altitude.
8. Press ZERO ATTITUDE; confirm centered dot, then verify X/Y movement and 30/60/90-degree ring/clamp behavior by controlled tilts.
9. Confirm 3 Hz radio operation, 0.5-second send timeout, timeline transitions, camera duration, status flags, and RECOVERY behavior are unchanged.
10. Stop TX cleanly and inspect the shutdown summary for send/sensor/local-log error counts.
11. Copy the TX CSV to RX. Test terminal, `--gui`, and `--graphs` playback. Confirm completion remains visible, RSSI/SNR are unavailable for TX logs, all four graphs render, and SQLite playback still works.
12. Compare TX CSV attempts/send results against RX SQLite packet count to identify radio losses.
13. Re-enable/start boot behavior only after the parent/user approves the real-hardware results.

READY FOR REAL-PI DEPLOYMENT: YES
