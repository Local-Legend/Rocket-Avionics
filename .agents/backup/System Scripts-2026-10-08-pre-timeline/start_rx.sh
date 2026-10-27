#!/usr/bin/env bash
#
# start_rx.sh - Launch the Rocket RX telemetry receiver + live display.
#
# RUN THIS ON THE RASPBERRY PI 3 (ground station). It is what the double-click
# desktop launcher (Rocket-RX.desktop) calls. You can also run it directly.
#
# It changes into the script directory so RX_Temenetry.py runs with the right
# working directory (pyLoraRFM9x/lgpio writes .lgd-nfy* files into the CWD),
# then starts the receiver with the terminal dashboard using the venv
# interpreter. The venv is created by the "RX side (Pi 3) - first-time setup"
# steps in README.md; a bare `python3` cannot import pyLoraRFM9x / pynmea2.
#
set -euo pipefail

RX_PYTHON="/home/pi/libs-venv/bin/python3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Rocket RX telemetry receiver..."
echo "Working directory: $SCRIPT_DIR"
echo "Interpreter      : $RX_PYTHON"
echo

if [[ ! -x "$RX_PYTHON" ]]; then
    echo "ERROR: $RX_PYTHON not found. Run the \"RX side (Pi 3) - first-time setup\" steps in README.md" >&2
    echo
    echo "Press Enter to close this window."
    read -r _
    exit 1
fi

# Default: terminal dashboard. To use the Tkinter window instead, change the
# line below to:  "$RX_PYTHON" RX_Temenetry.py --gui
"$RX_PYTHON" RX_Temenetry.py

# Keep the terminal open after the program exits so you can read the summary.
echo
echo "Receiver stopped. Press Enter to close this window."
read -r _
