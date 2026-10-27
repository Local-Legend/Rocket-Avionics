#!/usr/bin/env bash
#
# start_rx.sh - Launch the Rocket RX telemetry receiver + live display.
#
# RUN THIS ON THE RASPBERRY PI 3 (ground station). It is what the double-click
# desktop launcher (Rocket-RX.desktop) calls. You can also run it directly.
#
# It changes into the script directory so RX_Temenetry.py runs with the right
# working directory, then starts the receiver with the terminal dashboard.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Rocket RX telemetry receiver..."
echo "Working directory: $SCRIPT_DIR"
echo

# Default: terminal dashboard. To use the Tkinter window instead, change the
# line below to:  python3 RX_Temenetry.py --gui
python3 RX_Temenetry.py

# Keep the terminal open after the program exits so you can read the summary.
echo
echo "Receiver stopped. Press Enter to close this window."
read -r _
