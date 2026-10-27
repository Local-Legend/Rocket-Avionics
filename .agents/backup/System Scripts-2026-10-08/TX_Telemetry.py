import RPi.GPIO as GPIO
import serial
import pynmea2
import smbus
import os
from datetime import datetime
from time import sleep, monotonic
import math
from pyLoraRFM9x import LoRa, ModemConfig

# --- Onboard camera (ArduCam OV5647) -------------------------------------
# The camera records video LOCALLY to the Pi Zero only. Its footage is never
# added to the telemetry message and never sent over the LoRa radio. It runs
# in the background (its own thread) while the telemetry loop below reads
# sensors and transmits.
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

CAM_RECORD_DIR = "/home/pi/Desktop/Camera"
# Camera is secondary to the telemetry data. 720p30 @ 4 Mbps roughly halves
# both the GPU encode load and the SD-card write rate versus 1080p/8 Mbps,
# leaving the Zero's single core free for fast, even sensor sampling.
CAM_RESOLUTION = (1280, 720)
CAM_FRAMERATE  = 30
CAM_BITRATE    = 4_000_000       # ~4 Mbps H.264 (~240 MB for an 8 min run)
CAM_ROTATION   = 180             # match the rest of the avionics orientation


def start_camera():
    """Start a non-blocking local H.264 recording. Returns (picam2, path)."""
    os.makedirs(CAM_RECORD_DIR, exist_ok=True)
    picam2 = Picamera2()

    try:
        from libcamera import Transform
        transform = Transform(hflip=1, vflip=1) if CAM_ROTATION == 180 else Transform()
    except Exception:
        transform = None

    config_kwargs = {"main": {"size": CAM_RESOLUTION}}
    if transform is not None:
        config_kwargs["transform"] = transform

    video_config = picam2.create_video_configuration(**config_kwargs)
    video_config["controls"]["FrameRate"] = CAM_FRAMERATE
    picam2.configure(video_config)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(CAM_RECORD_DIR, "flight_{}.h264".format(stamp))
    picam2.start_recording(H264Encoder(bitrate=CAM_BITRATE), FileOutput(path))
    return picam2, path

# timeout=0 makes reads non-blocking so the GPS never stalls the sensor loop.
serialPort = serial.Serial("/dev/ttyS0", 9600, timeout=0)
bus = smbus.SMBus(1)

# Holds the most recent full NMEA line seen from the GPS. The GPS only emits a
# new fix ~1 Hz, so between fixes we simply reuse the last line. This decouples
# the (slow, 1 Hz) GPS from the (fast) sensor sampling below.
_gps_buffer = b""
last_gps_line = ""


def poll_gps():
    """Drain any bytes waiting from the GPS and keep the latest complete line.

    Non-blocking: reads only what is already in the serial buffer, so it adds
    negligible time to each loop pass. Returns the most recent NMEA line (which
    may be unchanged from the previous call if no new fix has arrived yet).
    """
    global _gps_buffer, last_gps_line
    try:
        waiting = serialPort.in_waiting
        if waiting:
            _gps_buffer += serialPort.read(waiting)
            # Keep only the newest complete line; discard older ones.
            if b"\n" in _gps_buffer:
                lines = _gps_buffer.split(b"\n")
                _gps_buffer = lines[-1]            # keep the partial remainder
                for ln in reversed(lines[:-1]):
                    text = ln.decode("utf-8", errors="replace").strip()
                    if text:
                        last_gps_line = text
                        break
    except Exception:
        pass
    return last_gps_line
MPU_Address = 0x68              #MPU6050 device address
SMPLRT_DIV   = 0x19
PWR_MGMT_1   = 0x6B
CONFIG       = 0x1A
GYRO_CONFIG  = 0x1B
INT_ENABLE   = 0x38
ACCEL_XOUT_H = 0x3B
ACCEL_YOUT_H = 0x3D
ACCEL_ZOUT_H = 0x3F
TEMP_OUT_H   = 0x41
GYRO_XOUT_H  = 0x43
GYRO_YOUT_H  = 0x45
GYRO_ZOUT_H  = 0x47

MAG_Address  = 0x1e              #HMC5883L magnetometer device address
Register_A   = 0
Register_B   = 0x01
Register_mode= 0x02
X_axis_H     = 0x03   
Z_axis_H     = 0x05   
Y_axis_H     = 0x07   
declination  = -0.00669
pi           = 3.14159265359     #define pi value

lora = LoRa(1, 5, 2, reset_pin = 25, modem_config=ModemConfig.Bw125Cr45Sf128, tx_power=5, acks=True)

