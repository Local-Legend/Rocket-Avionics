# RX header and list layout update

Date: 2026-10-27

## Changed files

- `System Scripts/RX_Temenetry.py`
  - Added `KRYPTON_PURPLE = "#3A1C4E"` and uses it for the exact `OBSIDIAN-1 TELEMETRY` GUI title.
  - Split `LIVE` into its own header label. It is lime only when live-radio telemetry has been received and packet age is within the existing 3-second freshness threshold; it is gray before the first packet, while stale, and throughout playback.
  - Added `STAGE: --` before the first packet and `STAGE: PAD/COUNTDOWN/FLIGHT/RECOVERY` afterward.
  - Uses a compact header grid with title left, LIVE near center, stage adjacent, and the existing prominent T clock right.
  - Window title is `Obsidian-1 Telemetry LIVE - <session>` in live mode and `Obsidian-1 Telemetry PLAYBACK - <source>` in playback mode.
  - Reordered the left list to ACCELERATION, GYRO, ALTITUDE, GPS, MAG, RADIO, MPU DIE TEMP, BMP CHIP TEMP. Existing name-keyed update bindings remain unchanged.
- `System Scripts/README.md`
  - Documented the Obsidian-1 title/color, dynamic LIVE semantics, playback behavior, stage header, and row order.
- `.kiro/steering/avionics-project.md`
  - Added this deployment and verification to project history.
- `.agents/tasks/rx-header-layout-verification.png`
  - Captured the live 800x480 RX desktop for visual verification.

No packet, radio, database, TX, service, sensor, or playback signature behavior changed. `playback.py` was not modified because it already calls the shared renderer with `mode_title="PLAYBACK"`.

## Deployment

RX-only deployment target: `pi@192.168.1.101`.

Backup directory:

`/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-header-layout-backup-20261027-175521`

Backed-up files: `RX_Temenetry.py`, `README.md`.

The previous GUI PID 2648 was stopped with SIGINT immediately before replacement. Local and deployed SHA-256 checksums matched:

- `RX_Temenetry.py`: `4742bd6586c447b224f04d0035ccaed803d880d5fd2818de98ebcd026826e8a8`
- `README.md`: `107e44b03d5a8f3b4d0c9e39a647c9981b9867d25479d16e21fa5afec7dc392c`

TX `pi@192.168.1.172` was not accessed or changed.

## Validation and final state

- Mac: `python3 -m py_compile "System Scripts/RX_Temenetry.py"` passed; generated `__pycache__` was removed. No project code was executed or imported.
- RX: `/home/pi/libs-venv/bin/python3 -m py_compile RX_Temenetry.py` passed with Python 3.7.3; remote `__pycache__` was removed.
- Final live GUI: exactly one process, PID 3642.
- Log: `/home/pi/Desktop/Avionics for Rocket/System Scripts/rx-gui-header-layout.log`.
- X11 title: `Obsidian-1 Telemetry LIVE - flight_20261027_175536`.
- X11 state: viewable at 798x436.
- Runtime telemetry: new session received fresh RECOVERY packets at 1 Hz; the verification query showed 20 packets with newest receive epoch only about 0.22 seconds old. The screenshot then showed RADIO `[LIVE]`, header LIVE in lime, stage RECOVERY, and current GPS/RSSI/SNR values.
- Visual layout: title, LIVE, stage, and list fit without overlap; diagnostic temperatures appear at the bottom. The exact dark Krypton Purple has intentionally limited contrast on black, per the user requirement.
- Visual caveat: the Raspberry Pi desktop displayed its existing `Low voltage warning` notification over the upper-right corner during capture, temporarily obscuring part of the T-clock region. The header widgets themselves did not overlap. RX continues to report the previously known undervoltage condition.
- Playback was not launched alongside the receiver, preserving the requirement for exactly one final RX GUI. Static source verification confirms playback mode forces the header LIVE label gray, sets the playback window suffix, continues replaying stage/T-clock, and preserves the existing `PLAYBACK COMPLETE` event handling.
