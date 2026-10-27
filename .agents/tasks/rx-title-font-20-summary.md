# RX title font 20 update

Date: 2026-10-27

## Change

- RX only: changed the visible `OBSIDIAN-1 TELEMETRY` title in `System Scripts/RX_Temenetry.py` from `DejaVu Sans Mono` 17 bold to exactly 20 bold.
- Preserved the exact `#3A1C4E` Krypton Purple foreground and all eight existing white outline copies at their exact one-pixel offsets.
- The existing font probe dynamically expanded the bounded, borderless title Canvas; no manual layout sizing was added.
- Wording, placement, LIVE/STAGE/T-clock behavior, list layout, playback behavior, packet decoding, radio configuration, SQLite schema, and telemetry behavior were unchanged.

## Validation and deployment

- Mac syntax-only check: `python3 -m py_compile "System Scripts/RX_Temenetry.py"` passed; generated `__pycache__` was removed. No project script was run or imported on the Mac.
- RX backup: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-title-font-20-backup-20261027-180618/RX_Temenetry.py`.
- Deployed only to RX `pi@192.168.1.101`; TX was not accessed.
- Local and deployed SHA-256 matched: `27fc46b4574336a59b107800052e249786c5e6da5caddd70f7c12766e313ac46`.
- RX `/home/pi/libs-venv/bin/python3` 3.7.3 syntax check passed; remote `__pycache__` was removed.
- Restarted the existing transient per-user `rocket-rx-gui.service` without changing its configuration.
- Exactly one GUI process remained: PID 4833, `/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui`.
- X11 reported `Obsidian-1 Telemetry LIVE - flight_20261027_180630` at 798x436.
- SQLite advanced from 18643 to 18646 rows during the initial four-second restart check, then from 18656 to 18660 over another four seconds, confirming continued fresh packet reception.

## Visual verification

The 800x480 desktop capture at `.agents/tasks/rx-title-font-20-verification.png` shows the full 20 pt title and its one-pixel white outline without clipping. There is clear horizontal space before the lime LIVE label, and the title, LIVE, and STAGE widgets do not overlap at the required 798x436 GUI size. The capture also shows live RECOVERY telemetry, GPS lock with 9 satellites, RADIO LIVE, RSSI/SNR, and packet count updates. The far-right T clock remains partly covered by the Pi desktop's existing low-voltage notification; this is an external desktop overlay, not GUI widget overlap.

No persistent service file/configuration, TX file, packet format, radio setting, database schema, playback behavior, filename, or path was changed. RX redeployment was completed.
