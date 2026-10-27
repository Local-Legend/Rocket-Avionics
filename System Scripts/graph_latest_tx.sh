#!/usr/bin/env bash
# Open graphs for the newest TX-local telemetry CSV.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/home/pi/libs-venv/bin/python3"
PLAYBACK="$SCRIPT_DIR/playback.py"

latest="$(find /home/pi/Desktop -maxdepth 2 -type f -name 'telemetry_*.csv' \
    -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)"

if [[ -z "$latest" ]]; then
    echo "No TX telemetry CSV was found under /home/pi/Desktop."
    read -r -p "Press Enter to close..." _
    exit 1
fi

if [[ ! -x "$PYTHON" || ! -f "$PLAYBACK" ]]; then
    echo "Playback environment is incomplete."
    echo "Python: $PYTHON"
    echo "Script : $PLAYBACK"
    read -r -p "Press Enter to close..." _
    exit 1
fi

cd "$SCRIPT_DIR" || exit 1
echo "Opening graphs for:"
echo "$latest"
"$PYTHON" "$PLAYBACK" --tx-log "$latest" --graphs
status=$?

echo
echo "Graph viewer exited with status $status."
read -r -p "Press Enter to close..." _
exit "$status"
