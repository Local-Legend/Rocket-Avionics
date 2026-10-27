# 7 Hz BW500 telemetry update — deployment and hardware verification

Date: 2026-10-27

## Result

The 7 Hz high-bandwidth mode is deployed and passed the bench acceptance test. Both radios now use `ModemConfig.Bw500Cr45Sf128` at 915 MHz (SF7, 500 kHz bandwidth, CR4/5). Full PAD/COUNTDOWN/FLIGHT telemetry targets 7 Hz; RECOVERY remains GPS-only at 1 Hz. TX remains enabled and active, and the manual RX GUI is active on the Pi desktop.

There is no packet-contract or database-schema change: telemetry remains 17 fixed CSV fields plus GGA, and SQLite remains 40 columns. An old BW125 RX/TX copy cannot communicate with this BW500 configuration even though its packet schema is the same. Both ends must always use the exact same modem preset.

BW500 reduces receiver sensitivity and expected range relative to BW125. The bench link was verified; flight range was not verified and requires a representative range test before flight.

## Installed-library verification before editing

Read-only Python introspection was run on each real Pi before any source edit:

- TX `pi@192.168.1.172`: pyLoraRFM9x `1.0.2`; `hasattr(ModemConfig, "Bw500Cr45Sf128")` returned `True`; value printed as `ModemConfig.Bw500Cr45Sf128`.
- RX `pi@192.168.1.101`: pyLoraRFM9x `0.9.2`; the same member check returned `True`; value printed as `ModemConfig.Bw500Cr45Sf128`.

No substitute preset was used.

## Changed files

- `System Scripts/TX_Telemetry.py`
  - Both-radio preset changed from BW125 to exactly `ModemConfig.Bw500Cr45Sf128`.
  - `SAMPLE_RATE_HZ` changed from 3 to 7.
  - Recovery remains 1 Hz.
  - `lora.wait_packet_sent_timeout` remains 0.5 seconds.
  - Current scheduling/radio comments now describe 7 Hz and an estimated 58–62 ms airtime for a typical 140–150-byte packet.
- `System Scripts/RX_Temenetry.py` (name intentionally unchanged)
  - Modern and legacy constructors both use exactly `ModemConfig.Bw500Cr45Sf128`.
  - Two-decimal presentation applied to current/max-flight g, gyro axes, barometric absolute/AGL altitude, pressure, MPU/BMP die temperatures, NAV X/Y/total tilt, and compass heading in GUI, terminal, and scroll modes.
- `System Scripts/telemetry_decode.py`
  - Text readout/playback uses two decimals for barometric altitude/AGL, pressure, MPU/BMP temperatures, total g, gyro axes, and heading where displayed.
- `System Scripts/README.md`
  - Documents BW500, 7 Hz, estimated airtime, sensitivity/range tradeoff, exact-preset compatibility requirement, unchanged packet/schema, and two-decimal display behavior.
- `.kiro/steering/avionics-project.md`
  - Current radio/rate facts and history updated. Historical 3 Hz/BW125 test records were preserved as history.
- `.agents/tasks/7hz-bw500-update-summary.md`
  - This report.

`telemetry_db.py`, `playback.py`, and `rocket-tx.service` were fully read and left unchanged. Playback receives the new textual formatting through `telemetry_decode.py` and the same-GUI formatting through `RX_Temenetry.py`.

## Airtime estimate

For SF7, BW500, CR4/5, explicit header and payload CRC, a typical 140–150-byte telemetry payload plus the RadioHead framing is approximately 58–62 ms on air (about 60 ms). This is roughly one quarter of the former BW125 airtime. It leaves practical scheduling margin within the 1/7-second sample period and far more than the retained 0.5-second TX-done timeout.

This is a calculated estimate, not a flight-range result.

## Backups and deployment

Initial runtime state:

- TX service enabled/active, PID 994.
- RX manual GUI active, PID 1087, `DISPLAY=:0.0`.
- TX `get_throttled=0x0`; RX `get_throttled=0x50005`.

Timestamped backups on both Pis:

- TX: `/home/pi/Desktop/Avionics for Rocket/System Scripts/7hz-bw500-backup-20261027-173422/`
- RX: `/home/pi/Desktop/Avionics for Rocket/System Scripts/7hz-bw500-backup-20261027-173422/`

Each backup contains every replaced remote file: `TX_Telemetry.py`, `RX_Temenetry.py`, `telemetry_decode.py`, and `README.md`.

