# Flight timeline / T clock implementation plan

Date: 2026-10-08
Workflow: `wf_88d23dbe931a26bf` (step `plan`)
Spec (authoritative): `.kiro/steering/avionics-project.md`, section
"Flight timeline requirement (user, 2026-10-08) - PENDING IMPLEMENTATION".
Workspace root (contains spaces, always quote): `/Users/ajarretm/Desktop/Avionics for Rocket`
Target folder: `/Users/ajarretm/Desktop/Avionics for Rocket/System Scripts`
Pre-change backup (already made, never edit): `.agents/backup/System Scripts-2026-10-08-pre-timeline/`
(11 files; `diff -rq` against `System Scripts` printed nothing at plan time).

Not a git repository. Dev machine is the Mac (Python 3.9.6, no Pi libraries, no
pynmea2, Tk available). Nothing may be installed. Verification = `py_compile`,
`bash -n`, and mocked-hardware harnesses kept OUTSIDE the project under
`/tmp/avionics_harness/`. Keep all code Python 3.9 compatible (no `match`, no
`X | Y` types); the Pi runs 3.13.

**RX REDEPLOY REQUIRED: YES** (packet grows 14 -> 16 fields, DB gains 2 columns,
every RX display changes). The Pi 3 has still never been set up, so this is
folded into its first-time install.

Decomposition: three FEATs, executed in this order (artifacts in
`.agents/tasks/task-flight-timeline/`):

| FEAT | Scope | Files |
|---|---|---|
| FEAT-001 | Packet contract + RX side (decoders, T-clock formatter, DB, displays, playback, baro null fix) | `telemetry_decode.py`, `RX_Temenetry.py`, `telemetry_db.py`, `playback.py` |
| FEAT-002 | TX phase state machine + service unit | `TX_Telemetry.py`, `rocket-tx.service` |
| FEAT-003 | Docs + project memory | `README.md`, `.kiro/steering/avionics-project.md` |

Unchanged in this task (verify by `diff` against the backup at the end):
`install_tx_autostart.sh`, `start_rx.sh`, `make_rx_gui_shortcut.sh`,
`Rocket-RX.desktop`, every `Grok_*.py`, every root-level prototype.

---

## A. Decisions (made here; do not re-decide)

