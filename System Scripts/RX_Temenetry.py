#!/usr/bin/env python3
# Target board : Raspberry Pi 3 ground station (RX side) with an RFM9x LoRa radio on SPI0 CE1
# Interpreter  : /home/pi/libs-venv/bin/python3 (start_rx.sh / make_rx_gui_shortcut.sh use it; see README.md)
# Packet source: TX_Telemetry.py on the Pi Zero 2 W; decoder + schema below are copies of telemetry_decode.py / telemetry_db.py
"""
RX_Temenetry.py - All-in-one ground-station receiver for rocket telemetry.

Run this ONE script at launch time. It does everything:
    LoRa receive  ->  decode to engineering units  ->  store in SQLite
                  ->  show a live telemetry display

Decode and database logic are inlined here so the launch-day receiver has no
local-module dependencies. (telemetry_decode.py / telemetry_db.py still exist
as standalone modules for playback.py and testing; this script duplicates the
small amount of logic it needs so it is self-contained. Keep the copies
identical.)

Displays (pick with a flag; start_rx.sh runs these with /home/pi/libs-venv/bin/python3):
    /home/pi/libs-venv/bin/python3 RX_Temenetry.py            # live terminal dashboard (default)
    /home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui      # Tkinter window (needs Pi desktop + monitor)
    /home/pi/libs-venv/bin/python3 RX_Temenetry.py --scroll   # plain scrolling log (good over flaky SSH)

Other options:
    --db PATH        database file (default: /home/pi/Desktop/RxTelemetry.db)
    --node ADDR      this receiver's LoRa node address (default: 1)

Hardware: runs on the ground-station Raspberry Pi 3 with the RFM9x LoRa radio
(SPI0 CE1, G0 -> GPIO 5, RST -> GPIO 25; same pinout as the TX).
Needs pyLoraRFM9x + pynmea2. pyLoraRFM9x 0.9.x uses RPi.GPIO; 1.0+
uses lgpio. This receiver supports either library API.
"""

import argparse
import copy
import inspect
import math
import sqlite3
import sys
import threading
import time
from datetime import datetime

import pynmea2

# ======================================================================
# DECODE  (inlined from telemetry_decode.py)
# ======================================================================

ACCEL_SCALE_MODIFIER_16G = 2048.0      # TX sets ACCEL_CONFIG = 0x18 (+/-16 g)
GYRO_SCALE_MODIFIER_2000DEG = 16.4     # TX sets GYRO_CONFIG = 24 (+/-2000 deg/s)
GRAVITY_MS2 = 9.80665
FEET_PER_METER = 3.28084
INHG_PER_HPA = 0.0295299830714
# Applied only by the RX display. Set after the rocket's front edge is chosen.
COMPASS_DISPLAY_OFFSET_DEG = 0.0
# Exact Amazon Leo Krypton Purple requested for the GUI brand title.
KRYPTON_PURPLE = "#3A1C4E"
# Magnetic declination for Cocoa, FL (launch site): about -6.5 deg = -0.113 rad.
# Use 0.0 for bench heading checks. (The ElectronicWings tutorial value, about
# -0.4 deg, was wrong for this site.)
DECLINATION = -0.113

# Magnetometer calibration from your compass notes (Y +167, Z +112.5).
MAG_OFFSET_X = 0.0
MAG_OFFSET_Y = 167.0
MAG_OFFSET_Z = 112.5

# Packet layout: 10 raw ints, 4 BMP280 floats, phase letter, T clock,
# decimal status bitmask, then raw GGA. Baro altitude is GPS-pad-referenced
# after lock (standard-atmosphere fallback before lock); BMP temp is chip temp.
NUM_INT_FIELDS = 10
NUM_SENSOR_FIELDS = 17

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
    return int(float(text.strip()))


def _to_float_or_none(text):
    text = text.strip()
    if not text:
        return None
    return float(text)


def _fmt_or_na(value, fmt):
    if value is None:
        return "n/a"
    return fmt.format(value)


def format_t_clock(t):
    # None -> 'T --:--'; countdown rounds up in magnitude (T-00:01 is the last
    # second shown, never T-00:00); T+ truncates; hours digit from 3600 s.
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
    name = PHASE_NAMES.get(phase, "PHASE --")
    if phase == "P":
        return "PAD  T --:--  (waiting for GPS lock)"
    return "{}  {}".format(name, format_t_clock(t))


def compute_heading(mag_x, mag_y):
    heading = math.atan2(mag_y, mag_x) + DECLINATION
    if heading > 2 * math.pi:
        heading -= 2 * math.pi
    if heading < 0:
        heading += 2 * math.pi
    return int(heading * 180.0 / math.pi)


def _parse_gps(gps_sentence):
    result = {
        "gps_raw": gps_sentence, "timestamp": None,
        "lat": None, "lat_dir": None, "lon": None, "lon_dir": None,
        "altitude": None, "altitude_units": None,
        "fix_quality": None, "num_sats": None,
    }
    if not gps_sentence:
        return result

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
        has_fix = bool(fix_quality)       # gps_qual == 0 means no fix
        result.update({
            "timestamp": str(msg.timestamp) if msg.timestamp else None,
            "lat": msg.latitude if (has_fix and msg.lat) else None,
            "lat_dir": msg.lat_dir or None,
            "lon": msg.longitude if (has_fix and msg.lon) else None,
            "lon_dir": msg.lon_dir or None,
            "altitude": float(msg.altitude) if (has_fix and msg.altitude) else None,
            "altitude_units": msg.altitude_units or None,
            "fix_quality": fix_quality,
            "num_sats": num_sats,
        })
    except (pynmea2.ParseError, AttributeError, ValueError):
        pass
    return result


