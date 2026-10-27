#!/usr/bin/env python3
# Target board : Raspberry Pi 3 ground station (RX side; pure Python, no hardware access)
# Interpreter  : /home/pi/libs-venv/bin/python3 (see README.md, RX first-time setup)
# Depends on   : telemetry_db.py in the same folder (run from System Scripts/)
"""
playback.py - Replay recorded rocket telemetry from the SQLite database.

Reads a stored session back in receive order and prints each packet, pausing
between packets to reproduce the original timing (the gaps between recv_epoch
values). Useful for post-flight review without any radio hardware attached.

Usage (from the System Scripts folder):
    /home/pi/libs-venv/bin/python3 playback.py                  # replay the most recent session
    /home/pi/libs-venv/bin/python3 playback.py --list           # list all recorded sessions
    /home/pi/libs-venv/bin/python3 playback.py --session SID    # replay a specific session
    /home/pi/libs-venv/bin/python3 playback.py --speed 4        # 4x faster than real time
    /home/pi/libs-venv/bin/python3 playback.py --speed 0        # no delay (dump as fast as possible)
    /home/pi/libs-venv/bin/python3 playback.py --db /path/to.db # use a non-default database

This script has no hardware dependency (only the standard library and
telemetry_db.py), so it runs on any machine that has the database file.
"""

import argparse
import sys
import time

from telemetry_db import TelemetryDB, DEFAULT_DB_PATH


def _fmt_gps(row):
    if row["gps_lat"] is not None and row["gps_lon"] is not None:
        return "GPS  : {:.6f}{} {:.6f}{}  alt {} {}".format(
            row["gps_lat"], row["gps_lat_dir"] or "",
            row["gps_lon"], row["gps_lon_dir"] or "",
            row["gps_alt"], row["gps_alt_units"] or "",
        )
    return "GPS  : no fix"


def _has_baro(row):
    """True when this row carries BMP280 data (column present and not NULL)."""
    return "baro_agl_m" in row.keys() and row["baro_agl_m"] is not None


def _print_row(row, index):
    print("\n--- packet #{}  @ {} -----------------------".format(index, row["recv_iso"]))
    print("ACCEL(g): x={:+.3f} y={:+.3f} z={:+.3f}".format(
        row["accel_g_x"], row["accel_g_y"], row["accel_g_z"]))
    print("GYRO(dps): x={:+.2f} y={:+.2f} z={:+.2f}".format(
        row["gyro_dps_x"], row["gyro_dps_y"], row["gyro_dps_z"]))
    print("TEMP : {:.1f} C / {:.1f} F".format(row["temp_c"], row["temp_f"]))
    print("HEAD : {} deg".format(row["heading_deg"]))
    if _has_baro(row):
        print("BARO : alt {:.1f} m  AGL {:+.1f} m  {:.1f} hPa  {:.1f} C".format(
            row["baro_alt_m"], row["baro_agl_m"], row["baro_press_hpa"], row["baro_temp_c"]))
    print(_fmt_gps(row))
    if row["rssi"] is not None:
        print("LINK : RSSI {} dBm  SNR {} dB".format(row["rssi"], row["snr"]))


def list_sessions(db):
    sessions = db.sessions()
    if not sessions:
        print("No sessions recorded in {}.".format(db.path))
        return
    print("Recorded sessions in {}:".format(db.path))
    print("{:<28} {:>6}  {}  ->  {}".format("SESSION", "PKTS", "FIRST", "LAST"))
    for sid, n, first, last in sessions:
        print("{:<28} {:>6}  {}  ->  {}".format(sid, n, first, last))


def replay(db, session_id, speed):
    rows = list(db.rows_for_session(session_id))
    if not rows:
        print("No data for session '{}'.".format(session_id))
        return

    print("Replaying session '{}' ({} packets, speed x{}).".format(
        session_id, len(rows), speed if speed else "max"))

    prev_epoch = None
    max_agl = None
    for i, row in enumerate(rows, start=1):
        if prev_epoch is not None and speed and speed > 0:
            gap = (row["recv_epoch"] - prev_epoch) / speed
            if gap > 0:
                time.sleep(min(gap, 10.0))  # cap pauses so dropouts don't stall
        prev_epoch = row["recv_epoch"]
        if _has_baro(row) and (max_agl is None or row["baro_agl_m"] > max_agl):
            max_agl = row["baro_agl_m"]
        _print_row(row, i)

    if max_agl is not None:
        print("\nSession max baro AGL: {:+.1f} m".format(max_agl))
    print("\nEnd of session '{}'.".format(session_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay recorded rocket telemetry.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="path to the telemetry database")
    parser.add_argument("--session", help="session id to replay (default: most recent)")
    parser.add_argument("--list", action="store_true", help="list sessions and exit")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="playback speed multiplier (0 = no delay)")
    args = parser.parse_args(argv)

    db = TelemetryDB(path=args.db)
    try:
        if args.list:
            list_sessions(db)
            return 0

        session_id = args.session or db.latest_session()
        if session_id is None:
            print("Database is empty. Nothing to replay.")
            return 1

        replay(db, session_id, args.speed)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