| # | Topic | Decision | Why |
|---|---|---|---|
| D1 | Phase representation | Single-letter strings `"P" "C" "F" "R"` everywhere (TX variable, wire, DB `phase TEXT`). `PHASE_NAMES = {"P": "PAD", "C": "COUNTDOWN", "F": "FLIGHT", "R": "RECOVERY"}` defined in `TX_Telemetry.py` and in BOTH decoder copies. | One byte on the wire; names only for display/log. |
| D2 | GPS lock detection | Only while in PAD. A "new GGA sentence" = the string returned by `poll_gps()` differs from the previous pass (GGA carries a timestamp so each 1 Hz sentence differs; the 10 Hz loop otherwise sees the same string ~10 times). Each new sentence is parsed with `pynmea2.parse` (guarded); `int(gps_qual) >= 1` -> `lock_streak += 1`, anything else (qual 0, parse error, empty) -> `lock_streak = 0`. `lock_streak >= 3` triggers PAD -> COUNTDOWN. | Spec: 3 consecutive GGA sentences, ignore a flicker. Counting loop passes would lock on one sentence. |
| D3 | T clock source | At lock: `lock_mono = monotonic()`, `t0_mono = lock_mono + COUNTDOWN_S` (`COUNTDOWN_S = 30.0`). Every pass: `t_clock = round(monotonic() - t0_mono, 1)` (None in PAD). The SAME rounded value drives the C->F (`t_clock >= 0.0`) and F->R (`t_clock >= FLIGHT_DURATION_S`) transitions and is what gets transmitted, so the phase letter and the clock in one packet never disagree. GPS loss after lock changes nothing (lock logic is PAD-only; clock is monotonic). | Spec rule list. |
| D4 | BMP280 baseline | `init_baro()` keeps chip construction, `setup(...)` (same kwargs), `sleep(1)` and the one discarded conversion, and returns `chip` only. New `capture_pad_baseline()` (mean of 10 `get_pressure()` 0.1 s apart, prints `BMP280 pad baseline: ... hPa`, fail-soft -> `None`) is called inside the PAD -> COUNTDOWN transition, blocking ~1 s (the deadline scheduler already absorbs overruns; T-30.0 .. T-29.0 simply has no packets). Module global `pad_pressure_hpa = None` until then; `read_baro()` returns `agl_m = None` when the baseline is None but still returns temp/pressure/absolute altitude. | Spec: baseline captured in COUNTDOWN. Blocking keeps the bench-verified 10 x 0.1 s recipe. Consequence: in PAD field 13 is EMPTY while 10-12 are populated, so the "baro null-check" fix (D16) is now REQUIRED, not optional. |
| D5 | Camera lifecycle | Module globals `camera = None`, `video_path = None`. `start_camera()` unchanged in body; it is CALLED in the PAD -> COUNTDOWN transition (not at import). New `stop_camera()` helper: if `camera is not None`: `stop_recording()`, `close()`, print `Camera recording saved: <path>`, guarded; then sets `camera = None`. Called from the FLIGHT -> RECOVERY transition and from `finally:` (idempotent, so a SIGTERM in RECOVERY does not double-stop). | Spec: no video in PAD; file finalised at RECOVERY; same path as shutdown. |
| D6 | RECOVERY loop | Same `while True` loop and deadline scheduler. On entering R: `SAMPLE_PERIOD = RECOVERY_PERIOD_S` (`1.0`), `next_sample = monotonic()` (no catch-up burst), `stop_camera()`. In R the IMU/mag/baro read blocks are skipped (`if phase != "R"`), ints are `0`, baro fields `""`, GPS is still polled, packet still built with phase `R` + T clock + GGA, `lora.send` still guarded. The once-per-second status line keeps printing (1 line/s to the journal; acceptable). | Spec: GPS-only 1 Hz forever; keep scheduler and send guard. |
| D7 | Never exit | `while (monotonic() - start_time) < FLIGHT_DURATION_S` becomes `while True`. `FLIGHT_DURATION_S` (env `TX_FLIGHT_DURATION_S`, default 480) is now the avionics window measured from T-0. Script ends only via SIGTERM (`SystemExit(0)` -> `finally:`), a crash, or power loss. | Spec. |
| D8 | T-clock text (shared) | `format_t_clock(t)`: `None` -> `"T --:--"`; negative -> `secs = math.ceil(-t)`, sign `-`; otherwise `secs = math.floor(t)`, sign `+`; `secs < 3600` -> `"T{sign}{MM:02d}:{SS:02d}"`, else `"T{sign}{H}:{MM:02d}:{SS:02d}"`. Edge cases: -30.0 -> `T-00:30`; -0.4 -> `T-00:01`; 0.0 -> `T+00:00`; 59.9 -> `T+00:59`; 60.0 -> `T+01:00`; 3599.9 -> `T+59:59`; 3600.0 -> `T+1:00:00`. `-0.0` (possible from `"%.1f"`) is not `< 0` so it shows `T+00:00`. `format_phase_line(phase, t)`: `"PAD  T --:--  (waiting for GPS lock)"` for P, else `"<NAME>  <clock>"` e.g. `"COUNTDOWN  T-00:12"`. | Countdown convention: `T-00:01` is shown for the last second and `T-00:00` never appears; each displayed second lasts exactly one second. Hint text appended only in PAD, as the spec words it. |
| D9 | Decoder validation | `NUM_SENSOR_FIELDS = 16`, `NUM_INT_FIELDS = 10` kept; baro = `parts[10:14]`, `phase = parts[14].strip()`, `t_clock = _to_float_or_none(parts[15])`, GPS = `",".join(parts[16:])`. `phase not in PHASE_NAMES` -> `TelemetryDecodeError("bad phase field ...")`. Rationale: an old 14-field packet WITH a GGA tail has >16 comma parts (`parts[14] == "$GNGGA"`), so the count check alone would accept it and mis-parse; the phase check is what rejects it. A 14-field packet with an empty GPS tail (15 parts) is rejected by the count check as today. Result dict gains `"phase"`, `"phase_name"`, `"t_clock"`. | Step requirement: wrong-format packets must be rejected. |
| D10 | DB migration | Replace `BARO_COLUMNS` / `_BARO_COLUMNS` with `MIGRATED_COLUMNS = (("baro_temp_c", "REAL"), ("baro_press_hpa", "REAL"), ("baro_alt_m", "REAL"), ("baro_agl_m", "REAL"), ("phase", "TEXT"), ("t_clock", "REAL"))` and `_ensure_columns()` uses the declared type. `SCHEMA` gains `phase TEXT, t_clock REAL` after `baro_agl_m`, before `rssi` (fresh DBs: 39 columns; migrated DBs: appended at the end, same names). `_INSERT` gains `phase, t_clock` + 2 placeholders (38 values). | Existing guard, extended; 33- and 37-column DBs both migrate. |
| D11 | Displays | Terminal dashboard: new line directly under the `ROCKET TELEMETRY - LIVE` banner, before the first `=` rule closes: `" >>> {} <<<".format(format_phase_line(...))`, or `" >>> PHASE --  T --:-- <<<"` before the first packet. Scroll: each line prefixed `"[{phase} {clock}] "` e.g. `[C T-00:12] #42 acc(g) ...`. GUI: two new labels between `header` and `body`: `phase_lbl` (`("DejaVu Sans Mono", 20, "bold")`, fg `cyan`) and `tclock_lbl` (`("DejaVu Sans Mono", 44, "bold")`, fg `yellow`, bg black). Body font stays 16, so the clock is ~3x the sensor rows. Stale handling unchanged (last T stays; `link` label turns red `STALE`). No RX-side clock. | Spec: prominent, high-contrast, shows what the TX sent. |
| D12 | Playback | `playback.py` imports `format_t_clock`, `PHASE_NAMES` from `telemetry_decode` (pure Python; pynmea2 is already on the RX venv). Per row: `PHASE: COUNTDOWN  T-00:12` line right after the packet header, only when the row has a `phase` column and it is not NULL. Summary: one line per phase in order of first appearance with first and last clock, e.g. `Phases: PAD [T --:-- .. T --:--]  COUNTDOWN [T-00:30 .. T-00:01]  FLIGHT [T+00:00 .. T+07:59]  RECOVERY [T+08:00 .. T+15:32]`, plus existing max-AGL line. | Avoids a third copy of the formatter. |
| D13 | systemd | `Restart=on-failure`, `RestartSec=5`, `ExecStartPre=/bin/sleep 10`, venv `ExecStart`, `After=local-fs.target`, both `Environment=` lines kept. Comments rewritten: script runs until power off, restart only after a crash/power glitch, `TX_FLIGHT_DURATION_S` = avionics window measured from T-0. | Spec. |
| D14 | Installer / launchers | `install_tx_autostart.sh`, `start_rx.sh`, `make_rx_gui_shortcut.sh`, `Rocket-RX.desktop`: NO change. | Nothing in them depends on the loop or the packet. |
| D15 | Transition logging | One `print` per transition, format `PHASE <FROM> -> <TO>  T<+/-x.x>  gps_utc=<HH:MM:SS or n/a>  <reason>`; the UTC comes from `pynmea2.parse(last GGA).timestamp` (guarded). Reasons: `GPS lock (3 consecutive GGA fixes)`, `T-0`, `avionics window 480 s elapsed`. | Spec: logged once with GGA UTC. |
| D16 | Baro formatters (review carry-over, now required) | Per-value `n/a`: helper `_fmt_or_na(value, fmt)` returns `fmt.format(value)` or `"n/a"`. BARO line becomes `alt <x|n/a> m  AGL <x|n/a> m  <x|n/a> hPa  <x|n/a> C`; the whole line is `n/a` only when all four are None. Same in `telemetry_decode.format_readout`, `RX_Temenetry._baro_line`, `playback._print_row` (`_has_baro` -> any of the four not NULL). | PAD packets have 3 of 4 baro values (D4). |
| D17 | Headers | Each modified `.py` keeps its 3-line header; `TX_Telemetry.py` authority line adds `flight timeline spec 2026-10-08`; `playback.py` "Depends on" line adds `telemetry_decode.py`. | Step requirement. |
| D18 | Harness home | `/tmp/avionics_harness/` (stubs + scripts), outside the project. FEAT findings record the paths; the finalize step deletes the folder. | "Harnesses are inline and temporary"; nothing may be added to the project. |

