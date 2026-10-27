# RX G / altitude / temperature update summary

Date: 2026-10-27

## Result

Implemented, deployed to both Raspberry Pis, and verified through the real LoRa/SQLite path. The packet remains 17 fixed CSV fields plus GGA, and the SQLite schema remains 40 columns.

## Changes

- `System Scripts/TX_Telemetry.py`
  - Added `BARO_GPS_REFERENCED = 1 << 7` (value 128).
  - Sets the bit only after the current process has both a fresh pad-pressure mean and a valid pad GPS altitude. The state remains explicit in RECOVERY so retained altitude stays correctly labeled.
  - Confirmed existing startup/lock logic initializes both anchors to `None`, clears both again at the three-consecutive-fix transition, captures the lock GGA altitude, then captures a fresh ten-reading pressure mean. No prior-process baseline can be reused.
- `System Scripts/RX_Temenetry.py`
  - GUI/terminal/scroll acceleration now shows only current total g and `MAX FLIGHT G`; raw X/Y/Z remains decoded and stored.
  - `MAX FLIGHT G` resets on entry to phase `F`, only increases in `F`, and remains visible through RECOVERY/link staleness.
  - Split the old combined barometer row into `ALTITUDE` and `BMP CHIP TEMP` rows.
  - Altitude shows absolute ft, AGL ft, pressure inHg, and uses status bit 128—not phase—to choose `GPS REF` or `UNCAL`.
  - Renamed MPU presentation to `MPU DIE TEMP`; both MPU and BMP lines state that values are diagnostic die/chip temperatures, not ambient.
  - Kept the 800x450 GUI with visible 798x436 X11 window and NAV/COMPASS stacked on the right; reduced row padding modestly for eight left-panel rows.
- `System Scripts/telemetry_decode.py`
  - Added bit-128 constant and equivalent separated/readable output.
  - Preserved MPU conversion `raw / 340 + 36.53` C and all wire values.
- `System Scripts/playback.py`
  - Terminal playback now runs records through `LiveState`, prints FLIGHT-only max G, and formats retained state in RECOVERY.
  - GUI playback automatically uses the updated shared `LiveState`/renderer.
  - Total-acceleration graph remains correctly labeled in g.
- `System Scripts/README.md`
  - Documented current/max-flight-G semantics, separate altitude/temperature rows, die-temperature caveats, and status bit 128.
- `.kiro/steering/avionics-project.md`
  - Updated durable packet/status semantics, corrected current 3 Hz/service/NAV facts, and recorded deployment/runtime findings.
- `System Scripts/telemetry_db.py`
  - Read and verified; unchanged because the existing integer `status_flags` column stores bit 128 without a migration.

## RX reboot recovery

Before the RX disconnect/reboot, no remote stop, backup, copy, or restart had occurred. Only local workspace edits were in progress. After reconnect:

- Positively identified `Raspberry Pi 3 Model B Plus Rev 1.3` with a fresh uptime (boot/start reported 2026-10-27 17:10:12).
- Verified all six relevant remote file checksums were still pre-change and no new deployment backup existed.
- Verified no RX service exists; the desktop had relaunched the same manual GUI command, `/home/pi/libs-venv/bin/python3 .../RX_Temenetry.py --gui`, with `DISPLAY=:0.0` and `XAUTHORITY=/home/pi/.Xauthority`.
- Rechecked TX independently: Pi Zero 2 W had not rebooted, service PID 1695 was still enabled/active, and telemetry remained healthy.

## Backups and deployment

Initial backups containing every replaced file (`TX_Telemetry.py`, `RX_Temenetry.py`, `telemetry_decode.py`, `playback.py`, `README.md`):

- TX: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-g-alt-temp-backup-20261027-171257`
- RX: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-g-alt-temp-backup-20261027-171258`

A real recovery-playback check exposed that terminal playback was formatting raw zeroed recovery packets rather than retained `LiveState` data. Before replacing that deployed file, an additional backup was made on each Pi:

- TX and RX: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-g-alt-temp-playback-fix-backup-20261027-171532`

TX was stopped through systemd immediately before replacement. It finalized cleanly at 2586 packets with zero send failures, zero sensor errors, and zero local-log errors; no video was active because GPS had no fix. The service remained enabled. RX PID 954 (the post-reboot desktop-launched GUI) was terminated cleanly immediately before replacement.

## Validation

- Mac: `python3 -m py_compile` passed for all Python files; generated `System Scripts/__pycache__` was deleted. No project code was executed/imported, no mocks/harnesses were used, and no packages were installed.
- TX: Python 3.13 venv syntax checks passed; deployed checksums matched local files.
- RX: Python 3.7 venv syntax checks passed; deployed checksums matched local files.
- TX restart:
  - Enabled and active, PID 1841.
  - Opened `/home/pi/Desktop/flight_2026-10-27_17-13-42/telemetry_2026-10-27_17-13-42.csv`.
  - Gyro startup calibration completed with raw offsets X=-22, Y=9, Z=15.
  - Camera ov5647 configured successfully but remained standby in PAD.
  - Sustained 3.0 Hz with zero TX, I2C/sensor, and local-log errors.
- RX restart:
  - Manual GUI mode preserved, PID 1087, log `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-g-alt-temp-20261027-171331.log`.
  - X11 showed `Rocket Telemetry LIVE - flight_20261027_171332` at 798x436.
  - First observed interval stored 96 real packets in about 32 seconds; the final check showed 605 rows through 17:17:11 with one and only one RX GUI process.
- Latest measured real row during validation:
  - phase `P`, flags `47`; bit 128 correctly clear before calibration
  - total acceleration `1.058 g`
  - MPU die temperature `87.3 F`
  - BMP die temperature `87.6 F`
  - pressure `1016.31 hPa`
  - uncalibrated absolute barometric altitude `-87.9 ft`; AGL absent before lock
  - RSSI `-38 dBm`, SNR `9.75 dB`
- Existing real P/C/F session `flight_20261010_004935` replay:
  - PAD current 1.05 g, max `--`
  - COUNTDOWN current 1.05 g, max `--`
  - first FLIGHT packet at T+00:00 current/max 1.05 g
  - by T+00:38 max was 1.11 g
- Existing real F/R session `flight_20261011_135755` replay after the focused playback correction:
  - first FLIGHT sample current/max 1.05 g
  - first and last RECOVERY samples retained current 1.05 g and max 1.39 g through T+09:15
- Security exposure scan found no wildcard binds/listeners or weakened-auth patterns. No network settings or radio settings changed.

## Unresolved / not observed

- GPS remained no-fix during this deployment window. Therefore a live transition to COUNTDOWN and the status-bit change from clear to set (value +128) were not observed. Pre-lock behavior was verified with real packets; the set condition was verified in source and syntax checks. On the next quick outdoor lock, confirm flags include 128 and ALTITUDE changes from `ABS (UNCAL)` to `ABS (GPS REF)` with AGL near zero.
- The observed MPU value is plausible diagnostic die temperature, not evidence of a broken raw conversion. No cosmetic ambient offset was applied.
- TX `get_throttled=0x0`. RX remains `get_throttled=0x50005`; per user direction this was recorded but did not block validation.