The TX service was stopped cleanly immediately before replacement. Its prior run finalized with 1,948 successful packets, one send failure, zero sensor errors, zero local-log errors, and finalized CSV/video paths. The service remained enabled. The manual RX GUI was terminated immediately before replacement. Files were then installed on both Pis, and staged files were removed after validation.

Deployed SHA-256 values matched local values on both Pis:

- `TX_Telemetry.py`: `24905505c497408d6b2e09d587d419045dfd68d1db55b76077206683dc2665b4`
- `RX_Temenetry.py`: `bc6f2c48a39f91e445ea6a4b4a96e66f8cc94185e35526ef7e9b5115f9117e6f`
- `telemetry_decode.py`: `be0cb1fc75e03e79c86e726258c1db541411c058678785232d5b80f242254651`
- `README.md`: `7ba50ffd20713eef19090850a7b5911b5541851e6ac414376be9fb11caf563f3`

RX GUI was restarted first on `DISPLAY=:0.0`; TX service was restarted second. No reboot occurred and no RX service was created.

## Syntax validation

Mac syntax-only validation (project code was never executed or imported):

- `python3 -m py_compile` passed for `TX_Telemetry.py`, `RX_Temenetry.py`, `telemetry_decode.py`, `telemetry_db.py`, and `playback.py`.
- Generated local `__pycache__` was deleted.
- `bash -n` passed for `install_tx_autostart.sh`, `start_rx.sh`, and `make_rx_gui_shortcut.sh`.

Real Pi syntax validation:

- TX Python 3.13 venv compiled all five Python files successfully; remote `__pycache__` was deleted.
- RX Python 3.7 venv compiled all five Python files successfully; remote `__pycache__` was deleted.

## Real-hardware acceptance test

New RX SQLite session: `flight_20261027_173451`

New TX local log:

`/home/pi/Desktop/flight_2026-10-27_17-37-06/telemetry_2026-10-27_17-37-06.csv`

The system naturally obtained GPS lock with eight satellites, captured the pressure/GPS altitude baseline, started local camera recording, transitioned PAD → COUNTDOWN → FLIGHT, and retained the unchanged 17-field/status behavior.

A synchronized timestamp-bounded interval was used so TX and RX were compared over identical wall-clock boundaries:

- Epoch interval: `1793137131` through `1793137176`
- Duration: 45 seconds
- TX CSV attempts: 315
- TX CSV `send_ok`: 315
- TX attempt rate: **7.000 Hz**
- RX SQLite rows: 305
- RX receive rate: **6.778 Hz**
- Delivery ratio: **96.83%**
- Acceptance target: at least 95% — **passed**

A preliminary unsynchronized endpoint count over approximately 42 seconds showed 293 TX attempts and 271 RX rows (92.49%). Because start/end counts were collected over SSH at different instants, it was treated as a diagnostic rather than the acceptance result. The follow-up synchronized timestamp query above removed that boundary skew and passed.

Link and validity metrics for the synchronized interval:

- RSSI average: **-21.04 dBm**; range **-22.0 to -20.0 dBm**
- SNR average: **5.66 dB**; range **5.0 to 6.5 dB**
- Phase/status: all 305 rows were FLIGHT with status flags `239`
- Invalid/null status/phase rows: **0**
- RX decode/on-receive errors in the new GUI log: **0**
- TX sustained send failures: **0**
- TX I2C/sensor errors: **0**
- TX local-log errors: **0**
- TX rate-meter steady readings: predominantly 7.0 Hz, within the accepted 6.7–7.3 Hz band apart from isolated one-second window jitter that averaged exactly 7.000 Hz in the CSV interval
- TX CSV growth: exactly 7.000 rows/s in the acceptance interval

No rate adjustment was needed; final `SAMPLE_RATE_HZ` remains exactly 7 and the modem was not reverted or substituted.

## Final state

- TX: `rocket-tx.service` **enabled and active**, PID **1731**; one exact TX Python process.
- RX: manual GUI **active**, PID **2648**, one exact RX Python process on `DISPLAY=:0.0`; visible Tk window `Rocket Telemetry LIVE - flight_20261027_173451`, 798×436.
- TX power status: `get_throttled=0x0`.
- RX power status: `get_throttled=0x50005`; this is the previously known undervoltage/throttling condition the user elected not to block on. It remains recorded and should still be corrected before relying on worst-case flight performance.

## Remaining caveat

Bench throughput and delivery passed, but radio range at BW500 has not been verified. Perform a representative outdoor range test with both deployed copies before flight. Do not mix BW125 and BW500 copies at either end.