---

## B. The state machine (TX)

```
          boot                 3 consecutive GGA with            t_clock >= 0.0           t_clock >= FLIGHT_DURATION_S
 [init] ------> PAD ("P") ---- gps_qual >= 1 ----------> COUNTDOWN ("C") ----------> FLIGHT ("F") ----------------------> RECOVERY ("R")  (until power off)
```

| Phase | Sensors read / sent | Camera | Baseline | Loop period | `t_clock` field | Status-line text |
|---|---|---|---|---|---|---|
| PAD | IMU, mag, baro (temp/press/alt; AGL empty), GPS | not recording | not captured | 0.1 s (10 Hz target) | `""` | `phase=PAD(waiting for GPS lock)` |
| COUNTDOWN | all, AGL populated | **started on entry** | **captured on entry** (blocking ~1 s) | 0.1 s | `-30.0` .. `-0.1` | `phase=COUNTDOWN T-12.3` |
| FLIGHT | all | recording | - | 0.1 s | `0.0` .. | `phase=FLIGHT T+37.0` |
| RECOVERY | GPS only; ints `0`, baro `""` | **stopped + finalised on entry** | - | **1.0 s** | keeps counting up | `phase=RECOVERY T+483.7` |

Transition actions, in order:

- PAD -> COUNTDOWN: `lock_mono = monotonic()`; `t0_mono = lock_mono + COUNTDOWN_S`;
  `phase = "C"`; log (D15); `pad_pressure_hpa = capture_pad_baseline()`;
  `camera, video_path = start_camera()` (prints `Camera recording locally to:` if ok).
- COUNTDOWN -> FLIGHT: `phase = "F"`; log. Nothing else (no ignition output).
- FLIGHT -> RECOVERY: `phase = "R"`; log; `stop_camera()`; `SAMPLE_PERIOD = RECOVERY_PERIOD_S`;
  `next_sample = monotonic()`.
- Forward only; no transition ever reads the GPS fix again after lock.

Loop pass order (every phase): `GPS_strg = poll_gps()` -> `advance_phase(GPS_strg)`
(does the lock counting in PAD and the time-based transitions, returns `t_clock`
or None) -> sensor reads if `phase != "R"` -> `read_baro()` if `phase != "R"` ->
build packet -> guarded `lora.send` -> rate/status line -> deadline sleep.

Implement `advance_phase()` as a module-level function that mutates the module
globals (`phase`, `lock_streak`, `last_lock_sentence`, `t0_mono`,
`pad_pressure_hpa`, `camera`, `video_path`, `SAMPLE_PERIOD`, `next_sample`) via
`global`, keeping the script's module-level style; or keep the logic inline in the
loop body under a `# --- phase state machine ---` comment. Either is fine; one
place only.

