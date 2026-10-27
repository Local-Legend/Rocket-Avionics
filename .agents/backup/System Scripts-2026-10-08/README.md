# Rocket Avionics - System Scripts

Two computers, two deployments. Nothing here runs on your Mac; these are files
you transfer to the Raspberry Pis.

- **TX = Raspberry Pi Zero** (on the rocket): reads sensors, records the camera
  locally, transmits telemetry over LoRa. Autostarts on boot.
- **RX = Raspberry Pi 3** (ground station): receives telemetry, decodes it,
  stores it in SQLite, and shows a live display. Launched by a desktop
  double-click.

## Files and where they go

| File                       | Goes to   | Purpose                                             |
|----------------------------|-----------|-----------------------------------------------------|
| `TX_Telemetry.py`          | Pi Zero   | Flight script: sensors + camera + LoRa transmit     |
| `rocket-tx.service`        | Pi Zero   | systemd unit for autostart on boot                  |
| `install_tx_autostart.sh`  | Pi Zero   | One-time installer for the autostart service        |
| `RX_Temenetry.py`          | Pi 3      | All-in-one receiver + decode + DB + live display    |
| `telemetry_decode.py`      | Pi 3      | Decode module (used by `playback.py`)               |
| `telemetry_db.py`          | Pi 3      | SQLite storage module (used by `playback.py`)       |
| `playback.py`              | Pi 3      | Post-flight replay from the database                |
| `start_rx.sh`             | Pi 3      | Launcher that `Rocket-RX.desktop` calls             |
| `Rocket-RX.desktop`        | Pi 3      | Double-click desktop launcher                       |

All deploy paths below assume the project lives at
`/home/pi/Desktop/Avionics for Rocket/` on each Pi. If you put it elsewhere,
update the absolute paths in `rocket-tx.service`, `Rocket-RX.desktop`, and the
`--db` default (currently `/home/pi/Desktop/RxTelemetry.db`).

---

## TX side (Pi Zero) - autostart on boot

### 1. Copy the project to the Pi Zero

From your Mac (replace the hostname/IP with your Pi Zero's):

```bash
scp -r "/Users/ajarretm/Desktop/Avionics for Rocket" pi@raspberrypi-zero.local:/home/pi/Desktop/
```

### 2. Install Python dependencies on the Pi Zero

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-smbus python3-serial i2c-tools
pip3 install pynmea2 pyLoraRFM9x
```

Make sure I2C, SPI, and the serial port are enabled (`sudo raspi-config` ->
Interface Options). The camera must also be enabled.

### 3. Install the autostart service

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
chmod +x install_tx_autostart.sh
sudo ./install_tx_autostart.sh
```

The installer copies `rocket-tx.service` into `/etc/systemd/system/`, reloads
systemd, and enables the service. It refuses to run on anything that is not
Linux-with-systemd, so it is safe if accidentally run on the Mac.

### 4. Test without rebooting

```bash
sudo systemctl start rocket-tx.service
journalctl -u rocket-tx.service -f      # watch live output, Ctrl+C to stop watching
```

Then reboot to confirm it comes up on its own: `sudo reboot`.

Handy commands:

```bash
sudo systemctl status  rocket-tx.service    # current state
sudo systemctl stop    rocket-tx.service    # stop now
sudo systemctl disable rocket-tx.service    # turn OFF autostart
```

> Note: the flight script runs its telemetry loop a fixed number of times and
> then exits, so after it finishes the service goes inactive (this is expected
> with `Restart=on-failure`). If you want it to relaunch continuously, edit
> `rocket-tx.service` and set `Restart=always`.

---

## RX side (Pi 3) - double-click to launch

### 1. Copy the project to the Pi 3

```bash
scp -r "/Users/ajarretm/Desktop/Avionics for Rocket" pi@raspberrypi.local:/home/pi/Desktop/
```

### 2. Install Python dependencies on the Pi 3

```bash
pip3 install pynmea2 pyLoraRFM9x
```

(`RPi.GPIO` ships with Raspberry Pi OS. `tkinter` is only needed for `--gui`
mode: `sudo apt install -y python3-tk`.)

### 3. Make the launcher executable and put it on the desktop

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
chmod +x start_rx.sh
cp Rocket-RX.desktop /home/pi/Desktop/
chmod +x /home/pi/Desktop/Rocket-RX.desktop
```

Now there is a **Rocket RX Telemetry** icon on the Pi 3 desktop. Double-click
it: a terminal opens running the receiver with the live dashboard. Close the
window (or Ctrl+C) to stop; it prints a session summary first.

> If double-click asks whether to execute or display, choose "Execute". On some
> desktops you may need: File Manager -> Edit -> Preferences -> General ->
> "Don't ask options on launch executable file".

### 4. Run modes

`start_rx.sh` launches the default terminal dashboard. To change the display,
edit the `python3 RX_Temenetry.py` line in `start_rx.sh`:

```bash
python3 RX_Temenetry.py            # live terminal dashboard (default)
python3 RX_Temenetry.py --gui      # Tkinter window (needs desktop + monitor)
python3 RX_Temenetry.py --scroll   # plain scrolling log (good over flaky SSH)
```

### 5. Post-flight playback

```bash
cd "/home/pi/Desktop/Avionics for Rocket/System Scripts"
python3 playback.py --list              # list recorded flights
python3 playback.py                     # replay the most recent flight
python3 playback.py --session flight_20260102_143000 --speed 4
```

---

## Database

Both the receiver and playback use one SQLite file (default
`/home/pi/Desktop/RxTelemetry.db`). Each receiver run is a new `session_id`
(one flight), so many flights coexist in the same database.