def decode(message):
    if message is None:
        raise TelemetryDecodeError("empty message")
    if isinstance(message, (bytes, bytearray)):
        message = message.decode("ascii", errors="replace")

    parts = message.split(",")
    if len(parts) < NUM_SENSOR_FIELDS:
        raise TelemetryDecodeError(
            "expected at least {} fields, got {}".format(NUM_SENSOR_FIELDS, len(parts)))

    try:
        raw = [_to_int(p) for p in parts[:NUM_INT_FIELDS]]
        baro_vals = [_to_float_or_none(p) for p in parts[NUM_INT_FIELDS:NUM_INT_FIELDS + 4]]
    except ValueError as exc:
        raise TelemetryDecodeError("bad numeric field: {}".format(exc))

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp_raw, mag_x, mag_y, mag_z = raw
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m = baro_vals

    # Phase letter + T clock; the phase check rejects old 14-field packets
    # (their parts[14] is "$GNGGA").
    phase = parts[14].strip()
    if phase not in PHASE_NAMES:
        raise TelemetryDecodeError("bad phase field: {!r}".format(phase))
    try:
        t_clock = _to_float_or_none(parts[15])
        status_flags = _to_int(parts[16])
    except ValueError as exc:
        raise TelemetryDecodeError("bad timeline/status field: {}".format(exc))

    gps_sentence = ",".join(parts[NUM_SENSOR_FIELDS:]).strip()

    ax_g = acc_x / ACCEL_SCALE_MODIFIER_16G
    ay_g = acc_y / ACCEL_SCALE_MODIFIER_16G
    az_g = acc_z / ACCEL_SCALE_MODIFIER_16G

    gx_dps = gyro_x / GYRO_SCALE_MODIFIER_2000DEG
    gy_dps = gyro_y / GYRO_SCALE_MODIFIER_2000DEG
    gz_dps = gyro_z / GYRO_SCALE_MODIFIER_2000DEG

    temp_c = (temp_raw / 340.0) + 36.53
    temp_f = temp_c * 1.8 + 32.0

    cal_mag_x = mag_x + MAG_OFFSET_X
    cal_mag_y = mag_y + MAG_OFFSET_Y
    cal_mag_z = mag_z + MAG_OFFSET_Z
    heading = compute_heading(cal_mag_x, cal_mag_y)

    gps = _parse_gps(gps_sentence)

    return {
        "raw": {
            "acc": (acc_x, acc_y, acc_z),
            "gyro": (gyro_x, gyro_y, gyro_z),
            "temp": temp_raw,
            "mag": (mag_x, mag_y, mag_z),
        },
        "accel_g": {"x": ax_g, "y": ay_g, "z": az_g},
        "accel_ms2": {"x": ax_g * GRAVITY_MS2, "y": ay_g * GRAVITY_MS2, "z": az_g * GRAVITY_MS2},
        "gyro_dps": {"x": gx_dps, "y": gy_dps, "z": gz_dps},
        "temp_c": temp_c, "temp_f": temp_f,
        "mag_cal": {"x": cal_mag_x, "y": cal_mag_y, "z": cal_mag_z},
        "heading_deg": heading,
        "baro": {
            "temp_c": baro_temp_c,
            "press_hpa": baro_press_hpa,
            "alt_m": baro_alt_m,
            "agl_m": baro_agl_m,
        },
        "phase": phase,
        "phase_name": PHASE_NAMES[phase],
        "t_clock": t_clock,
        "status_flags": status_flags,
        "gps": gps,
    }


# ======================================================================
# DATABASE  (inlined from telemetry_db.py)
# ======================================================================