---

## C. The 16-field packet (contract; both decoder copies)

| Idx | Field | On the wire | Unavailable | RX |
|---|---|---|---|---|
| 0-9 | acc x3, gyro x3, temp, mag x3 | signed ints | `0` | unchanged |
| 10-13 | baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m | `%.2f` | `""` (13 is `""` in PAD) | float or None |
| **14** | **phase** | `P` / `C` / `F` / `R` | never empty | must be one of the four, else `TelemetryDecodeError` |
| **15** | **t_clock** | `%.1f` seconds relative to T-0, signed | `""` in PAD | float or None |
| 16.. | GPS | raw `$GNGGA`/`$GPGGA` (contains commas) | `""` | parsed if it contains `GGA` |

Examples (the GGA tail is the real bench sentence):

- PAD, no fix yet (AGL empty, clock empty):
  `0,0,2048,0,0,0,-3552,100,-67,0,24.81,1011.73,13.21,,P,,$GNGGA,145743.000,,,,,0,05,,,M,,M,,*5E`
- COUNTDOWN:
  `0,0,2048,0,0,0,-3552,100,-67,0,24.81,1011.73,13.21,0.00,C,-12.3,$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E`
- FLIGHT: same with `F,37.0`.
- RECOVERY (GPS only):
  `0,0,0,0,0,0,0,0,0,0,,,,,R,483.7,$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E`

TX build: `fmt_t = "" if t_clock is None else "{:.1f}".format(t_clock)`;
`message = ints + "," + baro + "," + phase + "," + fmt_t + "," + GPS_strg`.
Size grows by ~8 bytes (~150-160 bytes typical; still ~4 Hz real ceiling).

---

## D. Implementation items (ordered; each leaves every file compiling)

### FEAT-001 - packet contract and RX side

- [ ] 1. Build the Mac harness scaffolding once: `/tmp/avionics_harness/stubs/pynmea2.py`
      (a `parse()` that splits a GGA sentence and exposes `timestamp` (HHMMSS.sss ->
      `HH:MM:SS` string), `lat`, `lat_dir`, `lon`, `lon_dir`, `latitude`, `longitude`
      (degrees+minutes -> decimal, W/S negative), `gps_qual`, `num_sats`, `altitude`,
      `altitude_units`; `ParseError(ValueError)` raised for anything not starting
      with `$` or without `GGA`). Everything below runs with
      `PYTHONPATH=/tmp/avionics_harness/stubs`.
      Files: none in the project.
      Verify: `PYTHONPATH=/tmp/avionics_harness/stubs python3 -c "import pynmea2; m=pynmea2.parse('\$GNGGA,145743.000,2819.24962,N,08044.49755,W,1,16,0.7,6.2,M,-29.7,M,,*7E'); print(m.gps_qual, m.num_sats, round(m.latitude,6), round(m.longitude,6))"` prints `1 16 28.320827 -80.741626`.

- [ ] 2. `telemetry_decode.py`: add `PHASE_NAMES`, `NUM_SENSOR_FIELDS = 16`,
      `format_t_clock()`, `format_phase_line()`, `_fmt_or_na()`; extend `decode()`
      per D9 (phase validation, `t_clock`, GPS from `parts[16:]`, new result keys
      `phase`, `phase_name`, `t_clock`); `format_readout()` prints the phase line
      first and uses per-value baro `n/a` (D16); update the docstring layout table
      (16 fields) and the `__main__` sample to the COUNTDOWN example above.
      Files: `System Scripts/telemetry_decode.py`
      Verify: `cd "System Scripts" && python3 -m py_compile telemetry_decode.py && PYTHONPATH=/tmp/avionics_harness/stubs python3 telemetry_decode.py` prints a block whose first line is `COUNTDOWN  T-00:13`
      (ceil(12.3) = 13) followed by the ACCEL/GYRO/TEMP/HEAD/BARO/GPS lines.
      Then run `/tmp/avionics_harness/decode_check.py` (write it now): asserts
      the 7 D8 edge cases; round-trips the four section-C examples (phase,
      phase_name, t_clock, baro, gps fields, PAD gives `agl_m None` and
      `t_clock None`); `TelemetryDecodeError` for the old 14-field packet WITH
      the GGA tail, for the 14-field packet with an empty tail, and for phase
      `X`; `format_readout` of the PAD example contains `AGL n/a m` and not a
      `TypeError`. Expected output: `decode OK`.

- [ ] 3. `RX_Temenetry.py` inline decoder: mirror item 2 exactly (same constants,
      same function bodies for `PHASE_NAMES`, `format_t_clock`, `format_phase_line`,
      `_fmt_or_na`, `decode`; `_parse_gps`, `compute_heading`, `_to_int`,
      `_to_float_or_none` unchanged).
      Files: `System Scripts/RX_Temenetry.py`
      Verify: `/tmp/avionics_harness/copies_check.py` (write it now; reuse in the
      loop): imports both modules with pynmea2 stubbed (the RX import of
      pyLoraRFM9x is inside `main()`, so module import works), strips docstrings
      and compares `ast.dump()` of `decode`, `_parse_gps`, `compute_heading`,
      `_to_int`, `_to_float_or_none`, `format_t_clock`, `format_phase_line`,
      `_fmt_or_na` and the values of `NUM_INT_FIELDS`, `NUM_SENSOR_FIELDS`,
      `PHASE_NAMES`, `ACCEL_SCALE_MODIFIER_16G`, `DECLINATION`; `decode()` output
      equal for the four section-C examples. Expected: `copies agree`.

