#!/usr/bin/env python3
# Target board : Raspberry Pi 3 ground station (RX side; pure Python, no hardware access)
# Interpreter  : /home/pi/libs-venv/bin/python3 (see README.md, RX first-time setup)
# Packet source: TX_Telemetry.py on the Pi Zero 2 W (layout documented below; keep identical to the copy in RX_Temenetry.py)
"""
telemetry_decode.py - Decode raw rocket telemetry into engineering units.

The TX (TX_Telemetry.py) sends one comma-separated string per packet:

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp,
    mag_x, mag_y, mag_z,
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m,
    <raw NMEA GGA sentence>

    Idx   Field            On the wire                        Unavailable
    0-2   acc_x..acc_z     raw signed 16-bit (MPU-6050, +/-16 g)     0
    3-5   gyro_x..gyro_z   raw signed 16-bit (+/-2000 deg/s)         0
    6     temp             raw signed 16-bit (MPU-6050 die temp)     0
    7-9   mag_x..mag_z     raw signed 16-bit (HMC5883L)              0
    10    baro_temp_c      "%.2f" degrees C (BMP280)                 empty
    11    baro_press_hpa   "%.2f" absolute pressure, hPa             empty
    12    baro_alt_m       "%.2f" altitude vs 1013.25 hPa            empty
    13    baro_agl_m       "%.2f" altitude vs the pad baseline       empty
    14..  GPS              raw $GNGGA / $GPGGA sentence              empty

Fields 0..9 are raw signed 16-bit integers straight off the MPU-6050 and the
HMC5883L. Fields 10..13 are BMP280 floats already converted on the TX (empty
when the barometer is unavailable). Field 14 onward is the raw GPS NMEA
sentence, which itself contains commas and begins with '$' (e.g.
"$GNGGA,...,*7E"). Because of those embedded commas we split off the first 14
fields and keep the remainder as the GPS text.

Scale factors / calibration are taken directly from the project:
  - Accel  : TX sets ACCEL_CONFIG = 0x18 (+/-16 g)
             -> divide by 2048.0 to get g         (see mpu60501shot.py)
  - Gyro   : TX sets GYRO_CONFIG = 24 (0x18 = +/-2000 deg/s)
             -> divide by 16.4 to get deg/s        (see mpu60501shot.py)
  - Temp   : raw/340 + 36.53 = deg C               (MPU6050 datasheet)
             deg F = deg C * 1.8 + 32              (RX comment in original file)
  - Heading: atan2(y, x) + declination             (Magnetometer.py)
             with the field calibration offsets you measured.
"""

import math

import pynmea2

# --- Scale factors (match TX configuration) ------------------------------
ACCEL_SCALE_MODIFIER_16G = 2048.0      # TX sets ACCEL_CONFIG = 0x18 (+/-16 g)
GYRO_SCALE_MODIFIER_2000DEG = 16.4     # TX sets GYRO_CONFIG = 24 (+/-2000 deg/s)
GRAVITY_MS2 = 9.80665
# Magnetic declination for Cocoa, FL (launch site): about -6.5 deg = -0.113 rad.
# Use 0.0 for bench heading checks. (The ElectronicWings tutorial value, about
# -0.4 deg, was wrong for this site.)
DECLINATION = -0.113

# --- Magnetometer calibration (from your compass calibration notes) ------
# Y: raw range -411..76, add 167 to centre -> -244..243
# Z: raw range -333..108, add 112.5 to centre -> -221..220
# X was not characterised, so it carries no offset.
MAG_OFFSET_X = 0.0
MAG_OFFSET_Y = 167.0
MAG_OFFSET_Z = 112.5

# Number of leading sensor fields before the GPS sentence begins.
NUM_INT_FIELDS = 10        # raw MPU-6050 + HMC5883L integers
NUM_SENSOR_FIELDS = 14     # ints + 4 BMP280 floats


class TelemetryDecodeError(ValueError):
    """Raised when a received packet cannot be decoded."""


def _to_int(text):
    """Parse one raw sensor field to int, tolerating stray whitespace."""
    return int(float(text.strip()))


