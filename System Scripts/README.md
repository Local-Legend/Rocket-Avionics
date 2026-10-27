# Rocket Avionics System Scripts

These files run only on the Raspberry Pis; do not run them on the development Mac.

- **TX:** Raspberry Pi Zero 2 W, `pi@192.168.1.172`, Debian 13 (Trixie), Python 3.13.5 in `/home/pi/libs-venv`. It reads the MPU-6050 accelerometer/gyro/temperature, BMP280 altimeter, HMC5883L magnetometer and GPS, records the ov5647 camera locally, and transmits with the RFM9x.
- **RX:** Raspberry Pi 3 B+, `pi@192.168.1.101`, Raspbian 10 (Buster), Python 3.7.3 in `/home/pi/libs-venv`. It receives, records to SQLite, and displays terminal, scrolling, or Tkinter GUI output. End-to-end 7 Hz reception and GUI operation have been verified on the real Pis with the BW500 preset.

Both hosts are currently named `raspberrypi`; use the IP addresses until one is renamed. The project path on both is `/home/pi/Desktop/Avionics for Rocket/System Scripts`.

## Flight timeline

The TX runs continuously until power is removed:

1. **PAD:** full telemetry, local camera ready but not recording, blank T clock, waiting for GPS lock.
2. **COUNTDOWN:** after three consecutive valid GGA fixes, capture a fresh mean BMP280 pad-pressure baseline and the current valid GGA pad altitude, start local camera recording, and start at T-30. From this point field 12 is `pad_gps_alt_m + baro_agl_m`; field 13 is pressure-relative to the fresh baseline and should be near zero at lock.
3. **FLIGHT:** begins at T-0; full telemetry continues. The TX finalizes its local video at T+3 minutes (`TX_CAMERA_STOP_TPLUS_S=180`).
4. **RECOVERY:** at `TX_FLIGHT_DURATION_S` after T-0 (480 seconds in the service), transmit GPS-only packets at 1 Hz forever. IMU/magnetometer/barometer status bits continue to mean initialized/known hardware, but their last readings are shown as frozen.

The camera records only to the TX SD card. Photos and video are never included in LoRa packets, and the RX has no camera display.

GPS loss after initial lock does not move the phase backward or stop the monotonic T clock. `rocket-tx.service` uses `Restart=on-failure`; a clean service stop does not restart it.

## Packet contract

Each LoRa message contains **17 fixed CSV fields**, followed by the raw GGA sentence:

| Index | Field |
|---:|---|
| 0-2 | MPU-6050 accelerometer raw signed integers; RX divides by 2048 for +/-16 g |
| 3-5 | MPU-6050 gyro raw signed integers; RX divides by 16.4 for +/-2000 deg/s |
| 6 | MPU-6050 temperature raw integer; RX applies `raw / 340 + 36.53` |
| 7-9 | HMC5883L raw X/Y/Z |
| 10-13 | BMP280 chip temperature C, pressure hPa, absolute altitude m, pad-relative AGL m; empty when absent. Field 12 is GPS-pad-referenced after lock (`pad GPS altitude + baro AGL`); before lock or if GGA altitude is unavailable it is an uncalibrated 1013.25 hPa standard-atmosphere estimate |
| 14 | phase: `P`, `C`, `F`, or `R` |
| 15 | signed T-clock seconds with one decimal; empty in PAD |
| 16 | decimal `status_flags` bitmask |
| 17 onward | raw `$GNGGA`/`$GPGGA`, including its commas |

Status bits:

| Bit | Value | Meaning |
|---:|---:|---|
| 0 | 1 | `IMU_AVAILABLE` (MPU accelerometer/gyro/temperature initialized) |
| 1 | 2 | `MAG_AVAILABLE` |
| 2 | 4 | `BARO_AVAILABLE` |
| 3 | 8 | `GPS_AVAILABLE` (recent GGA received) |
| 4 | 16 | `GPS_LOCKED` (current GGA has a valid fix) |
| 5 | 32 | `CAMERA_AVAILABLE` |
| 6 | 64 | `CAMERA_RECORDING` |
| 7 | 128 | `BARO_GPS_REFERENCED` (this process captured both a fresh pad-pressure mean and a valid pad GPS altitude; field 12 is GPS-referenced whenever present) |

There is no radio-success bit because send success is only known after packet construction. RX radio health uses packet age plus RSSI/SNR. TX and RX must be deployed together for this packet revision.

## Database and displays

`RxTelemetry.db` contains one `telemetry` table with **40 columns**: 39 previous columns plus `status_flags INTEGER`. The idempotent migration adds missing barometer, phase, T-clock, and status columns; old rows receive `NULL`.