- [ ] 4. `telemetry_db.py` and the inline DB in `RX_Temenetry.py`: D10 (`phase TEXT,
      t_clock REAL` in both `SCHEMA`/`_SCHEMA`, `MIGRATED_COLUMNS` with types in
      both, `_ensure_columns()` uses the type, `_INSERT` + `insert()` pass
      `decoded["phase"]`, `decoded["t_clock"]`). Update the module docstring
      (2026-10-08 timeline columns, 39 columns, older 33/37-column DBs migrate).
      Files: `System Scripts/telemetry_db.py`, `System Scripts/RX_Temenetry.py`
      Verify: `/tmp/avionics_harness/db_check.py` (write it now): (a) creates
      `/tmp/avionics_harness/old37.db` by executing the 37-column `SCHEMA` text
      extracted from the backup `telemetry_db.py` (`.agents/backup/System
      Scripts-2026-10-08-pre-timeline/telemetry_db.py`) and inserting one row;
      opens it with the new `TelemetryDB`; asserts `PRAGMA table_info` now has 39
      columns including `phase` and `t_clock`, old row reads NULL in both; inserts
      the COUNTDOWN and PAD decoded dicts; reads back `phase == "C"`,
      `t_clock == -12.3`, PAD `t_clock IS NULL`; reopens (idempotent).
      (b) Same against a fresh DB. (c) RX inline `TelemetryDB` writes a DB that
      the module `TelemetryDB` reads, and both `PRAGMA table_info` column lists
      are identical (39). Expected: `db OK`. Extend `copies_check.py` to compare
      the schema column lists and `MIGRATED_COLUMNS`.

- [ ] 5. `RX_Temenetry.py` displays: dashboard phase line, scroll prefix, GUI
      labels per D11; `_baro_line` per D16. `LiveState` needs no new fields.
      Files: `System Scripts/RX_Temenetry.py`
      Verify: `/tmp/avionics_harness/rx_check.py` (write it now): builds a
      `LiveState`, feeds the four section-C decoded packets, asserts
      `render_dashboard()` contains `>>> COUNTDOWN  T-00:13 <<<` after the C
      packet, `>>> PAD  T --:--  (waiting for GPS lock) <<<` after the P packet,
      `>>> RECOVERY  T+08:03 <<<` after R; `_baro_line` of the PAD packet is
      `alt 13.2 m  AGL n/a m  1011.7 hPa  24.8 C`, of all-None is `n/a`; a
      captured scroll line (call the inner formatting via a small refactor-free
      check: simulate `on_recv` by running `main()`? NO - instead assert the
      prefix string built by `"[{} {}] ".format(data["phase"], format_t_clock(data["t_clock"]))`
      equals `[C T-00:13] `). GUI: `python3 -c` that creates `tk.Tk()`, runs the
      label-creation part by calling `run_gui_dashboard` is not feasible
      headless-safe on every Mac; instead assert by reading the source that
      `tclock_lbl` uses font size 44 and `phase_lbl` 20 (`ast`/regex on the
      file is acceptable ONLY for this GUI check, since Tk cannot be driven in
      the harness) and that `python3 -m py_compile RX_Temenetry.py` passes; also
      `PYTHONPATH=/tmp/avionics_harness/stubs python3 RX_Temenetry.py --help`
      still lists `--gui --scroll --node --db`. Expected: `rx OK`.

- [ ] 6. `playback.py`: D12 + D16 (import from `telemetry_decode`; `PHASE:` line;
      phases summary; `_has_baro` any-of-four; per-value `n/a`; header "Depends
      on" line; docstring says it needs `telemetry_db.py`, `telemetry_decode.py`
      and `pynmea2`).
      Files: `System Scripts/playback.py`
      Verify: `cd "System Scripts" && PYTHONPATH=/tmp/avionics_harness/stubs python3 playback.py --db /tmp/avionics_harness/old37.db --list`
      lists the sessions; `... playback.py --db /tmp/avionics_harness/old37.db --session <sid> --speed 0`
      prints, for the migrated old row, no `PHASE:` line and a `BARO :` line (or
      none if all NULL) without a traceback; for the C row `PHASE: COUNTDOWN  T-00:13`;
      for the P row `PHASE: PAD  T --:--` and `BARO : alt 13.2 m  AGL n/a m ...`;
      ends with a `Phases:` summary line and `End of session`.

- [ ] 7. FEAT-001 wrap-up: `python3 -m py_compile` on the four files; rerun
      `decode_check.py`, `copies_check.py`, `db_check.py`, `rx_check.py`; grep
      sweep `grep -n "NUM_SENSOR_FIELDS = 14\|BARO_COLUMNS" "System Scripts"/*.py`
      must be empty (grep is a sweep here, not the verification). Record the
      harness paths and the exact `decode()` result keys in the FEAT findings.

