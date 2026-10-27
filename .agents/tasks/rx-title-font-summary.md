# RX title font update

Date: 2026-10-27

## Change

- RX only: changed the visible `OBSIDIAN-1 TELEMETRY` title in `System Scripts/RX_Temenetry.py` from `DejaVu Sans Mono` 15 bold to 17 bold.
- Preserved the exact `#3A1C4E` Krypton Purple foreground and all eight existing white outline copies at their exact one-pixel offsets.
- The existing font probe continues to size the bounded, borderless title Canvas automatically. No manual width or height adjustment was needed.
- Wording, top-left placement, LIVE/STAGE/T-clock widgets, list layout, playback behavior, packet decoding, radio configuration, SQLite schema, and telemetry behavior were not changed.

## Validation and deployment

- Mac syntax-only check: `python3 -m py_compile "System Scripts/RX_Temenetry.py"` passed; generated `__pycache__` was removed. No project script was run or imported on the Mac.
- RX backup: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-title-font-backup-20261027-180134/RX_Temenetry.py`.
- Deployed only to RX `pi@192.168.1.101`; TX was not accessed.
- Local and deployed SHA-256 matched: `9920f30cd0b6c86d7ef848f47def7c973ffec7cd65d8f4a88c794893410d1494`.
- RX `/home/pi/libs-venv/bin/python3` 3.7.3 syntax check passed; remote `__pycache__` was removed.
- Exactly one live GUI process remained: PID 4448, `/home/pi/libs-venv/bin/python3 RX_Temenetry.py --gui`.
- X11 reported `Obsidian-1 Telemetry LIVE - flight_20261027_180327` at 798x436.
- SQLite advanced from 18493 to 18497 rows over four seconds, confirming continued packet reception.

## Visual verification

The 800x480 desktop capture at `.agents/tasks/rx-title-font-verification.png` confirms the full 17 pt title and one-pixel outline are unclipped. There is clear horizontal space before the lime LIVE label, with no overlap among title, LIVE, or STAGE. The far-right T clock remains partly covered by the Pi desktop's existing low-voltage notification; this is an external desktop overlay, not GUI widget overlap.

No persistent service file or configuration was changed. The GUI was launched through a transient per-user process unit so it remains active after SSH disconnect.