DEFAULT_DB_PATH = "/home/pi/Desktop/RxTelemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    recv_epoch     REAL    NOT NULL,
    recv_iso       TEXT    NOT NULL,
    raw_acc_x INTEGER, raw_acc_y INTEGER, raw_acc_z INTEGER,
    raw_gyro_x INTEGER, raw_gyro_y INTEGER, raw_gyro_z INTEGER,
    raw_temp INTEGER,
    raw_mag_x INTEGER, raw_mag_y INTEGER, raw_mag_z INTEGER,
    accel_g_x REAL, accel_g_y REAL, accel_g_z REAL,
    gyro_dps_x REAL, gyro_dps_y REAL, gyro_dps_z REAL,
    temp_c REAL, temp_f REAL,
    heading_deg INTEGER,
    gps_time TEXT,
    gps_lat REAL, gps_lat_dir TEXT, gps_lon REAL, gps_lon_dir TEXT,
    gps_alt REAL, gps_alt_units TEXT, gps_raw TEXT,
    baro_temp_c REAL, baro_press_hpa REAL, baro_alt_m REAL, baro_agl_m REAL,
    phase TEXT, t_clock REAL, status_flags INTEGER,
    rssi REAL, snr REAL
);
CREATE INDEX IF NOT EXISTS idx_session_time ON telemetry (session_id, recv_epoch);
"""

# Columns added 2026-10-08 (baro, phase/t_clock, then status_flags); added
# idempotently to older databases on open.
_MIGRATED_COLUMNS = (
    ("baro_temp_c", "REAL"),
    ("baro_press_hpa", "REAL"),
    ("baro_alt_m", "REAL"),
    ("baro_agl_m", "REAL"),
    ("phase", "TEXT"),
    ("t_clock", "REAL"),
    ("status_flags", "INTEGER"),
)

_INSERT = """
INSERT INTO telemetry (
    session_id, recv_epoch, recv_iso,
    raw_acc_x, raw_acc_y, raw_acc_z,
    raw_gyro_x, raw_gyro_y, raw_gyro_z, raw_temp,
    raw_mag_x, raw_mag_y, raw_mag_z,
    accel_g_x, accel_g_y, accel_g_z,
    gyro_dps_x, gyro_dps_y, gyro_dps_z,
    temp_c, temp_f, heading_deg,
    gps_time, gps_lat, gps_lat_dir, gps_lon, gps_lon_dir,
    gps_alt, gps_alt_units, gps_raw,
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m,
    phase, t_clock, status_flags,
    rssi, snr
) VALUES (?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?)
"""


class TelemetryDB:
    def __init__(self, path=DEFAULT_DB_PATH, session_id=None):
        self.path = path
        self.session_id = session_id or datetime.now().strftime("flight_%Y%m%d_%H%M%S")
        # check_same_thread=False: pyLoraRFM9x fires on_recv on its own thread.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self):
        """Add any columns missing from an older database (idempotent)."""
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(telemetry)")}
        for col, col_type in _MIGRATED_COLUMNS:
            if col not in existing:
                self.conn.execute("ALTER TABLE telemetry ADD COLUMN {} {}".format(col, col_type))

    def insert(self, d, rssi=None, snr=None):
        now = time.time()
        g = d["gps"]; ag = d["accel_g"]; gd = d["gyro_dps"]; raw = d["raw"]; b = d["baro"]
        self.conn.execute(_INSERT, (
            self.session_id, now, datetime.fromtimestamp(now).isoformat(),
            raw["acc"][0], raw["acc"][1], raw["acc"][2],
            raw["gyro"][0], raw["gyro"][1], raw["gyro"][2], raw["temp"],
            raw["mag"][0], raw["mag"][1], raw["mag"][2],
            ag["x"], ag["y"], ag["z"],
            gd["x"], gd["y"], gd["z"],
            d["temp_c"], d["temp_f"], d["heading_deg"],
            g["timestamp"], g["lat"], g["lat_dir"], g["lon"], g["lon_dir"],
            g["altitude"], g["altitude_units"], g["gps_raw"],
            b["temp_c"], b["press_hpa"], b["alt_m"], b["agl_m"],
            d["phase"], d["t_clock"], d["status_flags"],
            rssi, snr,
        ))
        self.conn.commit()

    def close(self):
        self.conn.close()


# ======================================================================
# SHARED LATEST-STATE  (receiver thread writes, display thread reads)
# ======================================================================

class LiveState:
    """Thread-safe holder for the most recent packet and running stats."""

    def __init__(self):
        self._lock = threading.Lock()
        self.latest = None          # last decoded dict
        self.rssi = None
        self.snr = None
        self.count = 0
        self.errors = 0
        self.last_recv = None       # epoch of last good packet
        self.max_alt = None         # GPS altitude
        self.max_baro_agl = None    # BMP280 altitude above the pad
        self.current_accel_g = None
        self.max_flight_g = None     # reset at entry to FLIGHT; frozen afterward
        self.attitude = None       # complementary-filter pitch/roll estimate
        self.attitude_zero_pitch = 0.0
        self.attitude_zero_roll = 0.0
        self.started = time.time()

    def _update_attitude(self, data, previous_recv):
        """Update the display-only estimate; +Z aligned with gravity is centered."""
        if data["phase"] == "R" or not (data["status_flags"] & IMU_AVAILABLE):
            return
        a = data["accel_g"]
        g = data["gyro_dps"]
        magnitude = math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2)
        if magnitude < 0.01:
            return
        accel_pitch = math.degrees(math.atan2(a["x"], math.sqrt(a["y"] ** 2 + a["z"] ** 2)))
        accel_roll = math.degrees(math.atan2(a["y"], math.sqrt(a["x"] ** 2 + a["z"] ** 2)))
        reliable = 0.75 <= magnitude <= 1.25
        if self.attitude is None or previous_recv is None:
            pitch = accel_pitch
            roll = accel_roll
        else:
            dt = max(0.0, min(time.time() - previous_recv, 1.0))
            predicted_pitch = self.attitude["pitch"] + g["y"] * dt
            predicted_roll = self.attitude["roll"] + g["x"] * dt
            if reliable:
                pitch = 0.98 * predicted_pitch + 0.02 * accel_pitch
                roll = 0.98 * predicted_roll + 0.02 * accel_roll
            else:
                pitch = predicted_pitch
                roll = predicted_roll
        self.attitude = {
            "pitch": max(-180.0, min(180.0, pitch)),
            "roll": max(-180.0, min(180.0, roll)),
            "tilt": math.degrees(math.acos(max(-1.0, min(1.0, a["z"] / magnitude)))),
            "accel_magnitude": magnitude,
            "accel_reliable": reliable,
        }

    def update(self, data, rssi, snr):
        with self._lock:
            previous = self.latest
            previous_recv = self.last_recv
            merged = copy.deepcopy(data)
            flags = data["status_flags"]
            inactive = data["phase"] == "R"
            if previous is not None:
                # Preserve last-known component readings when hardware becomes
                # unavailable or RECOVERY intentionally omits active sensors.
                if inactive or not (flags & IMU_AVAILABLE):
                    merged["raw"]["acc"] = previous["raw"]["acc"]
                    merged["raw"]["gyro"] = previous["raw"]["gyro"]
                    merged["raw"]["temp"] = previous["raw"]["temp"]
                    merged["accel_g"] = previous["accel_g"]
                    merged["accel_ms2"] = previous["accel_ms2"]
                    merged["gyro_dps"] = previous["gyro_dps"]
                    merged["temp_c"] = previous["temp_c"]
                    merged["temp_f"] = previous["temp_f"]
                if inactive or not (flags & MAG_AVAILABLE):
                    merged["raw"]["mag"] = previous["raw"]["mag"]
                    merged["mag_cal"] = previous["mag_cal"]
                    merged["heading_deg"] = previous["heading_deg"]
                for key in ("temp_c", "press_hpa", "alt_m", "agl_m"):
                    if merged["baro"][key] is None:
                        merged["baro"][key] = previous["baro"][key]
                # Current GPS lock/availability comes from status_flags, while
                # coordinates remain the last known position through lock loss.
                for key in ("timestamp", "lat", "lat_dir", "lon", "lon_dir",
                            "altitude", "altitude_units"):
                    if merged["gps"][key] is None:
                        merged["gps"][key] = previous["gps"][key]
            self.latest = merged
            self._update_attitude(merged, previous_recv)
            self.rssi = rssi
            self.snr = snr
            self.count += 1
            self.last_recv = time.time()
            alt = merged["gps"]["altitude"]
            if alt is not None and (self.max_alt is None or alt > self.max_alt):
                self.max_alt = alt
            agl = merged["baro"]["agl_m"]
            if agl is not None and (self.max_baro_agl is None or agl > self.max_baro_agl):
                self.max_baro_agl = agl
            a = merged["accel_g"]
            mag = math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2)
            self.current_accel_g = mag
            # Ground handling and countdown movement must not affect the flight
            # maximum. Reset exactly when this state first enters FLIGHT.
            if data["phase"] == "F" and (previous is None or previous["phase"] != "F"):
                self.max_flight_g = 0.0
            if data["phase"] == "F" and mag > self.max_flight_g:
                self.max_flight_g = mag

    def note_error(self):
        with self._lock:
            self.errors += 1

    def zero_attitude(self):
        """Center the displayed nav ball at the payload's current orientation."""
        with self._lock:
            if self.attitude is None:
                return False
            self.attitude_zero_pitch = self.attitude["pitch"]
            self.attitude_zero_roll = self.attitude["roll"]
            return True

    def snapshot(self):
        with self._lock:
            attitude = copy.deepcopy(self.attitude)
            if attitude is not None:
                attitude["pitch"] -= self.attitude_zero_pitch
                attitude["roll"] -= self.attitude_zero_roll
                attitude["tilt"] = math.sqrt(attitude["pitch"] ** 2 + attitude["roll"] ** 2)
            return {
                "latest": self.latest, "rssi": self.rssi, "snr": self.snr,
                "count": self.count, "errors": self.errors,
                "last_recv": self.last_recv, "max_alt": self.max_alt,
                "max_baro_agl": self.max_baro_agl,
                "current_accel_g": self.current_accel_g,
                "max_flight_g": self.max_flight_g, "attitude": attitude,
                "started": self.started,
            }