### FEAT-002 - TX state machine and service

- [ ] 8. `TX_Telemetry.py`: constants `COUNTDOWN_S = 30.0`, `RECOVERY_PERIOD_S = 1.0`,
      `PHASE_NAMES`; `FLIGHT_DURATION_S` comment = avionics window from T-0;
      header authority line (D17). `init_baro()` -> returns chip only (D4);
      new `capture_pad_baseline()`; `read_baro()` tolerates `pad_pressure_hpa is None`.
      Camera: do NOT start at import; `camera = video_path = None`; new
      `stop_camera()` (D5). Phase globals + `advance_phase()` (section B, D2,
      D3, D15). Loop: `while True`; RECOVERY skips sensor reads, period 1.0
      (D6); packet adds `phase` and `t_clock` fields (section C; update the
      layout comment); status line adds `phase=...`; `finally:` calls
      `stop_camera()` and prints the final phase in the summary. Keep every
      hardware constant, the LoRa call, `poll_gps()`, `read_MPU_data`,
      `read_MAG_data`, `init_imu`, `init_mag`, `on_recv`, SIGTERM handler
      unchanged.
      Files: `System Scripts/TX_Telemetry.py`
      Verify: `python3 -m py_compile TX_Telemetry.py`, then the harness in item 9.

- [ ] 9. TX harness `/tmp/avionics_harness/tx_harness.py` (write it now). Stubs via
      `sys.modules` before `runpy.run_path(TX_Telemetry.py, run_name="__main__")`:
      `serial` (fake `Serial` whose `in_waiting`/`read()` hand out one scripted
      GGA line per virtual second), `smbus2` (fake `SMBus` returning fixed
      register bytes, `read_byte_data(0x68, 0x75) == 0x68`), `bmp280` (fake
      `BMP280` with `setup`, `get_temperature() -> 24.81`, `get_pressure() -> 1011.73`),
      `pyLoraRFM9x` (`LoRa` recording ctor kwargs and every `send(message, to)`
      into a list; `ModemConfig.Bw125Cr45Sf128`), `picamera2`/`picamera2.encoders`/
      `picamera2.outputs`/`libcamera` (fake `Picamera2` recording `start_recording`/
      `stop_recording`/`close` calls and the output path), and the `pynmea2`
      stub from item 1. Virtual time: patch `time.monotonic` and `time.sleep`
      BEFORE running the script (the script does `from time import sleep, monotonic`,
      so the names bind to the patched functions): `sleep(d)` advances a fake
      clock by `d` and returns immediately. Set `FLIGHT_DIR_BASE` to a tmp dir by
      patching `pathlib.Path.mkdir`? No - simpler: the fake `Picamera2` never
      touches the filesystem, but `folder.mkdir()` does; run with
      `HOME`-independent path by monkeypatching `pathlib.Path.mkdir` to a no-op
      in the harness. Stop condition: the fake `send()` raises `SystemExit(0)`
      once the scenario's goal is met (this exercises `finally:` exactly like
      SIGTERM). Scenarios, each a separate run with its own scripted GPS feed
      and env:
      - S1 PAD, no fix: 10 GGA with qual 0, then stop after 60 packets.
        Expect every packet field 14 `P`, field 15 `""`, field 13 `""`,
        fields 10-12 populated, no `start_recording`, no `pad baseline` print,
        lock never reached.
      - S2 Lock -> COUNTDOWN: GGA qual 0, 1, 0, 1, 1, 1 ...: lock must happen on
        the third consecutive qual-1 sentence (not on the second); the first C
        packet has `t_clock` `-30.0` (allow `-30.0`/`-29.9` because the baseline
        capture advances the fake clock ~1 s -> expect `<= -28.9`); exactly one
        `start_recording`, one `BMP280 pad baseline:` print, one
        `PHASE PAD -> COUNTDOWN` log line; field 13 populated from the first C
        packet on.
      - S3 Crossing T-0: continue S2 until a packet with phase `F`; the last C
        packet has `t_clock < 0`, the first F packet `t_clock >= 0.0` and
        `<= 0.1`; one `PHASE COUNTDOWN -> FLIGHT` line; `stop_recording` not yet called.
      - S4 RECOVERY with `TX_FLIGHT_DURATION_S=5`: continue until 5 R packets.
        Expect exactly one `PHASE FLIGHT -> RECOVERY` line, exactly one
        `stop_recording` + `close`, R packets have ints all `0`, four empty baro
        fields, phase `R`, `t_clock >= 5.0`, GGA tail present; consecutive R
        packets are 1.0 s apart in virtual time (previous packets 0.1 s);
        `SMBus.read_byte_data` call count stops increasing after the transition;
        `finally:` does NOT call `stop_recording` a second time.
      - S5 GPS loss after lock: like S2 but after lock the feed sends qual 0
        forever; phase still advances C -> F on time, `t_clock` keeps rising,
        no second `PHASE PAD -> COUNTDOWN` line.
      - S6 `FAIL=camera,baro` env handled by the stubs (ctor raises): S2 flow
        still locks; prints `Camera unavailable:` and `BMP280 unavailable:` once;
        C packets have four empty baro fields; `finally:` prints `video: none`.
      - S7 Every captured packet round-trips through
        `telemetry_decode.decode()` (import from `System Scripts` with the stub
        on `PYTHONPATH`) without `TelemetryDecodeError`, and the decoded
        `phase`/`t_clock` equal the TX fields. Also assert the LoRa ctor kwargs
        are exactly `spi_channel=1, interrupt_pin=5, reset_pin=25, spi_port=0,
        my_address=2, freq=915, tx_power=15, modem_config=Bw125Cr45Sf128, acks=False`
        and `send(..., 1)`.
      Files: none in the project.
      Verify: `PYTHONPATH=/tmp/avionics_harness/stubs python3 /tmp/avionics_harness/tx_harness.py` prints `S1 OK` .. `S7 OK` then `TX harness OK`.

