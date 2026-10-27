import serial
import pynmea2

def parseGPS(line):
    if "GGA" not in line:
        return
    try:
        msg = pynmea2.parse(line)
    except pynmea2.ParseError:
        return
    print("Timestamp: %s -- Lat: %s %s -- Lon: %s %s -- Altitude: %s %s -- Sats: %s" % (
    msg.timestamp, msg.lat, msg.lat_dir, msg.lon, msg.lon_dir,
    msg.altitude, msg.altitude_units, msg.num_sats))

serialPort = serial.Serial("/dev/serial0", 9600, timeout=0.5)

while True:
    raw = serialPort.readline()
    line = raw.decode("ascii", errors="ignore").strip()
    if not line.startswith("$"):
        continue
    print(line)
    parseGPS(line)
    
