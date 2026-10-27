#!/usr/bin/env bash
#
# make_rx_gui_shortcut.sh - Generate a desktop shortcut that launches the RX
# receiver straight into the Tkinter GUI (python3 RX_Temenetry.py --gui).
#
# RUN THIS ON THE RASPBERRY PI 3 (ground station), from a terminal:
#     cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
#     chmod +x make_rx_gui_shortcut.sh
#     ./make_rx_gui_shortcut.sh
#
# It auto-detects the install path, writes a .desktop file to your Desktop, and
# marks it trusted/executable so a double-click launches the GUI directly.
#
set -euo pipefail

# Absolute path to this script's directory = where RX_Temenetry.py lives.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RX_SCRIPT="${SCRIPT_DIR}/RX_Temenetry.py"

if [[ ! -f "$RX_SCRIPT" ]]; then
    echo "ERROR: RX_Temenetry.py not found next to this script (${SCRIPT_DIR})." >&2
    exit 1
fi

# Pick the user's Desktop folder (fall back to ~/Desktop).
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$DESKTOP_DIR"
SHORTCUT="${DESKTOP_DIR}/Rocket-RX-GUI.desktop"

echo "Install dir : ${SCRIPT_DIR}"
echo "Desktop     : ${DESKTOP_DIR}"
echo "Writing     : ${SHORTCUT}"

# The GUI (--gui) creates its own Tkinter window, so no terminal wrapper is
# needed. We run python3 directly with the working directory set so the script
# finds everything it needs.
cat > "$SHORTCUT" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Rocket RX (GUI)
Comment=Receive and display live rocket telemetry in a window
Exec=python3 "${RX_SCRIPT}" --gui
Path=${SCRIPT_DIR}
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
EOF

chmod +x "$SHORTCUT"

# On the PIXEL/LXDE desktop, mark the launcher "trusted" so it runs on a
# double-click without the "Execute / Display" prompt. Harmless if unsupported.
if command -v gio >/dev/null 2>&1; then
    gio set "$SHORTCUT" metadata::trusted true 2>/dev/null || true
fi

echo
echo "Done. A 'Rocket RX (GUI)' icon is now on your Desktop."
echo "Double-click it to start the receiver with the live GUI window."
echo
echo "Note: the GUI needs the Pi's desktop + a monitor (or a VNC session)."
echo "If double-click asks, choose 'Execute'. Over headless SSH use instead:"
echo "    cd \"${SCRIPT_DIR}\" && python3 RX_Temenetry.py"