- [ ] 10. `rocket-tx.service`: D13.
      Files: `System Scripts/rocket-tx.service`
      Verify: `python3 - <<'EOF'` using `configparser` (`strict=False`,
      `interpolation=None`, read the file) asserting `Service.Restart == "on-failure"`,
      `RestartSec == "5"`, `ExecStartPre == "/bin/sleep 10"`, `ExecStart` starts
      with `/home/pi/libs-venv/bin/python3`, `Unit.After == "local-fs.target"`,
      no `multi-user.target` in `After`/`Wants`, `Install.WantedBy == "multi-user.target"`,
      both `Environment` lines present (configparser merges duplicate keys; read
      the raw text for the two `Environment=` lines), and the raw text contains
      no `Restart=always`. Expected: `unit OK`.

- [ ] 11. FEAT-002 wrap-up: `python3 -m py_compile TX_Telemetry.py`; rerun the TX
      harness; `grep -n "while (monotonic() - start_time) < FLIGHT_DURATION_S\|Restart=always" "System Scripts"/TX_Telemetry.py "System Scripts"/rocket-tx.service`
      empty (sweep only); `diff` each hardware constant line against the backup
      (the `GPS_PORT` .. `RX_NODE_ADDRESS` block and the `LoRa(...)` call must
      be byte-identical: `diff <(sed -n '/^GPS_PORT/,/^RX_NODE_ADDRESS/p' TX_Telemetry.py) <(sed -n '/^GPS_PORT/,/^RX_NODE_ADDRESS/p' "<backup>/TX_Telemetry.py")` prints nothing).
      Record in findings: harness path, the exact transition log format, the
      status-line format.

### FEAT-003 - docs and project memory

- [ ] 12. `System Scripts/README.md`: (a) section "5. How the service behaves":
      script runs until power off, phases summary, `Restart=on-failure`
      (crash/power glitch only), `TX_FLIGHT_DURATION_S` = avionics window from
      T-0, journal lines now include `phase=... T...` and the three `PHASE a -> b`
      lines, camera starts at lock and the video is finalised at RECOVERY;
      (b) new section "Flight timeline" (after section 5): the four-phase table
      (what the TX does, what the ground crew sees on each display), the T clock
      format (`T-00:30` -> `T+00:00` -> `T+MM:SS` -> `T+H:MM:SS`, `T --:--`
      in PAD), lock rule (3 consecutive GGA fixes; later GPS loss does not regress),
      and the two caveats verbatim in substance: launch is expected ~30 s after
      lock so a long pad hold after lock can exhaust the 8-minute window before
      launch; if GPS never locks the system stays in PAD with telemetry but no
      video and no clock; (c) camera bullet: recording starts at GPS lock, stops
      at RECOVERY; (d) RX "6. Run modes": dashboard phase/T line, scroll prefix,
      GUI large clock; (e) "7. Post-flight playback": `PHASE:` line + `Phases:`
      summary; playback now also needs `telemetry_decode.py` (and pynmea2, which
      the RX venv has); (f) "What changed on the RX in this update": rewrite for
      this update (16 fields, phase/t_clock, displays, DB columns, baro `n/a`
      per value); (g) "Database": 39 columns, `phase TEXT`, `t_clock REAL`,
      auto-migration of 33- and 37-column files; (h) troubleshooting rows:
      `Dashboard stuck at T --:--` -> no GPS lock yet (check `gps=` / sats);
      `RX shows decode errors after the update` -> TX and RX versions differ
      (16-field packet). Keep every hardware table, setup step and hostname
      note unchanged.
      Files: `System Scripts/README.md`
      Verify: `grep -c` sweeps (each >= 1): `Restart=on-failure`, `Flight timeline`,
      `T-00:30`, `T --:--`, `RECOVERY`, `t_clock`, `phase TEXT`, `16 fields` or
      `16-field`, `waiting for GPS lock`; and (each 0): `Restart=always`,
      `relaunches it after about 15 s`, `exits cleanly`, `14-field packet (+4`.
      Then read the whole file once for consistency with the code (this is a
      docs FEAT; the read-through is the real verification).