def _to_float_or_none(text):
    """Parse one baro field: None when empty (sensor unavailable), else float."""
    text = text.strip()
    if not text:
        return None
    return float(text)


def compute_heading(mag_x, mag_y):
    """Return a 0..359 degree compass heading from calibrated X/Y mag values.

    Mirrors the math in Magnetometer.py: atan2(y, x) + declination, wrapped
    into the 0..2*pi range and converted to whole degrees.
    """
    heading = math.atan2(mag_y, mag_x) + DECLINATION
    if heading > 2 * math.pi:
        heading -= 2 * math.pi
    if heading < 0:
        heading += 2 * math.pi
    return int(heading * 180.0 / math.pi)


def _parse_gps(gps_sentence):
    """Parse the raw NMEA text. Returns a dict of GPS fields (may be empty).

    We only act on a GGA sentence (which carries the 3D fix: lat, lon,
    altitude, fix quality, satellites). Anything else, or a parse failure,
    yields an empty-ish dict so the caller still gets a well-formed record.
    gps_qual == 0 means "no fix": the position fields are then forced to None.
    """
    result = {
        "gps_raw": gps_sentence,
        "timestamp": None,
        "lat": None,
        "lat_dir": None,
        "lon": None,
        "lon_dir": None,
        "altitude": None,
        "altitude_units": None,
        "fix_quality": None,
        "num_sats": None,
    }

    if not gps_sentence:
        return result

    # The sentence may arrive with leading junk; locate the GGA record.
    start = gps_sentence.find("$")
    if start > 0:
        gps_sentence = gps_sentence[start:]

    if "GGA" not in gps_sentence:
        return result

    try:
        msg = pynmea2.parse(gps_sentence.strip())
        try:
            fix_quality = int(msg.gps_qual) if msg.gps_qual not in (None, "") else None
        except (TypeError, ValueError):
            fix_quality = None
        try:
            num_sats = int(msg.num_sats) if msg.num_sats not in (None, "") else None
        except (TypeError, ValueError):
            num_sats = None
        has_fix = bool(fix_quality)
        result.update(
            {
                "timestamp": str(msg.timestamp) if msg.timestamp else None,
                "lat": msg.latitude if (has_fix and msg.lat) else None,
                "lat_dir": msg.lat_dir or None,
                "lon": msg.longitude if (has_fix and msg.lon) else None,
                "lon_dir": msg.lon_dir or None,
                "altitude": float(msg.altitude) if (has_fix and msg.altitude) else None,
                "altitude_units": msg.altitude_units or None,
                "fix_quality": fix_quality,
                "num_sats": num_sats,
            }
        )
    except (pynmea2.ParseError, AttributeError, ValueError):
        # Keep the raw text; leave parsed fields as None.
        pass

    return result


