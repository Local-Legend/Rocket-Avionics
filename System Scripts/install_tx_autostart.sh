#!/usr/bin/env bash
#
# install_tx_autostart.sh - Install the Rocket TX telemetry autostart service.
#
# RUN THIS ON THE RASPBERRY PI ZERO 2 W (the rocket's flight computer), NOT on
# your Mac. It installs a systemd service so TX_Telemetry.py starts
# automatically at boot using the venv interpreter /home/pi/libs-venv/bin/python3.
#
# Usage (on the Pi Zero 2 W):
#     cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
#     chmod +x install_tx_autostart.sh
#     sudo ./install_tx_autostart.sh
#
set -euo pipefail

SERVICE_NAME="rocket-tx.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_UNIT="${SCRIPT_DIR}/${SERVICE_NAME}"
DEST_UNIT="/etc/systemd/system/${SERVICE_NAME}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run with sudo (needs to write to /etc/systemd/system)." >&2
    exit 1
fi

# Guard against accidentally running on the wrong machine.
if [[ "$(uname -s)" != "Linux" ]] || ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: this installer is for the Raspberry Pi (Linux + systemd)." >&2
    echo "       It looks like you're not on the Pi. Aborting." >&2
    exit 1
fi

if [[ ! -f "$SRC_UNIT" ]]; then
    echo "ERROR: ${SERVICE_NAME} not found next to this script." >&2
    exit 1
fi

# The unit's ExecStart must point at the venv interpreter. Warn (do not fail)
# if it is missing so the user knows why the service would die at import time.
PY_INTERP="$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' "$SRC_UNIT")"
if [[ -n "$PY_INTERP" && ! -x "$PY_INTERP" ]]; then
    echo "WARNING: $PY_INTERP not found; create the venv per README.md before starting the service." >&2
fi

echo "Installing ${SERVICE_NAME} -> ${DEST_UNIT}"
cp "$SRC_UNIT" "$DEST_UNIT"

echo "Reloading systemd and enabling the service..."
systemctl daemon-reload
# Static check of the installed unit (ordering cycles, bad paths). Non-fatal.
systemd-analyze verify "$DEST_UNIT" || true
systemctl enable "$SERVICE_NAME"

echo
echo "Done. The TX telemetry will now start automatically on boot."
echo
echo "Useful commands:"
echo "  sudo systemctl start   ${SERVICE_NAME}   # start now without rebooting"
echo "  sudo systemctl stop    ${SERVICE_NAME}   # stop it"
echo "  sudo systemctl status  ${SERVICE_NAME}   # check state"
echo "  journalctl -u ${SERVICE_NAME} -f         # watch live log output"
echo "  journalctl -b | grep -i \"ordering cycle\"   # must print nothing"
echo "  sudo systemctl disable ${SERVICE_NAME}   # turn off autostart"