# ======================================================================
# DISPLAYS
# ======================================================================

CLEAR = "\033[2J\033[H"   # clear screen + home cursor (ANSI)


def _has_flag(data, bit):
    return bool(data and (data.get("status_flags", 0) & bit))


def _display_heading(heading):
    return (heading + COMPASS_DISPLAY_OFFSET_DEG) % 360.0


def _sensor_state(data, bit):
    if not _has_flag(data, bit):
        return "UNAVAILABLE"
    if data["phase"] == "R" and bit in (IMU_AVAILABLE, MAG_AVAILABLE, BARO_AVAILABLE):
        return "FROZEN (RECOVERY)"
    return "ACTIVE"


def _gps_state(data):
    if not _has_flag(data, GPS_AVAILABLE):
        return "UNAVAILABLE"
    return "LOCKED" if _has_flag(data, GPS_LOCKED) else "SEARCHING"


def _gps_line(gps):
    sats = ""
    if gps and gps.get("num_sats") is not None:
        sats = "   sats {}".format(gps["num_sats"])
    if gps and gps["lat"] is not None and gps["lon"] is not None:
        altitude_ft = "n/a" if gps["altitude"] is None else "{:.1f}".format(
            gps["altitude"] * FEET_PER_METER)
        return "{:.6f}{} {:.6f}{}   alt {} ft{}".format(
            gps["lat"], gps["lat_dir"] or "", gps["lon"], gps["lon_dir"] or "",
            altitude_ft, sats)
    return "no fix" + sats


def _altitude_line(baro, status_flags):
    if not baro or all(baro[k] is None for k in ("press_hpa", "alt_m", "agl_m")):
        return "n/a"
    press_inhg = None if baro["press_hpa"] is None else baro["press_hpa"] * INHG_PER_HPA
    alt_ft = None if baro["alt_m"] is None else baro["alt_m"] * FEET_PER_METER
    agl_ft = None if baro["agl_m"] is None else baro["agl_m"] * FEET_PER_METER
    source = "GPS REF" if status_flags & BARO_GPS_REFERENCED else "UNCAL"
    return "ABS ({}) {} ft | AGL {} ft | {} inHg".format(
        source, _fmt_or_na(alt_ft, "{:.2f}"), _fmt_or_na(agl_ft, "{:+.2f}"),
        _fmt_or_na(press_inhg, "{:.2f}"))


def _bmp_temp_line(baro):
    if not baro or baro["temp_c"] is None:
        return "n/a"
    return "{:.2f} F | diagnostic die temp, not ambient".format(
        baro["temp_c"] * 1.8 + 32.0)


def _fmt_max_agl(snap):
    agl = snap["max_baro_agl"]
    return "{:+.2f} ft".format(agl * FEET_PER_METER) if agl is not None else "--"


def _fmt_max_gps_alt(snap):
    altitude = snap["max_alt"]
    return "{:.1f} ft".format(altitude * FEET_PER_METER) if altitude is not None else "--"


def _fmt_current_g(snap):
    value = snap["current_accel_g"]
    return "{:.2f} g".format(value) if value is not None else "--"


def _fmt_max_flight_g(snap):
    value = snap["max_flight_g"]
    return "{:.2f} g".format(value) if value is not None else "--"


