import serial
import pynmea2

def parseGPS(strg):
    if strg.find('GGA') > 0:
        msg = pynmea2.parse(strg)
        print("Timestamp: %s -- Lat: %s %s -- Lon: %s %s -- Altitude: %s %s" % (msg.timestamp,msg.lat,msg.lat_dir,msg.lon,msg.lon_dir,msg.altitude,msg.altitude_units))

serialPort = serial.Serial("/dev/ttyS0", 9600, timeout=0.5)

while True:
    strg = serialPort.readline()
    strg = strg.decode('utf-8')
    parseGPS(strg)