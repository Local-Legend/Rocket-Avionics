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
    phase, t_clock, status_flags,
    <raw NMEA GGA sentence>

    Idx   Field            On the wire                        Unavailable
    0-2   acc_x..acc_z     raw signed 16-bit (MPU-6050, +/-16 g)     0
    3-5   gyro_x..gyro_z   raw signed 16-bit (+/-2000 deg/s)         0
    6     temp             raw signed 16-bit (MPU-6050 die temp)     0
    7-9   mag_x..mag_z     raw signed 16-bit (HMC5883L)              0
    10    baro_temp_c      "%.2f" BMP280 chip temperature C          empty
    11    baro_press_hpa   "%.2f" absolute pressure, hPa             empty
    12    baro_alt_m       "%.2f" GPS-referenced altitude after lock empty
    13    baro_agl_m       "%.2f" altitude vs fresh pad baseline     empty (always empty in PAD)
    14    phase            one letter: P / C / F / R                 never empty
    15    t_clock          "%.1f" seconds relative to T-0, signed    empty (in PAD)
    16    status_flags     decimal availability/state bitmask        never empty
    17..  GPS              raw $GNGGA / $GPGGA sentence              empty

Fields 0..9 are raw signed 16-bit integers straight off the MPU-6050 and the
HMC5883L. Fields 10..13 are BMP280 floats already converted on the TX (empty
when the barometer is unavailable; the AGL field is also empty in PAD because
the pad baseline is only captured at GPS lock). Field 14 is the flight phase
letter, field 15 is the T clock, and field 16 is a decimal status bitmask.
`BARO_GPS_REFERENCED` (bit 7/value 128) is set only after this TX process
captures both the fresh pad-pressure mean and a valid pad GPS altitude.
Field 17 onward is the raw GPS NMEA sentence, which itself contains commas.
Because of those embedded commas we split off exactly 17 fixed fields and keep
the remainder as GPS text. In RECOVERY the IMU, magnetometer, and barometer
availability bits continue to report initialized/known hardware, while their
readings are intentionally inactive and omitted/frozen by the live display.

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
FEET_PER_METER = 3.28084
INHG_PER_HPA = 0.0295299830714
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
NUM_SENSOR_FIELDS = 17     # ints + 4 BMP280 floats + phase + t_clock + status

# status_flags bits (kept identical to TX_Telemetry.py and RX_Temenetry.py)
IMU_AVAILABLE = 1 << 0
MAG_AVAILABLE = 1 << 1
BARO_AVAILABLE = 1 << 2
GPS_AVAILABLE = 1 << 3
GPS_LOCKED = 1 << 4
CAMERA_AVAILABLE = 1 << 5
CAMERA_RECORDING = 1 << 6
BARO_GPS_REFERENCED = 1 << 7

# Flight phase letters as sent on the wire (TX_Telemetry.py) -> display names.
PHASE_NAMES = {"P": "PAD", "C": "COUNTDOWN", "F": "FLIGHT", "R": "RECOVERY"}


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


def _fmt_or_na(value, fmt):
    """Format one optional value with fmt (a str.format spec), or 'n/a' if None."""
    if value is None:
        return "n/a"
    return fmt.format(value)