def render_dashboard(snap, session_id):
    """Build the full-screen terminal dashboard string from a state snapshot."""
    d = snap["latest"]
    now = time.time()
    uptime = int(now - snap["started"])
    age = "{:.1f}s".format(now - snap["last_recv"]) if snap["last_recv"] else "--"
    link = "stale" if (snap["last_recv"] and now - snap["last_recv"] > 3) else "live"

    # Phase + T clock exactly as the TX sent them (the last value stays on
    # screen when packets stop; the link line shows "stale").
    if d is None:
        phase_line = "PHASE --  T --:--"
    else:
        phase_line = format_phase_line(d["phase"], d["t_clock"])

    lines = []
    lines.append("=" * 52)
    lines.append(" ROCKET TELEMETRY - LIVE   session: {}".format(session_id))
    lines.append(" >>> {} <<<".format(phase_line))
    lines.append("=" * 52)
    lines.append(" packets:{:<6} errors:{:<4} uptime:{}s".format(
        snap["count"], snap["errors"], uptime))
    lines.append(" link:{:<6} last packet age: {}".format(link, age))
    rssi = snap["rssi"]
    lines.append(" RSSI:{} dBm   SNR:{} dB".format(
        rssi if rssi is not None else "--", snap["snr"] if snap["snr"] is not None else "--"))
    lines.append("-" * 52)

    if d is None:
        lines.append(" waiting for first packet...")
    else:
        a = d["accel_g"]; g = d["gyro_dps"]; raw = d["raw"]; mc = d["mag_cal"]
        lines.append(" ACCELERATION  [{}] current {} | MAX FLIGHT G {}".format(
            _sensor_state(d, IMU_AVAILABLE), _fmt_current_g(snap), _fmt_max_flight_g(snap)))
        lines.append(" GYROSCOPE     [{}] x {:+.2f} y {:+.2f} z {:+.2f} deg/s".format(
            _sensor_state(d, IMU_AVAILABLE), g["x"], g["y"], g["z"]))
        lines.append(" MPU DIE TEMP  [{}] {:.2f} F | diagnostic die temp, not ambient".format(
            _sensor_state(d, IMU_AVAILABLE), d["temp_f"]))
        lines.append(" ALTITUDE      [{}] {}".format(
            _sensor_state(d, BARO_AVAILABLE), _altitude_line(d["baro"], d["status_flags"])))
        lines.append(" BMP CHIP TEMP [{}] {}".format(
            _sensor_state(d, BARO_AVAILABLE), _bmp_temp_line(d["baro"])))
        lines.append(" GPS          [{}] {}".format(_gps_state(d), _gps_line(d["gps"])))
        lines.append(" MAGNETOMETER [{}] heading {:.2f} deg raw {} cal ({:+.1f},{:+.1f},{:+.1f})".format(
            _sensor_state(d, MAG_AVAILABLE), _display_heading(d["heading_deg"]),
            raw["mag"], mc["x"], mc["y"], mc["z"]))
    lines.append("-" * 52)
    lines.append(" MAX GPS alt: {}   MAX baro AGL: {}   MAX FLIGHT G: {}".format(
        _fmt_max_gps_alt(snap), _fmt_max_agl(snap), _fmt_max_flight_g(snap)))
    lines.append("=" * 52)
    lines.append(" Ctrl+C to stop")
    return "\n".join(lines)


def run_terminal_dashboard(state, session_id, stop_event):
    """Refresh an in-place dashboard ~5x/sec until stopped."""
    while not stop_event.is_set():
        snap = state.snapshot()
        sys.stdout.write(CLEAR + render_dashboard(snap, session_id) + "\n")
        sys.stdout.flush()
        time.sleep(0.2)


