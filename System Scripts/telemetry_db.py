#!/usr/bin/env python3
# Target board : Raspberry Pi 3 ground station (RX side; pure Python, no hardware access)
# Interpreter  : /home/pi/libs-venv/bin/python3 (see README.md, RX first-time setup)
# Schema       : keep identical to the inlined copy in RX_Temenetry.py
"""
telemetry_db.py - SQLite storage for received rocket telemetry.

One flat table keyed by an auto-increment id. Each row is a single received
packet carrying both the raw sensor integers and the converted engineering
units, tagged with a session id (one flight = one session) and a wall-clock
timestamp. This layout lets several flights live in one database and lets the
playback tool replay a chosen session in order and at the original cadence.

This replaces the original multi-table schema (ACL/GYR/TMP/MAG/GPS), which had
no timestamps or keys to correlate rows across tables.

2026-10-08: four BMP280 columns were added (baro_temp_c, baro_press_hpa,
baro_alt_m, baro_agl_m), then the flight timeline columns phase (TEXT, one of
P/C/F/R), t_clock (REAL, seconds relative to T-0, NULL in PAD), and
status_flags (INTEGER component-state bitmask). A fresh database has 40
columns. Older databases are migrated in place on open (missing columns are
appended with their declared type; old rows read as NULL in them).
"""

import sqlite3
import time
from datetime import datetime

DEFAULT_DB_PATH = "/home/pi/Desktop/RxTelemetry.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    recv_epoch     REAL    NOT NULL,   -- time.time() when received
    recv_iso       TEXT    NOT NULL,   -- human-readable receive time

    -- raw sensor integers (exactly as transmitted)
    raw_acc_x      INTEGER, raw_acc_y INTEGER, raw_acc_z INTEGER,
    raw_gyro_x     INTEGER, raw_gyro_y INTEGER, raw_gyro_z INTEGER,
    raw_temp       INTEGER,
    raw_mag_x      INTEGER, raw_mag_y INTEGER, raw_mag_z INTEGER,

    -- converted engineering units
    accel_g_x      REAL, accel_g_y REAL, accel_g_z REAL,
    gyro_dps_x     REAL, gyro_dps_y REAL, gyro_dps_z REAL,
    temp_c         REAL, temp_f REAL,
    heading_deg    INTEGER,

    -- GPS fix
    gps_time       TEXT,
    gps_lat        REAL, gps_lat_dir TEXT,
    gps_lon        REAL, gps_lon_dir TEXT,
    gps_alt        REAL, gps_alt_units TEXT,
    gps_raw        TEXT,

    -- barometer (BMP280); NULL when the TX reported it unavailable
    baro_temp_c    REAL,
    baro_press_hpa REAL,
    baro_alt_m     REAL, -- GPS-pad-referenced after lock; standard-atmosphere fallback
    baro_agl_m     REAL, -- pressure-relative to fresh pad baseline

    -- flight timeline and explicit component state
    phase          TEXT,
    t_clock        REAL,
    status_flags   INTEGER,

    -- link quality from the LoRa radio
    rssi           REAL,
    snr            REAL
);
CREATE INDEX IF NOT EXISTS idx_session_time
    ON telemetry (session_id, recv_epoch);
"""

# Columns added after the first release; appended idempotently to older
# databases on open.
MIGRATED_COLUMNS = (
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
    raw_gyro_x, raw_gyro_y, raw_gyro_z,
    raw_temp,
    raw_mag_x, raw_mag_y, raw_mag_z,
    accel_g_x, accel_g_y, accel_g_z,
    gyro_dps_x, gyro_dps_y, gyro_dps_z,
    temp_c, temp_f,
    heading_deg,
    gps_time, gps_lat, gps_lat_dir, gps_lon, gps_lon_dir,
    gps_alt, gps_alt_units, gps_raw,
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m,
    phase, t_clock, status_flags,
    rssi, snr
) VALUES (?,?,?, ?,?,?, ?,?,?, ?, ?,?,?, ?,?,?, ?,?,?, ?,?, ?, ?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?)
"""


class TelemetryDB:
    """Thin wrapper around a SQLite connection for telemetry rows."""

    def __init__(self, path=DEFAULT_DB_PATH, session_id=None):
        self.path = path
        self.session_id = session_id or datetime.now().strftime("flight_%Y%m%d_%H%M%S")
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self):
        """Add any columns missing from an older database (idempotent)."""
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(telemetry)")}
        for col, col_type in MIGRATED_COLUMNS:
            if col not in existing:
                self.conn.execute("ALTER TABLE telemetry ADD COLUMN {} {}".format(col, col_type))

    def insert(self, decoded, rssi=None, snr=None):
        """Insert one decoded telemetry dict (from telemetry_decode.decode)."""
        now = time.time()
        g = decoded["gps"]
        ag = decoded["accel_g"]
        gd = decoded["gyro_dps"]
        raw = decoded["raw"]
        b = decoded["baro"]

        self.conn.execute(
            _INSERT,
            (
                self.session_id, now, datetime.fromtimestamp(now).isoformat(),
                raw["acc"][0], raw["acc"][1], raw["acc"][2],
                raw["gyro"][0], raw["gyro"][1], raw["gyro"][2],
                raw["temp"],
                raw["mag"][0], raw["mag"][1], raw["mag"][2],
                ag["x"], ag["y"], ag["z"],
                gd["x"], gd["y"], gd["z"],
                decoded["temp_c"], decoded["temp_f"],
                decoded["heading_deg"],
                g["timestamp"], g["lat"], g["lat_dir"], g["lon"], g["lon_dir"],
                g["altitude"], g["altitude_units"], g["gps_raw"],
                b["temp_c"], b["press_hpa"], b["alt_m"], b["agl_m"],
                decoded["phase"], decoded["t_clock"], decoded["status_flags"],
                rssi, snr,
            ),
        )
        self.conn.commit()

    def sessions(self):
        """Return a list of (session_id, row_count, first_iso, last_iso)."""
        cur = self.conn.execute(
            "SELECT session_id, COUNT(*) AS n, MIN(recv_iso) AS first, MAX(recv_iso) AS last "
            "FROM telemetry GROUP BY session_id ORDER BY MIN(recv_epoch)"
        )
        return [(r["session_id"], r["n"], r["first"], r["last"]) for r in cur.fetchall()]

    def rows_for_session(self, session_id):
        """Yield all rows for a session in receive order."""
        cur = self.conn.execute(
            "SELECT * FROM telemetry WHERE session_id = ? ORDER BY recv_epoch",
            (session_id,),
        )
        for r in cur.fetchall():
            yield r

    def latest_session(self):
        """Return the most recently recorded session_id, or None if empty."""
        cur = self.conn.execute(
            "SELECT session_id FROM telemetry ORDER BY recv_epoch DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row["session_id"] if row else None

    def close(self):
        self.conn.close()