def decode(message):
    """Decode a raw TX telemetry string into a dict of engineering values.

    Raises TelemetryDecodeError if the packet is too short or the numeric
    sensor fields cannot be parsed.
    """
    if message is None:
        raise TelemetryDecodeError("empty message")

    if isinstance(message, (bytes, bytearray)):
        message = message.decode("ascii", errors="replace")

    parts = message.split(",")
    if len(parts) < NUM_SENSOR_FIELDS:
        raise TelemetryDecodeError(
            "expected at least {} fields, got {}".format(NUM_SENSOR_FIELDS, len(parts))
        )

    try:
        raw = [_to_int(p) for p in parts[:NUM_INT_FIELDS]]
        baro_vals = [_to_float_or_none(p) for p in parts[NUM_INT_FIELDS:NUM_SENSOR_FIELDS]]
    except ValueError as exc:
        raise TelemetryDecodeError("bad numeric field: {}".format(exc))

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp_raw, mag_x, mag_y, mag_z = raw
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m = baro_vals

    # Everything after the 14th comma is the GPS sentence (it contains commas).
    gps_sentence = ",".join(parts[NUM_SENSOR_FIELDS:]).strip()

    # --- Convert accelerometer to g and m/s^2 ---
    ax_g = acc_x / ACCEL_SCALE_MODIFIER_16G
    ay_g = acc_y / ACCEL_SCALE_MODIFIER_16G
    az_g = acc_z / ACCEL_SCALE_MODIFIER_16G

    # --- Convert gyro to deg/s ---
    gx_dps = gyro_x / GYRO_SCALE_MODIFIER_2000DEG
    gy_dps = gyro_y / GYRO_SCALE_MODIFIER_2000DEG
    gz_dps = gyro_z / GYRO_SCALE_MODIFIER_2000DEG

    # --- Convert temperature ---
    temp_c = (temp_raw / 340.0) + 36.53
    temp_f = temp_c * 1.8 + 32.0

    # --- Apply magnetometer calibration and compute heading ---
    cal_mag_x = mag_x + MAG_OFFSET_X
    cal_mag_y = mag_y + MAG_OFFSET_Y
    cal_mag_z = mag_z + MAG_OFFSET_Z
    heading = compute_heading(cal_mag_x, cal_mag_y)

    gps = _parse_gps(gps_sentence)

    return {
        # Raw values preserved for exact playback / re-processing.
        "raw": {
            "acc": (acc_x, acc_y, acc_z),
            "gyro": (gyro_x, gyro_y, gyro_z),
            "temp": temp_raw,
            "mag": (mag_x, mag_y, mag_z),
        },
        # Converted engineering units.
        "accel_g": {"x": ax_g, "y": ay_g, "z": az_g},
        "accel_ms2": {
            "x": ax_g * GRAVITY_MS2,
            "y": ay_g * GRAVITY_MS2,
            "z": az_g * GRAVITY_MS2,
        },
        "gyro_dps": {"x": gx_dps, "y": gy_dps, "z": gz_dps},
        "temp_c": temp_c,
        "temp_f": temp_f,
        "mag_cal": {"x": cal_mag_x, "y": cal_mag_y, "z": cal_mag_z},
        "heading_deg": heading,
        # BMP280 (already in engineering units on the TX; None if unavailable).
        "baro": {
            "temp_c": baro_temp_c,
            "press_hpa": baro_press_hpa,
            "alt_m": baro_alt_m,
            "agl_m": baro_agl_m,
        },
        "gps": gps,
    }


def format_readout(data):
    """Return a compact one-block human-readable string for live display."""
    g = data["gps"]
    if g["lat"] is not None and g["lon"] is not None:
        gps_line = "GPS  : {:.6f}{} {:.6f}{}  alt {} {}".format(
            g["lat"], g["lat_dir"] or "",
            g["lon"], g["lon_dir"] or "",
            g["altitude"], g["altitude_units"] or "",
        )
        if g.get("num_sats") is not None:
            gps_line += "  sats {}".format(g["num_sats"])
    else:
        gps_line = "GPS  : no fix"
        if g.get("num_sats") is not None:
            gps_line += "  sats {}".format(g["num_sats"])

    b = data["baro"]
    if b["agl_m"] is not None:
        baro_line = "BARO : alt {:.1f} m  AGL {:+.1f} m  {:.1f} hPa  {:.1f} C".format(
            b["alt_m"], b["agl_m"], b["press_hpa"], b["temp_c"])
    else:
        baro_line = "BARO : n/a"

    return (
        "ACCEL(g): x={accel_g[x]:+.3f} y={accel_g[y]:+.3f} z={accel_g[z]:+.3f}\n"
        "GYRO(dps): x={gyro_dps[x]:+.2f} y={gyro_dps[y]:+.2f} z={gyro_dps[z]:+.2f}\n"
        "TEMP : {temp_c:.1f} C / {temp_f:.1f} F\n"
        "HEAD : {heading_deg} deg\n"
        "{baro_line}\n"
        "{gps_line}"
    ).format(gps_line=gps_line, baro_line=baro_line, **data)


if __name__ == "__main__":
    # Quick self-test with a synthetic packet (no hardware needed). The GGA
    # sentence is the real one read on the bench in Cocoa, FL (2026-10-07).
    sample = (
        "0,0,2048,0,0,0,-3552,100,-67,0,"
        "24.81,1011.73,13.21,0.00,"
        "$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E"
    )
    decoded = decode(sample)
    print(format_readout(decoded))