The GUI keeps the large phase/T clock at the top, places compact component and radio readouts on the left, and stacks the smaller `NAV` target and `COMPASS` canvases on the right. It is sized for an 800x450 display (minimum 760x430). The target treats MPU-6050 +Z as the upright rocket direction. After pressing **ZERO ATTITUDE**, its center bullseye is the current vertical mounting orientation; concentric rings mark 30, 60, and 90 degrees of X/Y lateral tilt, and the high-contrast dot is clamped to the outer 90-degree ring. Numeric X/Y and total tilt remain visible. The estimate combines accelerometer tilt with gyro integration using a simple complementary filter. It warns when acceleration magnitude is outside 0.75-1.25 g, where acceleration makes tilt unreliable. This is an estimated visualization, not flight-control-grade orientation. Target and compass retain their last values and show `FROZEN` in RECOVERY or `STALE` after link timeout.

The compass uses the existing calibrated X/Y heading and Cocoa declination. Because the final rocket front edge is not chosen and the X-axis magnetometer offset is unknown, it displays `UNCALIBRATED X`. After final mounting, edit `COMPASS_DISPLAY_OFFSET_DEG = 0.0` near the top of `RX_Temenetry.py`; positive degrees rotate only the displayed heading and do not alter decoding or stored data. The GUI always shows the configured offset.

All RX presentation modes use imperial units. Critical live, scrolling, terminal, and playback readouts use two decimal places: current total acceleration and `MAX FLIGHT G`, gyro X/Y/Z, absolute barometric altitude and AGL, pressure inHg, both diagnostic die temperatures, NAV X/Y/total tilt, and compass heading. GPS latitude/longitude retain six decimals; graph axes select compact, readable precision. The acceleration row shows only current total magnitude, `sqrt(gx^2 + gy^2 + gz^2)`, so a stationary payload reads about 1.00 g. `MAX FLIGHT G` resets on the transition into FLIGHT, increases only during FLIGHT, and remains visible through RECOVERY or link staleness; PAD and COUNTDOWN handling never contributes. Raw acceleration X/Y/Z remains unchanged in the packet and SQLite database for playback and analysis. The total-acceleration graph remains in g.

Altitude and BMP temperature use separate rows. `ALTITUDE` shows absolute barometric altitude in feet, AGL in feet, and pressure in inHg. Its source is `GPS REF` only when status bit 128 confirms that this TX process captured both a fresh pad-pressure mean and valid pad GPS altitude; otherwise it is `UNCAL` (the 1013.25 hPa standard-atmosphere estimate). `BMP CHIP TEMP` is the BMP280 diagnostic silicon/die temperature in Fahrenheit, not ambient air temperature.

`MPU DIE TEMP` is likewise the MPU-6050 diagnostic silicon/die temperature, not ambient air. The conversion remains the datasheet formula `raw / 340 + 36.53` C with no cosmetic offset; self-heating and enclosure conditions can make it warmer than room air. Gyro remains deg/s, heading remains degrees, coordinates remain decimal degrees, and RSSI/SNR remain dBm/dB. Wire values and the 40-column SQLite schema remain unchanged. **MPH is not displayed because the current GGA packet contains no speed field.** GPS speed would require a future packet-contract change (for example, adding RMC data).

The GUI header brands the display as **OBSIDIAN-1 TELEMETRY** in the exact Amazon Leo Krypton Purple `#3A1C4E`. Its separate **LIVE** indicator is gray before the first packet and whenever the link is stale, and turns lime only for fresh live-radio telemetry; playback keeps it gray and identifies the window as playback. The adjacent stage begins as `STAGE: --`, then follows PAD/COUNTDOWN/FLIGHT/RECOVERY while the prominent T clock remains on the right.

The left-side rows are ordered ACCELERATION, GYRO, ALTITUDE, GPS, MAG, RADIO, MPU DIE TEMP, and BMP CHIP TEMP, keeping both diagnostic temperatures at the bottom. Set the payload vertical and stationary, then press **ZERO ATTITUDE** to center the nav ball at its current mounting orientation. TX also averages 100 stationary gyro samples during its first second to remove rate bias; do not move the payload during that startup calibration.

GPS distinguishes LOCKED, SEARCHING, and UNAVAILABLE while retaining the last coordinates after lock loss. The radio row shows LIVE/STALE, RSSI/SNR, and receive age. The T clock is never synthesized by RX.

