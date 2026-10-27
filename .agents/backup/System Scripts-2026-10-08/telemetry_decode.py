#!/usr/bin/env python3
"""
telemetry_decode.py - Decode raw rocket telemetry into engineering units.

The TX (TX_Telemetry.py) sends one comma-separated string per packet:

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp,
    mag_x, mag_y, mag_z, <raw NMEA GPS sentence>

Fields 0..9 are raw signed 16-bit integers straight off the MPU6050 and the
HMC5883L. Field 10 onward is the raw GPS NMEA sentence, which itself contains
commas and begins with '$' (e.g. "$GPGGA,...,*47"). Because of those embedded
commas we split off the first 10 fields and keep the remainder as the GPS text.

Scale factors / calibration are taken directly from the project:
  - Accel  : TX never sets ACCEL_CONFIG, so it stays at the +/-2g default
             -> divide by 16384.0 to get g        (see mpu60501shot.py)
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
ACCEL_SCALE_MODIFIER_2G = 16384.0      # +/-2g default range
GYRO_SCALE_MODIFIER_2000DEG = 16.4     # TX sets GYRO_CONFIG = 24 (+/-2000 deg/s)
GRAVITY_MS2 = 9.80665
DECLINATION = -0.00669                 # from TX_Telemetry.py / Magnetometer.py

# --- Magnetometer calibration (from your compass calibration notes) ------
# Y: raw range -411..76, add 167 to centre -> -244..243
# Z: raw range -333..108, add 112.5 to centre -> -221..220
# X was not characterised, so it carries no offset.
MAG_OFFSET_X = 0.0
MAG_OFFSET_Y = 167.0
MAG_OFFSET_Z = 112.5

# Number of leading numeric sensor fields before the GPS sentence begins.
NUM_SENSOR_FIELDS = 10


class TelemetryDecodeError(ValueError):
    """Raised when a received packet cannot be decoded."""


def _to_int(text):
    """Parse one raw sensor field to int, tolerating stray whitespace."""
    return int(float(text.strip()))


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
    altitude). Anything else, or a parse failure, yields an empty-ish dict so
    the caller still gets a well-formed record.
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
        result.update(
            {
                "timestamp": str(msg.timestamp) if msg.timestamp else None,
                "lat": msg.latitude if msg.lat else None,
                "lat_dir": msg.lat_dir or None,
                "lon": msg.longitude if msg.lon else None,
                "lon_dir": msg.lon_dir or None,
                "altitude": float(msg.altitude) if msg.altitude else None,
                "altitude_units": msg.altitude_units or None,
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
        message = message.decode("utf-8", errors="replace")

    parts = message.split(",")
    if len(parts) < NUM_SENSOR_FIELDS:
        raise TelemetryDecodeError(
            "expected at least {} fields, got {}".format(NUM_SENSOR_FIELDS, len(parts))
        )

    try:
        raw = [_to_int(p) for p in parts[:NUM_SENSOR_FIELDS]]
    except ValueError as exc:
        raise TelemetryDecodeError("bad numeric field: {}".format(exc))

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp_raw, mag_x, mag_y, mag_z = raw

    # Everything after the 10th comma is the GPS sentence (it contains commas).
    gps_sentence = ",".join(parts[NUM_SENSOR_FIELDS:]).strip()

    # --- Convert accelerometer to g and m/s^2 ---
    ax_g = acc_x / ACCEL_SCALE_MODIFIER_2G
    ay_g = acc_y / ACCEL_SCALE_MODIFIER_2G
    az_g = acc_z / ACCEL_SCALE_MODIFIER_2G

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
    else:
        gps_line = "GPS  : no fix"

    return (
        "ACCEL(g): x={accel_g[x]:+.3f} y={accel_g[y]:+.3f} z={accel_g[z]:+.3f}\n"
        "GYRO(dps): x={gyro_dps[x]:+.2f} y={gyro_dps[y]:+.2f} z={gyro_dps[z]:+.2f}\n"
        "TEMP : {temp_c:.1f} C / {temp_f:.1f} F\n"
        "HEAD : {heading_deg} deg\n"
        "{gps_line}"
    ).format(gps_line=gps_line, **data)


if __name__ == "__main__":
    # Quick self-test with a synthetic packet (no hardware needed).
    sample = (
        "1024,-2048,16000,100,-50,25,-520,"
        "30,-120,45,"
        "$GPGGA,123519.00,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    )
    decoded = decode(sample)
    print(format_readout(decoded))
