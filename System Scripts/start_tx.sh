#!/usr/bin/env bash
# Launch TX_Telemetry.py manually on the Raspberry Pi Zero 2 W.
# This is intentionally a desktop/manual launcher; it does not enable autostart.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/home/pi/libs-venv/bin/python3"
TX_SCRIPT="$SCRIPT_DIR/TX_Telemetry.py"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: TX Python interpreter not found: $PYTHON"
    echo "See README.md for the TX environment setup."
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [[ ! -f "$TX_SCRIPT" ]]; then
    echo "ERROR: TX script not found: $TX_SCRIPT"
    read -r -p "Press Enter to close..." _
    exit 1
fi

cd "$SCRIPT_DIR" || exit 1

echo "Rocket TX Telemetry"
echo "==================="
echo "Manual launch only; boot autostart is not enabled."
echo "Press Ctrl+C to stop and finalize any active local video."
echo

"$PYTHON" "$TX_SCRIPT"
status=$?

echo
echo "TX stopped with exit status $status."
read -r -p "Press Enter to close..." _
exit "$status"
