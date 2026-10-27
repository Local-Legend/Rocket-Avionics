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
Needs pyLoraRFM9x + pynmea2 only (no separate GPIO library: pyLoraRFM9x uses lgpio).
"""

import argparse
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
# Magnetic declination for Cocoa, FL (launch site): about -6.5 deg = -0.113 rad.
# Use 0.0 for bench heading checks. (The ElectronicWings tutorial value, about
# -0.4 deg, was wrong for this site.)
DECLINATION = -0.113

# Magnetometer calibration from your compass notes (Y +167, Z +112.5).
MAG_OFFSET_X = 0.0
MAG_OFFSET_Y = 167.0
MAG_OFFSET_Z = 112.5

# Packet layout (see telemetry_decode.py): 10 raw ints, 4 BMP280 floats, then
# the raw GGA sentence (which contains commas).
NUM_INT_FIELDS = 10
NUM_SENSOR_FIELDS = 14


class TelemetryDecodeError(ValueError):
    """Raised when a received packet cannot be decoded."""


def _to_int(text):
    return int(float(text.strip()))


def _to_float_or_none(text):
    text = text.strip()
    if not text:
        return None
    return float(text)


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
        baro_vals = [_to_float_or_none(p) for p in parts[NUM_INT_FIELDS:NUM_SENSOR_FIELDS]]
    except ValueError as exc:
        raise TelemetryDecodeError("bad numeric field: {}".format(exc))

    acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp_raw, mag_x, mag_y, mag_z = raw
    baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m = baro_vals
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
    rssi REAL, snr REAL
);
CREATE INDEX IF NOT EXISTS idx_session_time ON telemetry (session_id, recv_epoch);
"""

# Columns added 2026-10-08; added to older databases on open (migration).
_BARO_COLUMNS = ("baro_temp_c", "baro_press_hpa", "baro_alt_m", "baro_agl_m")

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
    rssi, snr
) VALUES (?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?)
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
        for col in _BARO_COLUMNS:
            if col not in existing:
                self.conn.execute("ALTER TABLE telemetry ADD COLUMN {} REAL".format(col))

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
        self.max_accel_g = 0.0
        self.started = time.time()

    def update(self, data, rssi, snr):
        with self._lock:
            self.latest = data
            self.rssi = rssi
            self.snr = snr
            self.count += 1
            self.last_recv = time.time()
            alt = data["gps"]["altitude"]
            if alt is not None and (self.max_alt is None or alt > self.max_alt):
                self.max_alt = alt
            agl = data["baro"]["agl_m"]
            if agl is not None and (self.max_baro_agl is None or agl > self.max_baro_agl):
                self.max_baro_agl = agl
            a = data["accel_g"]
            mag = math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2)
            if mag > self.max_accel_g:
                self.max_accel_g = mag

    def note_error(self):
        with self._lock:
            self.errors += 1

    def snapshot(self):
        with self._lock:
            return {
                "latest": self.latest, "rssi": self.rssi, "snr": self.snr,
                "count": self.count, "errors": self.errors,
                "last_recv": self.last_recv, "max_alt": self.max_alt,
                "max_baro_agl": self.max_baro_agl,
                "max_accel_g": self.max_accel_g, "started": self.started,
            }


# ======================================================================
# DISPLAYS
# ======================================================================

CLEAR = "\033[2J\033[H"   # clear screen + home cursor (ANSI)


def _gps_line(gps):
    sats = ""
    if gps and gps.get("num_sats") is not None:
        sats = "   sats {}".format(gps["num_sats"])
    if gps and gps["lat"] is not None and gps["lon"] is not None:
        return "{:.6f}{} {:.6f}{}   alt {} {}{}".format(
            gps["lat"], gps["lat_dir"] or "", gps["lon"], gps["lon_dir"] or "",
            gps["altitude"], gps["altitude_units"] or "", sats)
    return "no fix" + sats


def _baro_line(baro):
    if baro and baro["agl_m"] is not None:
        return "alt {:.1f} m  AGL {:+.1f} m  {:.1f} hPa  {:.1f} C".format(
            baro["alt_m"], baro["agl_m"], baro["press_hpa"], baro["temp_c"])
    return "n/a"


def _fmt_max_agl(snap):
    agl = snap["max_baro_agl"]
    return "{:+.1f} m".format(agl) if agl is not None else "--"


