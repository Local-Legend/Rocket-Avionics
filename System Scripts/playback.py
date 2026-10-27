#!/usr/bin/env python3
# Target board : Raspberry Pi Zero 2 W (TX-local CSV) or Pi 3 ground station (RX DB)
# Interpreter  : /home/pi/libs-venv/bin/python3
"""Replay RX SQLite sessions or TX-local CSV logs.

Examples:
    /home/pi/libs-venv/bin/python3 playback.py --gui
    /home/pi/libs-venv/bin/python3 playback.py --graphs --session SID
    /home/pi/libs-venv/bin/python3 playback.py --tx-log /path/telemetry_STAMP.csv --gui
    /home/pi/libs-venv/bin/python3 playback.py --tx-log /path/telemetry_STAMP.csv --graphs
"""

import argparse
import csv
import math
import sys
import threading
import time

from telemetry_db import TelemetryDB, DEFAULT_DB_PATH
from telemetry_decode import decode, format_readout, TelemetryDecodeError
from RX_Temenetry import LiveState, run_gui_dashboard

MAX_GAP_S = 10.0
MAX_GRAPH_POINTS = 1000
FEET_PER_METER = 3.28084


def _value(row, name, default=None):
    return row[name] if name in row.keys() and row[name] is not None else default


def _fmt_float(value):
    return "" if value is None else "{:.2f}".format(value)


def payload_from_db_row(row):
    """Reconstruct the original 17-field packet from one SQLite row."""
    raw_names = (
        "raw_acc_x", "raw_acc_y", "raw_acc_z",
        "raw_gyro_x", "raw_gyro_y", "raw_gyro_z", "raw_temp",
        "raw_mag_x", "raw_mag_y", "raw_mag_z",
    )
    fields = [str(_value(row, name, 0)) for name in raw_names]
    fields.extend(_fmt_float(_value(row, name)) for name in (
        "baro_temp_c", "baro_press_hpa", "baro_alt_m", "baro_agl_m"))
    phase = _value(row, "phase", "P")
    fields.append(phase if phase in ("P", "C", "F", "R") else "P")
    t_clock = _value(row, "t_clock")
    fields.append("" if t_clock is None else "{:.1f}".format(t_clock))
    fields.append(str(_value(row, "status_flags", 0)))
    fields.append(_value(row, "gps_raw", "") or "")
    return ",".join(fields)


def load_db_records(db, session_id):
    records = []
    for row in db.rows_for_session(session_id):
        records.append({
            "when": float(row["recv_epoch"]),
            "iso": row["recv_iso"],
            "payload": payload_from_db_row(row),
            "rssi": _value(row, "rssi"),
            "snr": _value(row, "snr"),
        })
    return records


