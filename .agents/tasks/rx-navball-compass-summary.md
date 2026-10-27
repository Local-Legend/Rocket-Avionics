# RX Nav Ball, Compass, and Imperial Display Summary

## Changed files

- `System Scripts/RX_Temenetry.py`
  - Added presentation-only imperial units to GUI, terminal dashboard, and scroll mode.
  - Added an 800x450 Tkinter layout with side-by-side attitude and compass canvases above compact component rows.
  - Added a receive-time complementary attitude estimate and recovery/stale retention cues.
  - Added `COMPASS_DISPLAY_OFFSET_DEG = 0.0` for display-only heading alignment.
- `System Scripts/telemetry_decode.py`
  - Updated `format_readout()` presentation units only; decoder output and packet contract are unchanged.
- `System Scripts/playback.py`
  - Updated replay presentation units only; stored SQLite values are unchanged.
- `System Scripts/README.md`
  - Documented units, visualization limits, compass offset calibration, display size, and the absence of speed/MPH in GGA telemetry.
- `.kiro/steering/avionics-project.md`
  - Recorded the durable RX display decisions and deployment requirement.

## Presentation conversions

Wire and SQLite values remain unchanged. Displays use:

- Acceleration: `g * 32.17405` -> `ft/s^2`
- Barometric altitude and AGL: `m * 3.28084` -> `ft`
- GPS altitude: `m * 3.28084` -> `ft`
- MPU-6050 and BMP280 temperature: `C * 9/5 + 32` -> `F`
- Pressure: `hPa * 0.0295299830714` -> `inHg`
- Gyro remains `deg/s`; heading remains degrees; coordinates remain decimal degrees; RSSI/SNR remain dBm/dB.
- MPH is not displayed because the current GGA payload has no speed field. Adding speed would require a future packet-contract/source change such as RMC data.

## Attitude estimate

The display treats MPU-6050 +Z as the rocket nose/upright axis. Accelerometer pitch and roll are computed from the current X/Y/Z vector. Between packets, gyro X/Y rates integrate roll/pitch; when acceleration magnitude is within 0.75-1.25 g, a complementary filter uses 98% integrated gyro and 2% accelerometer tilt. Outside that range, the estimate uses gyro integration and displays `ACCEL UNRELIABLE` because dynamic acceleration corrupts gravity-derived tilt. Integration delta is clamped to one second.

The circular canvas draws a clamped horizon chord, center reticle, pitch, roll, and total tilt. It retains the last estimate and shows `FROZEN` in RECOVERY or `STALE` after link timeout. This is explicitly an estimate, not flight-control-grade orientation; gyro drift and dynamic acceleration remain limitations.

## Compass

The compass uses the existing calibrated/declination-corrected HMC5883L X/Y heading. The rose rotates under a fixed pointer and shows N/E/S/W, 30-degree ticks, numeric heading, `UNCALIBRATED X`, stale/frozen state, and `OFFSET 0.0°`.

`COMPASS_DISPLAY_OFFSET_DEG` is added only to RX presentation headings and wrapped to 0..359. It does not change raw decoding, database values, or the packet. Change this constant after the rocket front/zero-heading edge is finalized.

## Validation

Mac syntax-only validation passed:

```text
python3 -m py_compile RX_Temenetry.py telemetry_decode.py playback.py
Exit code: 0
```

The generated `System Scripts/__pycache__` directory was removed. No project script was run or imported, no mock/harness was used, and no remote deployment, process restart, database creation, or package installation occurred.

## RX deployment instructions

The parent workflow should:

1. Back up the current RX files on `pi@192.168.1.101`.
2. Copy `RX_Temenetry.py`, `telemetry_decode.py`, `playback.py`, and `README.md` into `/home/pi/Desktop/Avionics for Rocket/System Scripts/`.
3. Syntax-check changed Python files with `/home/pi/libs-venv/bin/python3 -m py_compile` on the Pi 3 (Python 3.7.3).
4. Stop the currently running old GUI only when ready, then restart `/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui`.
5. Verify the 800x470 layout and perform an end-to-end TX/RX hardware test. Confirm +Z-upright attitude behavior, dynamic-acceleration warning, compass direction/offset, RECOVERY freeze, and link staleness.

No TX redeploy is required because the packet contract is unchanged. RX redeploy is required for the new presentation and GUI.

READY TO DEPLOY TO RX: YES