def render_dashboard(snap, session_id):
    """Build the full-screen terminal dashboard string from a state snapshot."""
    d = snap["latest"]
    now = time.time()
    uptime = int(now - snap["started"])
    age = "{:.1f}s".format(now - snap["last_recv"]) if snap["last_recv"] else "--"
    link = "stale" if (snap["last_recv"] and now - snap["last_recv"] > 3) else "live"

    lines = []
    lines.append("=" * 52)
    lines.append(" ROCKET TELEMETRY - LIVE   session: {}".format(session_id))
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
        a = d["accel_g"]; g = d["gyro_dps"]
        lines.append(" ACCEL (g)   x {:+7.3f}  y {:+7.3f}  z {:+7.3f}".format(a["x"], a["y"], a["z"]))
        lines.append(" GYRO (dps)  x {:+7.2f}  y {:+7.2f}  z {:+7.2f}".format(g["x"], g["y"], g["z"]))
        lines.append(" TEMP        {:.1f} C   /   {:.1f} F".format(d["temp_c"], d["temp_f"]))
        lines.append(" HEADING     {} deg".format(d["heading_deg"]))
        lines.append(" BARO        {}".format(_baro_line(d["baro"])))
        lines.append(" GPS         {}".format(_gps_line(d["gps"])))
    lines.append("-" * 52)
    lines.append(" MAX alt: {}   MAX baro AGL: {}   MAX |accel|: {:.2f} g".format(
        snap["max_alt"] if snap["max_alt"] is not None else "--",
        _fmt_max_agl(snap), snap["max_accel_g"]))
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


def run_gui_dashboard(state, session_id, stop_event):
    """Simple Tkinter window. Needs the Pi desktop + an attached monitor."""
    import tkinter as tk

    root = tk.Tk()
    root.title("Rocket Telemetry - {}".format(session_id))
    root.configure(bg="black")
    font = ("DejaVu Sans Mono", 16)

    header = tk.Label(root, text="ROCKET TELEMETRY - LIVE", fg="lime", bg="black",
                      font=("DejaVu Sans Mono", 18, "bold"))
    header.pack(pady=(10, 4))

    body = tk.Label(root, text="waiting for first packet...", fg="white", bg="black",
                    font=font, justify="left", anchor="w")
    body.pack(padx=16, pady=10, fill="both", expand=True)

    link = tk.Label(root, text="", fg="gray", bg="black", font=("DejaVu Sans Mono", 12))
    link.pack(pady=(0, 10))

    def refresh():
        if stop_event.is_set():
            root.destroy()
            return
        snap = state.snapshot()
        d = snap["latest"]
        now = time.time()
        if d is None:
            body.config(text="waiting for first packet...")
        else:
            a = d["accel_g"]; g = d["gyro_dps"]
            body.config(text=(
                "ACCEL (g)   x {:+7.3f}  y {:+7.3f}  z {:+7.3f}\n"
                "GYRO (dps)  x {:+7.2f}  y {:+7.2f}  z {:+7.2f}\n"
                "TEMP        {:.1f} C  /  {:.1f} F\n"
                "HEADING     {} deg\n"
                "BARO        {}\n"
                "GPS         {}\n\n"
                "MAX alt {}    MAX baro AGL {}    MAX |accel| {:.2f} g"
            ).format(a["x"], a["y"], a["z"], g["x"], g["y"], g["z"],
                     d["temp_c"], d["temp_f"], d["heading_deg"],
                     _baro_line(d["baro"]), _gps_line(d["gps"]),
                     snap["max_alt"] if snap["max_alt"] is not None else "--",
                     _fmt_max_agl(snap), snap["max_accel_g"]))
        stale = snap["last_recv"] and now - snap["last_recv"] > 3
        link.config(
            text="packets {}  errors {}  RSSI {} dBm  SNR {} dB  [{}]".format(
                snap["count"], snap["errors"],
                snap["rssi"] if snap["rssi"] is not None else "--",
                snap["snr"] if snap["snr"] is not None else "--",
                "STALE" if stale else "live"),
            fg="red" if stale else "gray")
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
                agl = "{:+.1f}m".format(b["agl_m"]) if b["agl_m"] is not None else "n/a"
                print("#{} acc(g) {:+.2f},{:+.2f},{:+.2f}  temp {:.1f}C  hdg {}  agl {}  gps {}  RSSI {}".format(
                    state.snapshot()["count"],
                    data["accel_g"]["x"], data["accel_g"]["y"], data["accel_g"]["z"],
                    data["temp_c"], data["heading_deg"], agl, _gps_line(g), rssi))
        except Exception as exc:
            state.note_error()
            print("[on_recv error] {!r}".format(exc))

    # Same pins as the TX (Grok_TX_test.py): SPI0 CE1, G0 -> GPIO 5, RST -> GPIO 25.
    # freq / ModemConfig / acks MUST match TX_Telemetry.py. tx_power is irrelevant
    # here (the receiver never transmits; acks=False).
    lora = LoRa(spi_channel=1,
                interrupt_pin=5,
                my_address=args.node,
                spi_port=0,
                reset_pin=25,
                freq=915,
                tx_power=5,
                modem_config=ModemConfig.Bw125Cr45Sf128,
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