def load_tx_records(path):
    records = []
    with open(path, "r", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"tx_epoch", "tx_iso", "tx_monotonic", "send_ok", "payload"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError("TX log is missing required columns: {}".format(
                ", ".join(sorted(required))))
        for row in reader:
            try:
                when = float(row["tx_monotonic"])
            except (TypeError, ValueError):
                when = float(row["tx_epoch"])
            records.append({
                "when": when,
                "iso": row["tx_iso"],
                "payload": row["payload"],
                "rssi": None,
                "snr": None,
                "send_ok": row["send_ok"],
            })
    return records


def decode_records(records):
    decoded = []
    errors = 0
    for record in records:
        try:
            item = dict(record)
            item["data"] = decode(record["payload"])
            decoded.append(item)
        except TelemetryDecodeError:
            errors += 1
    return decoded, errors


def list_sessions(db):
    sessions = db.sessions()
    if not sessions:
        print("No sessions recorded in {}.".format(db.path))
        return
    print("Recorded sessions in {}:".format(db.path))
    print("{:<28} {:>6}  {}  ->  {}".format("SESSION", "PKTS", "FIRST", "LAST"))
    for sid, count, first, last in sessions:
        print("{:<28} {:>6}  {}  ->  {}".format(sid, count, first, last))


def paced_records(records, speed):
    previous = None
    for record in records:
        if previous is not None and speed > 0:
            gap = max(0.0, record["when"] - previous) / speed
            if gap:
                time.sleep(min(gap, MAX_GAP_S))
        previous = record["when"]
        yield record


def replay_terminal(records, speed, source_name, decode_errors):
    print("Replaying {} ({} packets, {} decode errors, speed x{}).".format(
        source_name, len(records), decode_errors, speed if speed else "max"))
    state = LiveState()
    for index, record in enumerate(paced_records(records, speed), 1):
        state.update(record["data"], record["rssi"], record["snr"])
        snap = state.snapshot()
        print("\n--- packet #{} @ {} ---".format(index, record["iso"]))
        print(format_readout(snap["latest"]))
        max_flight = snap["max_flight_g"]
        print("MAX FLIGHT G: {}".format(
            "--" if max_flight is None else "{:.2f} g".format(max_flight)))
        if record["rssi"] is None:
            print("LINK : RSSI n/a  SNR n/a")
        else:
            print("LINK : RSSI {} dBm  SNR {} dB".format(record["rssi"], record["snr"]))
    print("\nEnd of playback: {}.".format(source_name))


def replay_gui(records, speed, source_name, decode_errors):
    state = LiveState()
    stop_event = threading.Event()
    completion_event = threading.Event()

    def feed():
        for record in paced_records(records, speed):
            if stop_event.is_set():
                break
            state.update(record["data"], record["rssi"], record["snr"])
        for _ in range(decode_errors):
            state.note_error()
        completion_event.set()

    worker = threading.Thread(target=feed)
    worker.daemon = True
    worker.start()
    run_gui_dashboard(state, source_name, stop_event, mode_title="PLAYBACK",
                      completion_event=completion_event)


def _graph_time(records):
    """Use T-clock where present and extend that timeline backward through PAD."""
    if not records:
        return []
    anchor_index = None
    for index, record in enumerate(records):
        if record["data"].get("t_clock") is not None:
            anchor_index = index
            break
    if anchor_index is None:
        first_when = records[0]["when"]
        return [record["when"] - first_when for record in records]
    anchor_when = records[anchor_index]["when"]
    anchor_t = records[anchor_index]["data"]["t_clock"]
    points = []
    for record in records:
        t_clock = record["data"].get("t_clock")
        points.append(t_clock if t_clock is not None else
                      anchor_t + record["when"] - anchor_when)
    return points


def _metric_values(records, metric):
    values = []
    for record in records:
        data = record["data"]
        if metric == "Baro AGL (ft)":
            value = data["baro"]["agl_m"]
            values.append(None if value is None else value * FEET_PER_METER)
        elif metric == "Total acceleration (g)":
            a = data["accel_g"]
            values.append(math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2))
        elif metric == "Gyro magnitude (deg/s)":
            g = data["gyro_dps"]
            values.append(math.sqrt(g["x"] ** 2 + g["y"] ** 2 + g["z"] ** 2))
        else:
            value = data["gps"]["altitude"]
            values.append(None if value is None else value * FEET_PER_METER)
    return values


def _decimated_indices(length):
    if length <= MAX_GRAPH_POINTS:
        return list(range(length))
    step = float(length - 1) / float(MAX_GRAPH_POINTS - 1)
    return sorted(set(int(round(i * step)) for i in range(MAX_GRAPH_POINTS)))