def run_gui_dashboard(state, session_id, stop_event, mode_title="LIVE", completion_event=None):
    """Low-overhead shared live/playback dashboard for the Pi 3."""
    import tkinter as tk

    root = tk.Tk()
    playback_mode = str(mode_title).upper() == "PLAYBACK"
    window_mode = "PLAYBACK" if playback_mode else "LIVE"
    root.title("Obsidian-1 Telemetry {} - {}".format(window_mode, session_id))
    root.configure(bg="black")
    root.geometry("800x450")
    root.minsize(760, 430)

    header = tk.Frame(root, bg="black")
    header.pack(fill="x", padx=8, pady=(4, 2))
    title_text = "OBSIDIAN-1 TELEMETRY"
    title_font = ("DejaVu Sans Mono", 20, "bold")
    title_probe = tk.Label(header, text=title_text, font=title_font)
    title_probe.update_idletasks()
    title_canvas = tk.Canvas(header, width=title_probe.winfo_reqwidth(),
                             height=title_probe.winfo_reqheight(), bg="black",
                             bd=0, highlightthickness=0)
    title_probe.destroy()
    title_x = 2
    title_y = int(title_canvas.cget("height")) // 2
    for offset_x, offset_y in ((-1, -1), (0, -1), (1, -1),
                               (-1, 0), (1, 0),
                               (-1, 1), (0, 1), (1, 1)):
        title_canvas.create_text(title_x + offset_x, title_y + offset_y,
                                 text=title_text, fill="white", anchor="w",
                                 font=title_font)
    title_canvas.create_text(title_x, title_y, text=title_text,
                             fill=KRYPTON_PURPLE, anchor="w", font=title_font)
    title_canvas.grid(row=0, column=0, sticky="w")
    live_lbl = tk.Label(header, text="LIVE", fg="gray", bg="black",
                        font=("DejaVu Sans Mono", 13, "bold"))
    live_lbl.grid(row=0, column=1, padx=(6, 8))
    phase_lbl = tk.Label(header, text="STAGE: --", fg="cyan", bg="black",
                         font=("DejaVu Sans Mono", 14, "bold"))
    phase_lbl.grid(row=0, column=2, padx=(0, 8))
    tclock_lbl = tk.Label(header, text="T --:--", fg="yellow", bg="black",
                          font=("DejaVu Sans Mono", 30, "bold"))
    tclock_lbl.grid(row=0, column=3, sticky="e")
    header.grid_columnconfigure(0, weight=1)

    body = tk.Frame(root, bg="black")
    body.pack(fill="both", expand=True, padx=6)

    # Component readouts flank the instruments on the left. The attitude ball
    # and compass are stacked in a fixed-width column on the right.
    panel = tk.Frame(body, bg="black", bd=1, relief="solid")
    panel.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
    names = ("ACCELERATION", "GYRO", "ALTITUDE", "GPS", "MAG", "RADIO",
             "MPU DIE TEMP", "BMP CHIP TEMP")
    values = {}
    for row, name in enumerate(names):
        tk.Label(panel, text=name, fg="cyan", bg="black", width=17, anchor="w",
                 font=("DejaVu Sans Mono", 9, "bold")).grid(row=row, column=0, padx=4, pady=5, sticky="nw")
        values[name] = tk.Label(panel, text="waiting...", fg="white", bg="black",
                                anchor="w", justify="left", wraplength=390,
                                font=("DejaVu Sans Mono", 9))
        values[name].grid(row=row, column=1, padx=3, pady=5, sticky="w")

    zero_status_lbl = tk.Label(panel, text="Set payload vertical, then zero",
                               fg="gray", bg="black",
                               font=("DejaVu Sans Mono", 8))
    zero_status_lbl.grid(row=len(names), column=1, padx=3, pady=(3, 5), sticky="w")

    def zero_attitude_display():
        if state.zero_attitude():
            zero_status_lbl.config(text="Attitude display centered", fg="lime")
        else:
            zero_status_lbl.config(text="Waiting for IMU data", fg="yellow")

    tk.Button(panel, text="ZERO ATTITUDE", command=zero_attitude_display,
              font=("DejaVu Sans Mono", 8, "bold"), padx=4, pady=1).grid(
                  row=len(names), column=0, padx=4, pady=(3, 5), sticky="w")

    instruments = tk.Frame(body, bg="black")
    instruments.grid(row=0, column=1, sticky="ne")
    attitude_canvas = tk.Canvas(instruments, width=190, height=165, bg="#07111d",
                                highlightthickness=1, highlightbackground="cyan")
    attitude_canvas.grid(row=0, column=0, pady=(0, 3))
    compass_canvas = tk.Canvas(instruments, width=190, height=165, bg="#07111d",
                               highlightthickness=1, highlightbackground="cyan")
    compass_canvas.grid(row=1, column=0)

    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=0)
    body.grid_rowconfigure(0, weight=1)
    panel.grid_columnconfigure(1, weight=1)

    footer = tk.Frame(root, bg="black")
    footer.pack(fill="x", padx=6, pady=(2, 4))
    gps_lock_canvas = tk.Canvas(footer, width=18, height=18, bg="black",
                                highlightthickness=0)
    gps_lock_canvas.pack(side="left")
    gps_lock_light = gps_lock_canvas.create_oval(3, 3, 15, 15,
                                                  fill="gray", outline="white")
    gps_lock_lbl = tk.Label(footer, text="GPS WAITING | SAT --", fg="gray",
                            bg="black", font=("DejaVu Sans Mono", 9, "bold"))
    gps_lock_lbl.pack(side="left", padx=(3, 12))
    completion_lbl = tk.Label(footer, text="", fg="yellow", bg="black",
                              font=("DejaVu Sans Mono", 9, "bold"))
    completion_lbl.pack(side="left", padx=(0, 8))
    summary_lbl = tk.Label(footer, text="", fg="gray", bg="black",
                           font=("DejaVu Sans Mono", 9), anchor="e")
    summary_lbl.pack(side="right", fill="x", expand=True)

    def draw_attitude(attitude, frozen, stale):
        c = attitude_canvas
        c.delete("all")
        cx, cy, radius = 95.0, 70.0, 50.0
        c.create_text(7, 6, text="NAV", anchor="nw", fill="cyan",
                      font=("DejaVu Sans Mono", 11, "bold"))
        # Linear radial scale: 30, 60, and 90 degrees. The outer ring is
        # the clamp boundary in either lateral axis.
        for degrees, ring_radius in ((30, radius / 3.0),
                                     (60, radius * 2.0 / 3.0),
                                     (90, radius)):
            c.create_oval(cx - ring_radius, cy - ring_radius,
                          cx + ring_radius, cy + ring_radius,
                          outline="#4f7188" if degrees < 90 else "white",
                          width=1 if degrees < 90 else 2)
            c.create_text(cx + ring_radius - 8, cy - 7, text=str(degrees),
                          fill="#8fb4cc", font=("DejaVu Sans Mono", 6))
        c.create_line(cx - radius, cy, cx + radius, cy, fill="#4f7188")
        c.create_line(cx, cy - radius, cx, cy + radius, fill="#4f7188")
        c.create_text(cx + radius + 8, cy, text="X", fill="white",
                      font=("DejaVu Sans Mono", 7, "bold"))
        c.create_text(cx, cy - radius - 7, text="Y", fill="white",
                      font=("DejaVu Sans Mono", 7, "bold"))
        if attitude is None:
            c.create_text(cx, cy, text="NO IMU DATA", fill="gray")
            return
        x_tilt = max(-90.0, min(90.0, attitude["pitch"]))
        y_tilt = max(-90.0, min(90.0, attitude["roll"]))
        dx = x_tilt / 90.0 * radius
        dy = -y_tilt / 90.0 * radius
        distance = math.sqrt(dx * dx + dy * dy)
        if distance > radius:
            scale = radius / distance
            dx *= scale
            dy *= scale
        c.create_oval(cx + dx - 6, cy + dy - 6, cx + dx + 6, cy + dy + 6,
                      fill="yellow", outline="black", width=2)
        status = "STALE" if stale else ("FROZEN" if frozen else "LIVE ESTIMATE")
        status_color = "red" if stale else ("yellow" if frozen else "lime")
        c.create_text(cx, 133, text="X {:+.2f}  Y {:+.2f}  TOTAL {:.2f}°".format(
            attitude["pitch"], attitude["roll"], attitude["tilt"]), fill="white",
            font=("DejaVu Sans Mono", 7))
        quality = status if attitude["accel_reliable"] else "{} | ACCEL UNRELIABLE".format(status)
        c.create_text(cx, 151, text=quality,
                      fill="orange" if not attitude["accel_reliable"] else status_color,
                      font=("DejaVu Sans Mono", 7, "bold"))

    def draw_compass(heading, frozen, stale):
        c = compass_canvas
        c.delete("all")
        cx, cy, radius = 95.0, 70.0, 50.0
        c.create_text(7, 6, text="COMPASS", anchor="nw", fill="cyan",
                      font=("DejaVu Sans Mono", 11, "bold"))
        c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                      outline="white", width=2)
        if heading is None:
            c.create_text(cx, cy, text="NO MAG DATA", fill="gray")
            return
        display_heading = _display_heading(heading)
        for bearing in range(0, 360, 30):
            angle = math.radians(bearing - display_heading - 90.0)
            outer_x = cx + math.cos(angle) * (radius - 2)
            outer_y = cy + math.sin(angle) * (radius - 2)
            inner = radius - (12 if bearing % 90 == 0 else 7)
            c.create_line(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner,
                          outer_x, outer_y, fill="white")
        for bearing, label in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            angle = math.radians(bearing - display_heading - 90.0)
            c.create_text(cx + math.cos(angle) * (radius - 22),
                          cy + math.sin(angle) * (radius - 22), text=label,
                          fill="lime" if label == "N" else "white",
                          font=("DejaVu Sans Mono", 10, "bold"))
        c.create_polygon(cx, cy - radius + 4, cx - 6, cy - radius + 18,
                         cx + 6, cy - radius + 18, fill="red")
        c.create_text(cx, cy, text="{:06.2f} deg".format(display_heading), fill="yellow",
                      font=("DejaVu Sans Mono", 13, "bold"))
        status = "STALE" if stale else ("FROZEN" if frozen else "LIVE")
        color = "red" if stale else ("yellow" if frozen else "lime")
        c.create_text(cx, 133, text="{} | UNCALIBRATED X".format(status), fill=color,
                      font=("DejaVu Sans Mono", 7, "bold"))
        c.create_text(cx, 151, text="OFFSET {:.2f}°".format(COMPASS_DISPLAY_OFFSET_DEG),
                      fill="white", font=("DejaVu Sans Mono", 7))

    def refresh():
        if stop_event.is_set():
            root.destroy()
            return
        snap = state.snapshot()
        d = snap["latest"]
        now = time.time()
        age = now - snap["last_recv"] if snap["last_recv"] else None
        stale = age is not None and age > 3
        frozen = bool(d and d["phase"] == "R")
        gps_status = "WAITING"
        gps_sats = None
        if d is not None:
            phase_lbl.config(text="STAGE: {}".format(PHASE_NAMES.get(d["phase"], "--")))
            tclock_lbl.config(text=format_t_clock(d["t_clock"]))
            a, g, b = d["accel_g"], d["gyro_dps"], d["baro"]
            values["ACCELERATION"].config(
                text="[{}] current {} | MAX FLIGHT G {}".format(
                    _sensor_state(d, IMU_AVAILABLE), _fmt_current_g(snap),
                    _fmt_max_flight_g(snap)))
            values["GYRO"].config(
                text="[{}] X {:+.2f}  Y {:+.2f}  Z {:+.2f} deg/s".format(
                    _sensor_state(d, IMU_AVAILABLE), g["x"], g["y"], g["z"]))
            values["MPU DIE TEMP"].config(
                text="[{}] {:.2f} F | diagnostic, not ambient".format(
                    _sensor_state(d, IMU_AVAILABLE), d["temp_f"]))
            values["ALTITUDE"].config(text="[{}] {}".format(
                _sensor_state(d, BARO_AVAILABLE),
                _altitude_line(b, d["status_flags"])))
            values["BMP CHIP TEMP"].config(text="[{}] {}".format(
                _sensor_state(d, BARO_AVAILABLE), _bmp_temp_line(b)))
            gps_status = _gps_state(d)
            gps_sats = d["gps"].get("num_sats")
            values["GPS"].config(text="[{}] {}".format(gps_status, _gps_line(d["gps"])),
                                 fg="lime" if gps_status == "LOCKED" else
                                    ("yellow" if gps_status == "SEARCHING" else "red"))
            values["MAG"].config(text="[{}] heading {:.2f} deg".format(
                _sensor_state(d, MAG_AVAILABLE), _display_heading(d["heading_deg"])))
            draw_attitude(snap["attitude"], frozen, stale)
            draw_compass(d["heading_deg"], frozen, stale)
        else:
            draw_attitude(None, False, False)
            draw_compass(None, False, False)
        live_is_fresh = bool(not playback_mode and snap["last_recv"] and not stale)
        live_lbl.config(fg="lime" if live_is_fresh else "gray")
        link_state = "STALE" if stale else ("LIVE" if snap["last_recv"] else "WAITING")
        values["RADIO"].config(text="[{}] RSSI {} dBm SNR {} dB age {}".format(
            link_state, snap["rssi"] if snap["rssi"] is not None else "--",
            snap["snr"] if snap["snr"] is not None else "--",
            "--" if age is None else "{:.1f}s".format(age)),
            fg="red" if stale else ("lime" if snap["last_recv"] else "white"))

        lock_colors = {
            "LOCKED": ("lime", "GPS LOCKED"),
            "SEARCHING": ("yellow", "GPS SEARCHING"),
            "UNAVAILABLE": ("red", "GPS UNAVAILABLE"),
            "WAITING": ("gray", "GPS WAITING"),
        }
        lock_color, lock_text = lock_colors.get(gps_status, ("gray", "GPS WAITING"))
        sat_text = "--" if gps_sats is None else str(gps_sats)
        gps_lock_canvas.itemconfig(gps_lock_light, fill=lock_color)
        gps_lock_lbl.config(text="{} | SAT {}".format(lock_text, sat_text), fg=lock_color)
        if completion_event is not None and completion_event.is_set():
            completion_lbl.config(text="PLAYBACK COMPLETE")

        summary_lbl.config(text="packets {} | errors {} | max GPS {} | max AGL {} | MAX FLIGHT G {}".format(
            snap["count"], snap["errors"], _fmt_max_gps_alt(snap),
            _fmt_max_agl(snap), _fmt_max_flight_g(snap)))
        root.after(200, refresh)

    def on_close():
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, refresh)
    root.mainloop()