Run live and playback modes on RX after setup:

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui
/home/pi/libs-venv/bin/python3 RX_Temenetry.py
/home/pi/libs-venv/bin/python3 RX_Temenetry.py --scroll
/home/pi/libs-venv/bin/python3 playback.py --list
/home/pi/libs-venv/bin/python3 playback.py --session flight_YYYYMMDD_HHMMSS --gui
/home/pi/libs-venv/bin/python3 playback.py --session flight_YYYYMMDD_HHMMSS --graphs
/home/pi/libs-venv/bin/python3 playback.py --tx-log /path/to/telemetry_YYYY-MM-DD_HH-MM-SS.csv --gui
/home/pi/libs-venv/bin/python3 playback.py --tx-log /path/to/telemetry_YYYY-MM-DD_HH-MM-SS.csv --graphs
/home/pi/libs-venv/bin/python3 playback.py --speed 4 --gui
```

`playback.py --gui` reuses the live RX `LiveState` and dashboard renderer, retains the final values, and shows `PLAYBACK COMPLETE`. RX database rows are reconstructed into the original 17-field payload and decoded through the same decoder as TX CSV payloads. TX logs have no link measurements, so playback shows RSSI/SNR as `n/a`. Existing `--db`, `--session`, `--list`, and `--speed` behavior remains; timing gaps are capped at 10 seconds after applying the speed multiplier.

`--graphs` is a post-flight Tkinter Canvas viewer with selector buttons for barometric AGL (ft), total acceleration (g), gyro magnitude (deg/s), and GPS altitude (ft). It draws one metric at a time for an 800x480-class screen and uniformly decimates to at most 1000 plotted samples. It requires no matplotlib, numpy, radio, or other new dependency.

## Hardware and radio settings

TX I2C bus 1 addresses: MPU-6050 `0x68`, HMC5883L `0x1e`, BMP280 `0x76`. GPS is `/dev/serial0` at 9600 baud. Camera is ov5647 via picamera2. Radio is RFM9x on SPI0 CE1: MOSI physical 19, MISO 21, SCK 23, CS 26/BCM7, RST 22/BCM25, G0 29/BCM5, 3.3 V physical 1, ground 6. Attach the antenna before transmitting.

Both radios use 915 MHz, `ModemConfig.Bw500Cr45Sf128` (SF7, 500 kHz bandwidth, CR4/5), and `acks=False`. TX is node 2 sending to RX node 1 at 15 dBm. RX transmit power is irrelevant because acknowledgements are disabled. Full telemetry targets 7 Hz; the typical 140-150-byte packet has an estimated 58-62 ms airtime (about 60 ms) at this preset, and TX retains the proven 0.5 s packet-sent timeout. Compared with BW125, BW500 trades receiver sensitivity and expected range for higher throughput. Bench delivery is verified, but flight range is not; perform a representative range test before flight.

Both radio ends must always use the exact same modem preset. An older RX or TX copy configured for `Bw125Cr45Sf128` is radio-incompatible with this BW500 configuration and will not exchange telemetry, even though the 17-field packet and 40-column database schemas are unchanged.

## TX setup and manual launch

The TX is already configured. Its venv is `/home/pi/libs-venv`, with system packages visible for picamera2. Required GPS boot configuration is `enable_uart=1` and `dtoverlay=disable-bt` under `[all]` in `/boot/firmware/config.txt`, no `console=serial0,115200` in `/boot/firmware/cmdline.txt`, and disabled `serial-getty@ttyAMA0.service`.

For bench testing, use the **Rocket TX Telemetry** desktop icon. It opens
`start_tx.sh` in a terminal using `/home/pi/libs-venv/bin/python3`. Press Ctrl+C
to stop TX cleanly and finalize the local CSV and any active video.

The boot service is installed and enabled. Before a maintenance boot where TX
must not start, disable it while the current system is running:

```bash
sudo systemctl disable --now rocket-tx.service
```

Re-enable it when ready with `sudo systemctl enable rocket-tx.service`. Do not
start the service without an attached RFM9x and antenna. The LoRa radio is the
one hard startup requirement; all sensors, local logging, and camera fail soft. At every TX process startup, before GPS lock and even when the camera is disabled, it creates one shared run folder:

- `/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/telemetry_YYYY-MM-DD_HH-MM-SS.csv`
- `/home/pi/Desktop/flight_YYYY-MM-DD_HH-MM-SS/flight_YYYY-MM-DD_HH-MM-SS.h264` when camera recording starts

The CSV header is `tx_epoch,tx_iso,tx_monotonic,send_ok,payload`. Python CSV quoting keeps the comma-filled 17-field payload in one column. Every attempted PAD, COUNTDOWN, FLIGHT, and RECOVERY transmission is appended after `lora.send()` with its boolean result; each row is flushed and every tenth row is `fsync`ed. A final flush/fsync occurs on clean shutdown. Log failures print an error and increment `log_err` without stopping radio transmission. No media bytes enter either the CSV payload field or LoRa packet.

To recover missed RX packets, copy the telemetry CSV from TX to the RX Pi (or removable storage), then use `playback.py --tx-log PATH --gui` or `--graphs`. The TX CSV is an independent record of attempted sends, so rows with `send_ok=0` are still available for local post-flight analysis.

Camera recording starts at GPS lock (T-30) and stops/finalizes at T+3 minutes (`TX_CAMERA_STOP_TPLUS_S=180`). It is 1280x720 at 30 fps and 4 Mbps, rotated 180 degrees. The file remains local to the TX; no photo or video is transmitted. The BMP280 62.5 ms standby setting still requires ongoing real-hardware verification.

## RX first-time setup on Buster

The RX currently runs old Raspbian 10/Buster and Python 3.7.3. A supported Raspberry Pi OS upgrade is preferable before flight because Buster is old. Do **not** assume the TX's Trixie/Python 3.13 package instructions apply unchanged.

If retaining Buster, first enable SPI and install its available build/Tk packages. Package names and compatible pyLoraRFM9x/lgpio versions must be confirmed against the configured Buster repositories; do not blindly upgrade the OS immediately before a flight:

```bash
sudo raspi-config                         # enable SPI; optionally rename host rocket-rx
sudo apt update
sudo apt install python3-venv python3-dev python3-tk swig
# Try only if available in the Buster repositories:
apt-cache policy liblgpio-dev
sudo apt install liblgpio-dev
python3 -m venv --system-site-packages /home/pi/libs-venv
/home/pi/libs-venv/bin/pip install pynmea2 pyLoraRFM9x
/home/pi/libs-venv/bin/python3 -c 'import tkinter, pynmea2, pyLoraRFM9x; print("RX imports OK")'
```

If `liblgpio-dev` is unavailable or pyLoraRFM9x will not build for Python 3.7, stop and upgrade/reimage the RX with a current Raspberry Pi OS rather than mixing unsupported repositories. After setup, wire the RX RFM9x using the table above, then create launchers:

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
chmod +x start_rx.sh make_rx_gui_shortcut.sh
cp Rocket-RX.desktop /home/pi/Desktop/
chmod +x /home/pi/Desktop/Rocket-RX.desktop
./make_rx_gui_shortcut.sh
```