- [ ] 13. `.kiro/steering/avionics-project.md`: (a) rename the section heading
      "Flight timeline requirement (user, 2026-10-08) - PENDING IMPLEMENTATION"
      to "... - IMPLEMENTED 2026-10-08 (workflow wf_88d23dbe931a26bf)" and keep
      its table/rules (they are the spec); add the decisions D2-D6, D8, D9, D12,
      D16 as a short "Decisions made in the flight-timeline update" block;
      (b) "Telemetry packet format" section: 16 fields, new rows 14 `phase` and
      15 `t_clock`, GPS from 16, the COUNTDOWN example packet, `NUM_SENSOR_FIELDS = 16`,
      note that PAD sends field 13 empty, note that the phase-letter check is
      what rejects old packets; (c) "Radio configuration" section: packet size
      ~150-160 bytes; (d) SQLite section: 39 columns, `phase`, `t_clock`,
      `MIGRATED_COLUMNS`; (e) "Decisions made in the System Scripts update"
      D15/D16 rows: mark superseded (`Restart=on-failure`, no clean exit);
      (f) Open decisions: Q11 already resolved; add "camera video path still
      not bench-verified, now starts at lock"; (g) History: new newest-first
      entry for this update with **RX REDEPLOY REQUIRED: YES**, harness
      summary, and the summary file path `.agents/tasks/flight-timeline-summary.md`.
      Files: `.kiro/steering/avionics-project.md`
      Verify: `grep -c "PENDING IMPLEMENTATION" .kiro/steering/avionics-project.md` is 0;
      `grep -c "NUM_SENSOR_FIELDS = 16"` >= 1; `grep -c "Restart=always"` only in
      history/superseded context (read the matches); file still renders as
      markdown (open and skim).

### Convergence (fixer + reviewer loop) and finalize

- [ ] 14. Integration pass (loop iteration 1): `python3 -m py_compile` on all 5 `.py`,
      `bash -n` on the 3 `.sh`, rerun `decode_check.py`, `copies_check.py`,
      `db_check.py`, `rx_check.py`, `tx_harness.py` (S7 is the TX<->RX seam),
      the unit check, and `diff -q` of the four files that must be unchanged
      against the backup. Fix seams. Reviewer writes
      `.agents/tasks/flight-timeline-review.json` with `{"verdict": "APPROVED"|"CHANGES_REQUESTED", ...}`.
- [ ] 15. Finalize: write `.agents/tasks/flight-timeline-summary.md` (per-file
      changes, RX REDEPLOY REQUIRED: YES, verification table, what to do on each
      Pi next), confirm the steering history entry, `rm -rf /tmp/avionics_harness`,
      remove any `__pycache__` under `System Scripts`, final
      `diff -rq "System Scripts" "<backup>"` listing exactly the 7 changed files
      (`TX_Telemetry.py`, `rocket-tx.service`, `telemetry_decode.py`,
      `RX_Temenetry.py`, `telemetry_db.py`, `playback.py`, `README.md`).

---

## E. Harness scenario checklist (what "verified on the Mac" must mean)

| # | Scenario | Where | Pass criterion |
|---|---|---|---|
| H1 | T-clock edge cases -30.0, -0.4, 0.0, 59.9, 60.0, 3599.9, 3600.0 | `decode_check.py` (both copies via `copies_check.py`) | `T-00:30 T-00:01 T+00:00 T+00:59 T+01:00 T+59:59 T+1:00:00`; `None` -> `T --:--` |
| H2 | Decoder round-trips 16-field P/C/F/R examples | `decode_check.py` | keys `phase`, `phase_name`, `t_clock`, baro, gps correct |
| H3 | Decoder rejects 14-field packet (with and without GGA tail) and phase `X` | `decode_check.py` | `TelemetryDecodeError` x3 |
| H4 | Both decoder copies AST-identical | `copies_check.py` | `copies agree` |
| H5 | DB migration 37 -> 39 columns, old row NULLs, idempotent reopen, RX-inline/module schema identical | `db_check.py` | `db OK` |
| H6 | Playback prints `PHASE:` and `Phases:` and survives rows with partial/NULL baro | playback run | no traceback |
| H7 | PAD with no fix | `tx_harness.py` S1 | `P`, empty clock, no camera, no baseline |
| H8 | Lock after 3 consecutive fixes -> COUNTDOWN (not after 2, flicker resets) | S2 | one transition, camera + baseline once, first clock ~-30.0 |
| H9 | Clock crosses T-0 -> FLIGHT | S3 | first F packet `0.0 <= t <= 0.1` |
| H10 | RECOVERY after shortened window | S4 | camera stopped once, zeros/empties, 1 Hz spacing, I2C reads stop |
| H11 | GPS loss after lock does not regress | S5 | phases still advance, no second lock |
| H12 | Camera/baro failure still fail-soft | S6 | `unavailable` prints, flow continues |
| H13 | TX packets decode on the RX copy | S7 | no decode errors, fields match |
| H14 | Unit file | configparser check | `unit OK` |

Not verifiable on the Mac (state so in the summary): real picamera2 start/stop
at lock/RECOVERY, lgpio/pyLoraRFM9x, BMP280 timing, systemd restart behaviour,
Tkinter rendering of the new labels, journal volume in a multi-hour RECOVERY.