# ======================================================================
# MAIN
# ======================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="All-in-one rocket telemetry receiver.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="database file path")
    parser.add_argument("--node", type=int, default=1, help="this receiver's LoRa node address")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--gui", action="store_true", help="Tkinter window (needs desktop)")
    group.add_argument("--scroll", action="store_true", help="plain scrolling log")
    args = parser.parse_args(argv)

    # Import the hardware lib here so --help works on any machine.
    # (pyLoraRFM9x >= 1.0 uses lgpio + spidev; no separate GPIO library is needed.)
    from pyLoraRFM9x import LoRa, ModemConfig

    db = TelemetryDB(path=args.db)
    state = LiveState()
    stop_event = threading.Event()
    print("Logging to {} (session: {})".format(db.path, db.session_id))

    def on_recv(payload):
        # Runs on the pyLoraRFM9x interrupt thread, which has no try/except of
        # its own: an uncaught exception here would silently kill reception
        # for the rest of the flight. Nothing below may raise.
        try:
            raw = payload.message.decode("ascii", errors="replace")
            rssi = getattr(payload, "rssi", None)
            snr = getattr(payload, "snr", None)
            try:
                data = decode(raw)
            except TelemetryDecodeError as exc:
                state.note_error()
                if args.scroll:
                    print("[decode error] {}  raw={!r}".format(exc, raw))
                return
            db.insert(data, rssi=rssi, snr=snr)
            state.update(data, rssi, snr)
            if args.scroll:
                g = data["gps"]; b = data["baro"]
                snap = state.snapshot()
                prefix = "[{} {}] ".format(data["phase"], format_t_clock(data["t_clock"]))
                print(prefix + "#{} flags={} current_g={} max_flight_g={}  MPU_DIE_TEMP {:.2f}F  hdg {:.2f}deg  ALTITUDE [{}]  BMP_CHIP_TEMP {}  gps [{}] {}  RSSI {}".format(
                    snap["count"], data["status_flags"], _fmt_current_g(snap),
                    _fmt_max_flight_g(snap), data["temp_f"],
                    _display_heading(data["heading_deg"]),
                    _altitude_line(b, data["status_flags"]), _bmp_temp_line(b),
                    _gps_state(data), _gps_line(g), rssi))
        except Exception as exc:
            state.note_error()
            print("[on_recv error] {!r}".format(exc))

    # Same pins as the TX (Grok_TX_test.py): SPI0 CE1, G0 -> GPIO 5, RST -> GPIO 25.
    # freq / ModemConfig / acks MUST match TX_Telemetry.py. tx_power is irrelevant
    # here (the receiver never transmits; acks=False).
    # pyLoraRFM9x 1.0+ renamed the constructor arguments and added spi_port.
    # Detect the installed API before constructing the object; trying unsupported
    # keywords on 0.9.x creates a partial object whose destructor emits warnings.
    if "spi_channel" in inspect.signature(LoRa.__init__).parameters:
        lora = LoRa(spi_channel=1,
                    interrupt_pin=5,
                    my_address=args.node,
                    spi_port=0,
                    reset_pin=25,
                    freq=915,
                    tx_power=5,
                    modem_config=ModemConfig.Bw500Cr45Sf128,
                    acks=False)
    else:
        print("Using legacy pyLoraRFM9x constructor (0.9.x)")
        lora = LoRa(1, 5, args.node,
                    reset_pin=25,
                    freq=915,
                    tx_power=5,
                    modem_config=ModemConfig.Bw500Cr45Sf128,
                    acks=False)
    lora.on_recv = on_recv
    lora.set_mode_rx()

    print("Receiver ready on node {}. Listening... (Ctrl+C to stop)".format(args.node))

    try:
        if args.gui:
            # Tkinter must own the main thread; it exits when stop_event is set.
            run_gui_dashboard(state, db.session_id, stop_event)
        elif args.scroll:
            while True:
                time.sleep(1)
        else:
            run_terminal_dashboard(state, db.session_id, stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        snap = state.snapshot()
        print("\nStopping. {} packet(s) stored, {} decode error(s).".format(
            snap["count"], snap["errors"]))
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