## User bench-test checklist

> **Power first:** if the Pi desktop shows a low-voltage warning or
> `vcgencmd get_throttled` has current undervoltage bits set, replace the 5 V
> supply/cable before trusting radio, display, SPI, or SD-card test results.

1. Attach all TX I2C devices, GPS, camera, RFM9x, and antenna; attach and verify the RX RFM9x and antenna.
2. On TX, confirm `i2cdetect -y 1` shows `68`, `1e`, `76`; confirm `/dev/serial0 -> ttyAMA0`; confirm `rpicam-hello --list-cameras` lists ov5647.
3. Complete RX package setup and verify the three RX imports above.
4. Start RX GUI first. Confirm RADIO LINK waits/stales before packets.
5. Start TX manually or via the service. Confirm the sensor rows become ACTIVE and RADIO LINK becomes LIVE with RSSI/SNR.
6. Confirm GPS progresses UNAVAILABLE/SEARCHING to LOCKED and shows coordinates/satellites.
7. Hold three valid GGA fixes: verify COUNTDOWN at T-30, `AGL` near zero, `ABS (GPS REF)` near the captured GPS pad altitude, status bit 128 set, and camera output (if enabled) in the same folder as the already-created telemetry CSV.
8. Confirm current acceleration is near 1.00 g while stationary and `MAX FLIGHT G` is `--` throughout PAD/COUNTDOWN; at T-0 verify it resets and begins tracking FLIGHT-only samples.
9. Verify the CSV contains the exact five-column header, quoted payloads, and increasing PAD/COUNTDOWN rows; temporarily blocking the RX should not stop TX-local logging.
10. Press **ZERO ATTITUDE** while vertical; verify the target dot centers, then tilt toward X/Y and confirm the 30/60/90-degree ring mapping and outer clamp.
11. Verify T-0 changes phase to FLIGHT without interrupting telemetry; at T+3 minutes confirm the TX finalizes the video while telemetry continues.
12. For a short bench test, temporarily set `TX_FLIGHT_DURATION_S` in the service/environment; at expiry verify RECOVERY, GPS-only 1 Hz packets, and frozen last-known sensor values.
13. Stop TX cleanly. Copy its CSV to RX and test both `playback.py --tx-log PATH --gui` and `--graphs`; verify final GUI values remain with `PLAYBACK COMPLETE`, TX link values are n/a, and all four graph metrics render. Also replay the RX SQLite session and compare packet counts.
14. Restore the flight window to 480 seconds before flight.