def run_graphs(records, source_name):
    import tkinter as tk

    metrics = ("Baro AGL (ft)", "Total acceleration (g)",
               "Gyro magnitude (deg/s)", "GPS altitude (ft)")
    times = _graph_time(records)
    indices = _decimated_indices(len(records))

    root = tk.Tk()
    root.title("Rocket Telemetry Graphs - {}".format(source_name))
    root.geometry("800x450")
    root.configure(bg="black")
    selected = tk.StringVar(value=metrics[0])
    buttons = tk.Frame(root, bg="black")
    buttons.pack(fill="x", padx=5, pady=4)
    canvas = tk.Canvas(root, width=790, height=390, bg="#07111d",
                       highlightthickness=1, highlightbackground="cyan")
    canvas.pack(fill="both", expand=True, padx=5, pady=(0, 5))

    def draw():
        canvas.delete("all")
        width = max(400, canvas.winfo_width())
        height = max(250, canvas.winfo_height())
        left, right, top, bottom = 62, width - 18, 35, height - 42
        metric = selected.get()
        values = _metric_values(records, metric)
        valid = [(times[i], values[i], i) for i in indices if values[i] is not None]
        canvas.create_text(width / 2, 16, text=metric, fill="cyan",
                           font=("DejaVu Sans Mono", 12, "bold"))
        if not valid:
            canvas.create_text(width / 2, height / 2, text="NO DATA", fill="gray")
            return
        min_x = min(p[0] for p in valid)
        max_x = max(p[0] for p in valid)
        min_y = min(p[1] for p in valid)
        max_y = max(p[1] for p in valid)
        if max_x == min_x:
            max_x = min_x + 1.0
        if max_y == min_y:
            pad = max(1.0, abs(min_y) * 0.05)
            min_y -= pad
            max_y += pad
        canvas.create_rectangle(left, top, right, bottom, outline="white")
        canvas.create_text(left, top - 10, text="max {:.2f}".format(max_y), fill="white", anchor="w")
        canvas.create_text(left, bottom + 13, text="min {:.2f}".format(min_y), fill="white", anchor="w")
        canvas.create_text(right, bottom + 13, text="time {:.1f}..{:.1f} s".format(min_x, max_x),
                           fill="white", anchor="e")
        segments = []
        previous_index = None
        for x_value, y_value, index in valid:
            x = left + (x_value - min_x) / (max_x - min_x) * (right - left)
            y = bottom - (y_value - min_y) / (max_y - min_y) * (bottom - top)
            if previous_index is None or index > previous_index + max(2, len(records) // MAX_GRAPH_POINTS + 1):
                if len(segments) >= 4:
                    canvas.create_line(*segments, fill="yellow", width=2)
                segments = [x, y]
            else:
                segments.extend((x, y))
            previous_index = index
        if len(segments) >= 4:
            canvas.create_line(*segments, fill="yellow", width=2)
        canvas.create_text(width / 2, height - 8,
                           text="{} decoded packets; {} plotted points max".format(
                               len(records), MAX_GRAPH_POINTS), fill="gray")

    for metric in metrics:
        tk.Radiobutton(buttons, text=metric, variable=selected, value=metric,
                       command=draw, indicatoron=False, bg="#202830", fg="white",
                       selectcolor="#36566c", font=("DejaVu Sans Mono", 8)).pack(
                           side="left", expand=True, fill="x", padx=2)
    canvas.bind("<Configure>", lambda event: draw())
    root.after(50, draw)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay RX SQLite or TX-local telemetry.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="RX SQLite database path")
    parser.add_argument("--session", help="RX session (default: most recent)")
    parser.add_argument("--tx-log", help="TX telemetry CSV; bypasses SQLite")
    parser.add_argument("--list", action="store_true", help="list RX database sessions and exit")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="playback speed multiplier (0 = no delay)")
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--gui", action="store_true", help="replay in the live RX dashboard")
    display.add_argument("--graphs", action="store_true", help="open lightweight post-flight graphs")
    args = parser.parse_args(argv)
    if args.speed < 0:
        parser.error("--speed must be zero or positive")
    if args.list and args.tx_log:
        parser.error("--list applies only to the RX database")

    db = None
    try:
        if args.tx_log:
            source_name = args.tx_log.rsplit("/", 1)[-1]
            raw_records = load_tx_records(args.tx_log)
        else:
            db = TelemetryDB(path=args.db)
            if args.list:
                list_sessions(db)
                return 0
            session_id = args.session or db.latest_session()
            if session_id is None:
                print("Database is empty. Nothing to replay.")
                return 1
            source_name = session_id
            raw_records = load_db_records(db, session_id)

        records, decode_errors = decode_records(raw_records)
        if not records:
            print("No decodable telemetry in {}.".format(source_name))
            return 1
        if args.graphs:
            run_graphs(records, source_name)
        elif args.gui:
            replay_gui(records, args.speed, source_name, decode_errors)
        else:
            replay_terminal(records, args.speed, source_name, decode_errors)
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        print("Playback error: {}".format(exc))
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