#MPU write to sample rate register
bus.write_byte_data(MPU_Address, SMPLRT_DIV, 7)
sleep(.2)
#MPU Write to power management register
bus.write_byte_data(MPU_Address, PWR_MGMT_1, 1)
sleep(.2)
#MPU Write to Configuration register
bus.write_byte_data(MPU_Address, CONFIG, 0)
sleep(.2)
#MPU Write to Gyro configuration register
bus.write_byte_data(MPU_Address, GYRO_CONFIG, 24)
sleep(.2)
#MPU Write to interrupt enable register
bus.write_byte_data(MPU_Address, INT_ENABLE, 1)
sleep(.2)
#MAG write to Configuration Register A
bus.write_byte_data(MAG_Address, Register_A, 0x70)
sleep(.2)
#MAG Write to Configuration Register B for gain
bus.write_byte_data(MAG_Address, Register_B, 0xa0)
sleep(.2)
#MAG Write to mode Register for selecting mode
bus.write_byte_data(MAG_Address, Register_mode, 0)
sleep(.2)


def on_recv(payload):            # This is our callback function that runs when a message is received
#    print("From:", payload.header_from)
    MSG = payload.message
    print(MSG.decode('utf-8'))
    
#    print("RSSI: {}; SNR: {}".format(payload.rssi, payload.snr))

def read_MPU_data(addr):
    #Accelero and Gyro value are 16-bit
    high = bus.read_byte_data(MPU_Address, addr)
    low = bus.read_byte_data(MPU_Address, addr+1)
    
        #concatenate higher and lower value
    value = ((high << 8) | low)
        
        #to get signed value from mpu6050
    if(value > 32768):
        value = value - 65536
    return value

def read_MAG_data(addr):
    
        #Read raw 16-bit value
        high = bus.read_byte_data(MAG_Address, addr)
        low = bus.read_byte_data(MAG_Address, addr+1)

        #concatenate higher and lower value
        value = ((high << 8) | low)

        #to get signed value from module
        if(value > 32768):
            value = value - 65536
        return value

lora.on_recv = on_recv #When message is recieved do this

# Start the onboard camera recording BEFORE the telemetry loop. This runs in
# the background and writes video to the Pi locally; it is not transmitted.
camera, video_path = start_camera()
print("Camera recording locally to:", video_path)

try:
    # Transmit for a fixed flight window instead of a fixed packet count.
    FLIGHT_DURATION_S = 8 * 60      # 8 minutes

    # Sample on a fixed 10 Hz schedule. Driving the loop off absolute deadlines
    # (rather than a plain sleep) keeps the rate even even when a given pass
    # takes a bit longer, so the data is evenly spaced in time -- important for
    # capturing the fast boost phase and any roll rate.
    SAMPLE_RATE_HZ = 10
    SAMPLE_PERIOD  = 1.0 / SAMPLE_RATE_HZ

    start_time = monotonic()
    next_sample = start_time
    x = 0

    # Bench-test rate meter: once per second, print the ACTUAL achieved
    # packets/sec so you can see whether the loop really hits SAMPLE_RATE_HZ.
    rate_window_start = start_time
    rate_window_count = 0

    while (monotonic() - start_time) < FLIGHT_DURATION_S:

        #Read Accelerometer raw value
        acc_x = read_MPU_data(ACCEL_XOUT_H)
        acc_y = read_MPU_data(ACCEL_YOUT_H)
        acc_z = read_MPU_data(ACCEL_ZOUT_H)

        #Read Gyroscope raw value
        gyro_x = read_MPU_data(GYRO_XOUT_H)
        gyro_y = read_MPU_data(GYRO_YOUT_H)
        gyro_z = read_MPU_data(GYRO_ZOUT_H)

        #Read Temperature raw value
        temp = read_MPU_data(TEMP_OUT_H)

        #Read Magnetometer raw value
        mag_x = read_MAG_data(X_axis_H)
        mag_z = read_MAG_data(Z_axis_H)
        mag_y = read_MAG_data(Y_axis_H)

        # Non-blocking GPS: reuse the latest fix (GPS only updates ~1 Hz).
        GPS_strg = poll_gps()

        message = (str(acc_x) + ',' + str(acc_y) + ',' + str(acc_z) + ',' + str(gyro_x) + ',' + str(gyro_y) + ',' + str(gyro_z) + ',' + str(temp) + ',' + str(mag_x) + ',' + str(mag_y) + ',' + str(mag_z) + ',' + GPS_strg)

        status = lora.send(message, 1)
        x = x + 1

        # --- rate meter: report achieved packets/sec once per second ---
        rate_window_count += 1
        now = monotonic()
        elapsed = now - rate_window_start
        if elapsed >= 1.0:
            actual_hz = rate_window_count / elapsed
            print("t={:5.0f}s  pkts={:<6d}  rate={:.1f} Hz (target {} Hz)".format(
                now - start_time, x, actual_hz, SAMPLE_RATE_HZ))
            rate_window_start = now
            rate_window_count = 0

        # Sleep until the next scheduled sample instant. If we have fallen
        # behind (a pass overran), skip ahead so we don't accumulate drift.
        next_sample += SAMPLE_PERIOD
        delay = next_sample - monotonic()
        if delay > 0:
            sleep(delay)
        else:
            next_sample = monotonic()
finally:
    # Stop the camera cleanly so the video file is finalised, then release GPIO.
    try:
        camera.stop_recording()
        camera.close()
        print("Camera recording saved:", video_path)
    except Exception as e:
        print("Camera shutdown error:", e)
    GPIO.cleanup()