def format_t_clock(t):
    """Return the T clock as text: 'T --:--', 'T-MM:SS', 'T+MM:SS' or 'T+H:MM:SS'.

    Negative values (countdown) are rounded UP in magnitude so that T-00:01 is
    shown for the final second and T-00:00 never appears; positive values are
    truncated so each displayed second lasts exactly one second. From one hour
    onward an hours digit is added (T+1:00:00).
    """
    if t is None:
        return "T --:--"
    if t < 0:
        sign = "-"
        secs = int(math.ceil(-t))
    else:
        sign = "+"
        secs = int(math.floor(t))
    if secs < 3600:
        return "T{}{:02d}:{:02d}".format(sign, secs // 60, secs % 60)
    return "T{}{}:{:02d}:{:02d}".format(sign, secs // 3600, (secs % 3600) // 60, secs % 60)


def format_phase_line(phase, t):
    """Return '<PHASE NAME>  <T clock>' (PAD adds the waiting-for-lock hint)."""
    name = PHASE_NAMES.get(phase, "PHASE --")
    if phase == "P":
        return "PAD  T --:--  (waiting for GPS lock)"
    return "{}  {}".format(name, format_t_clock(t))


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
        baro_vals = [_to_float_or_none(p) for p in parts[NUM_INT_FIELDS:NUM_INT_FIELDS + 4]]
    except ValueError as exc:
        raise TelemetryDecodeError("bad numeric field: {}".format(exc))

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp_raw, mag_x, mag_y, mag_z = raw
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m = baro_vals

    # Flight phase letter and T clock. The phase check also rejects packets
    # from an older 14-field TX: there parts[14] is "$GNGGA", not a letter.
    phase = parts[14].strip()
    if phase not in PHASE_NAMES:
        raise TelemetryDecodeError("bad phase field: {!r}".format(phase))
    try:
        t_clock = _to_float_or_none(parts[15])
        status_flags = _to_int(parts[16])
    except ValueError as exc:
        raise TelemetryDecodeError("bad timeline/status field: {}".format(exc))

    # Everything after the 17th comma is the GPS sentence (it contains commas).
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
        # Flight timeline: phase letter, its display name, and the T clock in
        # seconds relative to T-0 (None until GPS lock).
        "phase": phase,
        "phase_name": PHASE_NAMES[phase],
        "t_clock": t_clock,
        "status_flags": status_flags,
        "gps": gps,
    }


def format_readout(data):
    """Return a compact one-block human-readable string for live display."""
    g = data["gps"]
    if g["lat"] is not None and g["lon"] is not None:
        altitude_ft = "n/a" if g["altitude"] is None else "{:.1f}".format(
            g["altitude"] * FEET_PER_METER)
        gps_line = "GPS  : {:.6f}{} {:.6f}{}  alt {} ft".format(
            g["lat"], g["lat_dir"] or "",
            g["lon"], g["lon_dir"] or "", altitude_ft,
        )
        if g.get("num_sats") is not None:
            gps_line += "  sats {}".format(g["num_sats"])
    else:
        gps_line = "GPS  : no fix"
        if g.get("num_sats") is not None:
            gps_line += "  sats {}".format(g["num_sats"])

    # Keep altitude and BMP die temperature separate for readable live/playback
    # output. The explicit TX status bit—not flight phase—identifies the source.
    b = data["baro"]
    if all(b[k] is None for k in ("press_hpa", "alt_m", "agl_m")):
        altitude_line = "ALTITUDE     : n/a"
    else:
        alt_ft = None if b["alt_m"] is None else b["alt_m"] * FEET_PER_METER
        agl_ft = None if b["agl_m"] is None else b["agl_m"] * FEET_PER_METER
        press_inhg = None if b["press_hpa"] is None else b["press_hpa"] * INHG_PER_HPA
        source = "GPS REF" if data["status_flags"] & BARO_GPS_REFERENCED else "UNCAL"
        altitude_line = "ALTITUDE     : ABS ({}) {} ft  AGL {} ft  {} inHg".format(
            source, _fmt_or_na(alt_ft, "{:.2f}"), _fmt_or_na(agl_ft, "{:+.2f}"),
            _fmt_or_na(press_inhg, "{:.2f}"))
    if b["temp_c"] is None:
        bmp_temp_line = "BMP CHIP TEMP: n/a"
    else:
        bmp_temp_line = "BMP CHIP TEMP: {:.2f} F (diagnostic die temp, not ambient)".format(
            b["temp_c"] * 1.8 + 32.0)

    phase_line = format_phase_line(data["phase"], data["t_clock"])
    a = data["accel_g"]
    total_g = math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2)

    return (
        "{phase_line}\n"
        "ACCELERATION: current {total_g:.2f} g (1.00 g at rest)\n"
        "GYRO(deg/s): x={gyro_dps[x]:+.2f} y={gyro_dps[y]:+.2f} z={gyro_dps[z]:+.2f}\n"
        "MPU DIE TEMP: {temp_f:.2f} F (diagnostic die temp, not ambient)\n"
        "HEAD : {heading_deg:.2f} deg\n"
        "{altitude_line}\n"
        "{bmp_temp_line}\n"
        "{gps_line}"
    ).format(phase_line=phase_line, gps_line=gps_line,
             altitude_line=altitude_line, bmp_temp_line=bmp_temp_line,
             total_g=total_g, **data)


if __name__ == "__main__":
    # Quick self-test with a synthetic COUNTDOWN packet (no hardware needed).
    # The GGA sentence is the real one read on the bench in Cocoa, FL (2026-10-07).
    sample = (
        "0,0,2048,0,0,0,-3552,100,-67,0,"
        "24.81,1011.73,13.21,0.00,"
        "C,-12.3,255,"
        "$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E"
    )
    decoded = decode(sample)
    print(format_readout(decoded))
