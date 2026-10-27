# RX title outline update

## Result

Updated only `System Scripts/RX_Temenetry.py`. The visible `OBSIDIAN-1 TELEMETRY` title now uses a bounded, borderless Tk Canvas with the existing `DejaVu Sans Mono`, 15-point, bold font. Eight white copies at one-pixel surrounding offsets form a thin outline; the foreground remains exact Krypton Purple `#3A1C4E`.

LIVE dynamic gray/lime behavior, STAGE placement, T-clock, parameter order, playback rendering, packet/radio/database behavior, and title wording were not changed. TX `192.168.1.172` was not accessed.

## Validation and deployment

- Mac syntax-only check: `python3 -m py_compile` passed; generated `__pycache__` removed.
- RX backup: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-title-outline-backup-20261027-175838/RX_Temenetry.py`.
- Deployed SHA-256 matched local: `c69d9e0979acd2feba4c4b671973afdd129b82de249a2c7b83f5255aea5fdf56`.
- RX `/home/pi/libs-venv/bin/python3 -m py_compile RX_Temenetry.py` passed; remote `__pycache__` removed.
- Replaced prior manual GUI PID 3642. Final GUI is the sole matching process, PID 4046, on `DISPLAY=:0.0`.
- X11 reports the window viewable at 798x436 (`Obsidian-1 Telemetry LIVE - flight_20261027_175913`).
- SQLite rows advanced from 18325 to 18328 over three seconds, confirming live reception.

## Visual verification

Screenshot: `.agents/tasks/rx-title-outline-verification.png`.

The screenshot confirms purple letter centers with a clean thin white outline and no overlap among title, lime LIVE, and RECOVERY stage. The existing desktop low-voltage notification overlays part of the far-right T-clock; this is not widget overlap and matches the prior known RX power warning.
