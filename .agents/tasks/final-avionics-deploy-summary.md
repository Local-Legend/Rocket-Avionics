# Final Avionics Deployment Summary

Date: 2026-10-08

## Delivered

Completed the integrated flight TX and ground-station RX in one coding pass. No project script was run or imported on the Mac, no harness or fake hardware module was created, and no package was installed.

Local files changed:

- `System Scripts/TX_Telemetry.py`
- `System Scripts/RX_Temenetry.py`
- `System Scripts/telemetry_decode.py`
- `System Scripts/telemetry_db.py`
- `System Scripts/playback.py`
- `System Scripts/README.md`
- `.kiro/steering/avionics-project.md`

The existing `rocket-tx.service` already matched the required continuous timeline and `Restart=on-failure`; it was deployed unchanged with the rest of `System Scripts`.

## Packet and status contract

The packet now has 17 fixed CSV fields followed by raw GGA:

- 0-9: MPU-6050 and HMC5883L raw signed integers
- 10-13: BMP280 temperature, pressure, absolute altitude, and pad-relative AGL
- 14: phase (`P`, `C`, `F`, `R`)
- 15: signed T-clock seconds, blank in PAD
- 16: decimal `status_flags`
- 17 onward: raw `$GNGGA`/`$GPGGA` sentence

Status bits: IMU available=1, magnetometer available=2, barometer available=4, GPS available/recent=8, GPS currently locked=16, camera available=32, camera recording=64.

IMU/magnetometer/barometer availability means initialized/known hardware. Their bits remain set in RECOVERY while reads stop; RX retains their last known values and labels them frozen. GPS lock can clear without changing phase, and RX retains the last coordinates. Camera displays UNAVAILABLE, STANDBY, RECORDING, or STOPPED. Radio health is derived from packet age and RSSI/SNR.

Both DB implementations now have a 40-column `telemetry` table. `status_flags INTEGER` is added through the existing idempotent migration guard; older rows receive NULL. Playback displays the stored status.

## GUI

The Tkinter GUI retains the large phase and T clock and adds compact component rows for:

- accelerometer X/Y/Z in g
- MPU gyro X/Y/Z and MPU temperature
- BMP280 pressure, absolute altitude, AGL, and temperature
- camera state
- prominent GPS LOCKED/SEARCHING/UNAVAILABLE state, last coordinates, and satellites
- magnetometer heading plus raw/calibrated values
- radio LIVE/STALE state, RSSI/SNR, and packet age

`LiveState` retains the most recent meaningful component values when a later packet reports a failure or RECOVERY omits active sensor readings. The RX does not synthesize the T clock.

## Validation

Mac, syntax only:

```bash
python3 -m py_compile "System Scripts"/*.py
bash -n "System Scripts"/*.sh
```

Result: PASS. Generated `System Scripts/__pycache__` was deleted. No app code was run/imported.

TX Pi Zero 2 W (`192.168.1.172`):

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
/home/pi/libs-venv/bin/python3 -m py_compile ./*.py
bash -n ./*.sh
```

Result: PASS using the deployed Python 3.13 venv. Remote `__pycache__` deleted. The application and service were not started.

RX Pi 3 B+ (`192.168.1.101`):

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
/usr/bin/python3 -m py_compile ./*.py
bash -n ./*.sh
```

Result: PASS using the actual Python 3.7.3 interpreter. This is the definitive Python 3.7 syntax gate. Remote `__pycache__` deleted. The receiver was not started.

## Backups and deployment

Backups created before deployment and retained:

- TX: `/home/pi/Desktop/Avionics for Rocket/System Scripts.backup-20261008-172221`
- RX: `/home/pi/Desktop/Avionics for Rocket/System Scripts.backup-20261008-172216`

The entire local `System Scripts` folder was deployed to both:

- TX: `/home/pi/Desktop/Avionics for Rocket/System Scripts`
- RX: `/home/pi/Desktop/Avionics for Rocket/System Scripts`

No `.agents`, steering files, workspace backups, root scripts, or PDFs were deployed.

## Not tested

- No TX hardware behavior: external hardware was not attached, so sensors, GPS, radio, camera, timeline transitions, and service startup were not exercised.
- No RX runtime behavior: its RFM9x wiring and required packages/venv are not ready, so radio reception, SQLite writes, and Tkinter rendering were not exercised.
- H.264 recording/finalization remains unverified on hardware.
- BMP280 62.5 ms standby behavior remains unverified.
- Packet airtime/achieved sample rate remains unverified.

## Exact user bench-test checklist

1. Attach TX MPU-6050, BMP280, HMC5883L, GPS, ov5647 camera, RFM9x, and antenna. Attach and verify the RX RFM9x and antenna.
2. On TX, run `i2cdetect -y 1` and confirm `68`, `1e`, and `76`; run `ls -l /dev/serial0` and confirm `ttyAMA0`; run `rpicam-hello --list-cameras` and confirm ov5647.
3. Complete RX Buster package/venv setup from `README.md`, or preferably upgrade to a supported Raspberry Pi OS first. Confirm `/home/pi/libs-venv/bin/python3 -c 'import tkinter, pynmea2, pyLoraRFM9x; print("RX imports OK")'`.
4. Start the RX GUI first with `/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui`. Confirm RADIO LINK initially shows WAITING/STALE.
5. With both antennas attached, start TX manually or install/start `rocket-tx.service`. Confirm radio becomes LIVE with RSSI/SNR and all attached sensor rows show ACTIVE; camera should show STANDBY in PAD.
6. Confirm GPS moves from UNAVAILABLE/SEARCHING to LOCKED and displays coordinates and satellite count.
7. After three consecutive valid GGA fixes, confirm phase COUNTDOWN, clock begins at T-30, BMP280 AGL zeroes near the pad, camera shows RECORDING, and an H.264 file appears.
8. Confirm T-0 transitions to FLIGHT without interrupting telemetry or video.
9. For a short bench-only recovery test, temporarily set `TX_FLIGHT_DURATION_S` to a small value. Confirm RECOVERY finalizes the video, transmits GPS-only at 1 Hz, and shows IMU/magnetometer/barometer last values as FROZEN (RECOVERY).
10. Temporarily obstruct/disconnect GPS after initial lock. Confirm GPS changes from LOCKED to SEARCHING/UNAVAILABLE, coordinates remain visible, and phase/T clock continue forward.
11. Stop TX cleanly and confirm the video is playable. Use `playback.py --list` and replay the session; verify phase, T clock, status, barometer, GPS, and link records.
12. Restore `TX_FLIGHT_DURATION_S=480`, verify both radios still use 915 MHz / `Bw125Cr45Sf128` / `acks=False`, and confirm TX power 15 dBm before flight.

READY FOR USER BENCH TEST: YES
