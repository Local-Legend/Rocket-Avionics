# Target board : Raspberry Pi Zero 2 W (rocket flight computer, TX side)
# Interpreter  : /home/pi/libs-venv/bin/python3 (venv, include-system-site-packages = true)
# Authority    : Grok_GPSTest.py, Grok_TX_test.py, Grok_Cam_Test.py, bench transcript, user decisions 2026-10-08
import os
import sys
import signal
import serial
import pynmea2
import smbus2 as smbus              # transcript: "import smbus2 as smbus", not "import smbus"
from datetime import datetime
from pathlib import Path
from time import sleep, monotonic
from pyLoraRFM9x import LoRa, ModemConfig

# --- Flight-computer constants (one source line each) ---------------------
# GPS: /dev/serial0 -> ttyAMA0 (PL011) after dtoverlay=disable-bt; see README.
GPS_PORT = "/dev/serial0"           # Grok_GPSTest.py
GPS_BAUD = 9600                     # Grok_GPSTest.py

I2C_BUS      = 1                    # SDA GPIO 2 (pin 3), SCL GPIO 3 (pin 5)
MPU_Address  = 0x68                 # MPU-6050 (i2cdetect 0x68, WHO_AM_I 0x68)
MAG_Address  = 0x1e                 # HMC5883L (genuine; a QMC5883L clone would be 0x0d)
BARO_Address = 0x76                 # BMP280 (chip id 0x58)

# RFM9x LoRa on SPI0 CE1: CS pin 26, G0 GPIO 5 (pin 29), RST GPIO 25 (pin 22).
LORA_SPI_PORT     = 0               # Grok_TX_test.py
LORA_SPI_CHANNEL  = 1               # Grok_TX_test.py (CE1 -> /dev/spidev0.1)
LORA_IRQ_PIN      = 5               # Grok_TX_test.py (G0)
LORA_RESET_PIN    = 25              # Grok_TX_test.py (RST)
LORA_FREQ_MHZ     = 915             # Grok_TX_test.py (US band)
# User override 2026-10-08: flight value 15 dBm. Grok_TX_test.py bench value was 5;
# the library clamps 5..23.
LORA_TX_POWER_DBM = 15
TX_NODE_ADDRESS   = 2               # this transmitter
RX_NODE_ADDRESS   = 1               # ground station (RX_Temenetry.py --node default)

SEA_LEVEL_HPA = 1013.25             # standard atmosphere; absolute altitude reference
FLIGHT_DIR_BASE = Path("/home/pi/Desktop")   # per-run folder flight_<stamp>/ goes here

# Transmit for a fixed flight window instead of a fixed packet count. The
# systemd unit sets TX_FLIGHT_DURATION_S=480 and relaunches when it ends.
FLIGHT_DURATION_S = float(os.environ.get("TX_FLIGHT_DURATION_S", 8 * 60))


def _on_sigterm(signum, frame):
    # `systemctl stop` sends SIGTERM; turn it into a clean exit so the
    # `finally:` block below runs and the video file is finalised.
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _on_sigterm)

# --- Onboard camera (ov5647, Camera Module v1, 2592x1944) -----------------
# The camera records video LOCALLY to the Pi Zero 2 W only. Its footage is
# never added to the telemetry message and never sent over the LoRa radio. It
# runs in the background (its own thread) while the telemetry loop below reads
# sensors and transmits. picamera2 only; the legacy picamera module does not
# work on current Raspberry Pi OS. Stills were bench-verified with
# Grok_Cam_Test.py; the H.264 video path below uses the same library and
# lifecycle but has NOT been bench-verified yet.
# Camera is secondary to the telemetry data. 720p30 @ 4 Mbps roughly halves
# both the GPU encode load and the SD-card write rate versus 1080p/8 Mbps,
# leaving the Zero 2 W's cores free for fast, even sensor sampling.
CAM_RESOLUTION = (1280, 720)
CAM_FRAMERATE  = 30
CAM_BITRATE    = 4_000_000       # ~4 Mbps H.264 (~240 MB for an 8 min run)
CAM_ROTATION   = 180             # match the rest of the avionics orientation


def start_camera():
    """Start a non-blocking local H.264 recording.

    Returns (picam2, path), or (None, None) if the camera or picamera2 is
    unavailable (fail-soft: telemetry must still fly without video).
    """
    picam2 = None
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        # Folder convention from Grok_Cam_Test.py: /home/pi/Desktop/<name>_<stamp>/
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder = FLIGHT_DIR_BASE / "flight_{}".format(stamp)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "flight_{}.h264".format(stamp)

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
        picam2.start_recording(H264Encoder(bitrate=CAM_BITRATE), FileOutput(str(path)))
        return picam2, str(path)
    except Exception as e:
        print("Camera unavailable:", e)
        if picam2 is not None:
            try:
                picam2.close()
            except Exception:
                pass
        return None, None


# --- GPS (UART) ------------------------------------------------------------
# timeout=0 makes reads non-blocking so the GPS never stalls the sensor loop.
try:
    serialPort = serial.Serial(GPS_PORT, GPS_BAUD, timeout=0)
except (OSError, serial.SerialException) as e:
    print("GPS unavailable:", e)
    serialPort = None

# Holds the most recent GGA line seen from the GPS. The GPS only emits a new
# fix ~1 Hz, so between fixes we simply reuse the last line. This decouples
# the (slow, 1 Hz) GPS from the (fast) sensor sampling below.
_gps_buffer = b""
last_gps_line = ""


def poll_gps():
    """Drain any bytes waiting from the GPS and keep the latest GGA line.

    Non-blocking: reads only what is already in the serial buffer, so it adds
    negligible time to each loop pass. Returns the most recent GGA sentence
    (which may be unchanged from the previous call if no new fix has arrived
    yet), or "" if none has been seen.

    Rules from Grok_GPSTest.py / the bench transcript: readline bytes are NOT
    valid UTF-8 (noise, partial lines), so decode as ascii with errors ignored;
    accept only lines starting with "$"; the RX decoder parses only GGA, so
    keep only GGA (lat/lon/alt/fix/sats). $GNZDA is time only, $GPTXT is chat.
    """
    global _gps_buffer, last_gps_line
    if serialPort is None:
        return last_gps_line
    try:
        waiting = serialPort.in_waiting
        if waiting:
            _gps_buffer += serialPort.read(waiting)
            if b"\n" in _gps_buffer:
                lines = _gps_buffer.split(b"\n")
                _gps_buffer = lines[-1]            # keep the partial remainder
                # Newest first; the first GGA line wins.
                for ln in reversed(lines[:-1]):
                    text = ln.decode("ascii", errors="ignore").strip()
                    if text.startswith("$") and "GGA" in text:
                        last_gps_line = text
                        break
            if len(_gps_buffer) > 4096:
                # No newline for a long time: baud/clock garbage. Drop it.
                _gps_buffer = b""
    except (OSError, serial.SerialException):
        pass
    return last_gps_line


# --- I2C sensors -----------------------------------------------------------
try:
    bus = smbus.SMBus(I2C_BUS)
except OSError as e:
    print("I2C bus unavailable:", e)
    bus = None

SMPLRT_DIV   = 0x19
PWR_MGMT_1   = 0x6B
CONFIG       = 0x1A
GYRO_CONFIG  = 0x1B
ACCEL_CONFIG = 0x1C
INT_ENABLE   = 0x38
ACCEL_XOUT_H = 0x3B
ACCEL_YOUT_H = 0x3D
ACCEL_ZOUT_H = 0x3F
TEMP_OUT_H   = 0x41
GYRO_XOUT_H  = 0x43
GYRO_YOUT_H  = 0x45
GYRO_ZOUT_H  = 0x47
WHO_AM_I     = 0x75
ACCEL_RANGE_16G = 0x18           # mpu60501shot.py: ACCEL_RANGE_16G -> 2048 LSB/g

Register_A   = 0                 # HMC5883L
Register_B   = 0x01
Register_mode= 0x02
X_axis_H     = 0x03              # data register order is X, Z, Y (transcript)
Z_axis_H     = 0x05
Y_axis_H     = 0x07


def read_MPU_data(addr):
    #Accelero and Gyro value are 16-bit
    high = bus.read_byte_data(MPU_Address, addr)
    low = bus.read_byte_data(MPU_Address, addr+1)

    #concatenate higher and lower value
    value = ((high << 8) | low)

    #to get signed value from mpu6050 (0x8000 must become -32768)
    if(value >= 32768):
        value = value - 65536
    return value


def read_MAG_data(addr):
    #Read raw 16-bit value
    high = bus.read_byte_data(MAG_Address, addr)
    low = bus.read_byte_data(MAG_Address, addr+1)

    #concatenate higher and lower value
    value = ((high << 8) | low)

    #to get signed value from module (0x8000 must become -32768)
    if(value >= 32768):
        value = value - 65536
    return value


def init_imu():
    """Configure the MPU-6050. Returns True if it is usable."""
    if bus is None:
        return False
    try:
        #MPU write to sample rate register
        bus.write_byte_data(MPU_Address, SMPLRT_DIV, 7)
        sleep(.2)
        #MPU Write to power management register
        bus.write_byte_data(MPU_Address, PWR_MGMT_1, 1)
        sleep(.2)
        #MPU Write to Configuration register
        bus.write_byte_data(MPU_Address, CONFIG, 0)
        sleep(.2)
        #MPU Write to Gyro configuration register (24 = 0x18 = +/-2000 dps, 16.4 LSB/dps)
        bus.write_byte_data(MPU_Address, GYRO_CONFIG, 24)
        sleep(.2)
        #MPU Write to Accel configuration register: +/-16 g -> 2048 LSB/g (user
        #decision 2026-10-08). The decoders in telemetry_decode.py AND
        #RX_Temenetry.py must match (ACCEL_SCALE_MODIFIER_16G = 2048.0).
        bus.write_byte_data(MPU_Address, ACCEL_CONFIG, ACCEL_RANGE_16G)
        sleep(.2)
        #MPU Write to interrupt enable register
        bus.write_byte_data(MPU_Address, INT_ENABLE, 1)
        sleep(.2)
        who = bus.read_byte_data(MPU_Address, WHO_AM_I)
        if who != 0x68:
            print("MPU-6050 WHO_AM_I unexpected: 0x{:02x} (wanted 0x68)".format(who))
        # The first sample after wake is garbage (raw temp 0 shows as exactly
        # 36.53 C on the RX); read and discard one full set.
        sleep(0.1)
        for reg in (ACCEL_XOUT_H, ACCEL_YOUT_H, ACCEL_ZOUT_H,
                    GYRO_XOUT_H, GYRO_YOUT_H, GYRO_ZOUT_H, TEMP_OUT_H):
            read_MPU_data(reg)
        return True
    except OSError as e:
        print("MPU-6050 unavailable:", e)
        return False


def init_mag():
    """Configure the HMC5883L. Returns True if it is usable."""
    if bus is None:
        return False
    try:
        #MAG write to Configuration Register A
        bus.write_byte_data(MAG_Address, Register_A, 0x70)
        sleep(.2)
        #MAG Write to Configuration Register B for gain
        bus.write_byte_data(MAG_Address, Register_B, 0xa0)
        sleep(.2)
        #MAG Write to mode Register for selecting mode
        bus.write_byte_data(MAG_Address, Register_mode, 0)
        sleep(.2)
        return True
    except OSError as e:
        print("HMC5883L unavailable:", e)
        return False


def pressure_to_altitude_m(p_hpa, t_c, qnh_hpa):
    """Hypsometric altitude (same formula as Pimoroni BMP280.get_altitude)."""
    return ((qnh_hpa / p_hpa) ** (1.0 / 5.257) - 1.0) * (t_c + 273.15) / 0.0065


def init_baro():
    """Set up the BMP280 and capture the pad baseline pressure.

    Returns (chip, pad_pressure_hpa), or (None, None) if unavailable.
    Bench facts (transcript): the first conversion after init is incomplete
    (e.g. 724 hPa / 3009 m) so one reading is discarded; good values are
    ~1011.7 hPa, ~13 m vs 1013.25 hPa at Cocoa. Zero altitude on the pad.
    """
    try:
        from smbus2 import SMBus
        from bmp280 import BMP280
        chip = BMP280(i2c_dev=SMBus(I2C_BUS), i2c_addr=BARO_Address)
        # 62.5 ms standby (~7 Hz updates) instead of the 500 ms default so AGL
        # tracks a rocket; oversampling x16 as the library default. Tuning
        # change vs the bench default; confirm `baro=` updates in journalctl.
        chip.setup(mode="normal", temperature_oversampling=16,
                   pressure_oversampling=16, temperature_standby=62.5)
        sleep(1)
        chip.get_pressure()                 # discard the first (incomplete) conversion
        samples = []
        for _ in range(10):
            samples.append(chip.get_pressure())
            sleep(0.1)
        pad_pressure_hpa = sum(samples) / len(samples)
        print("BMP280 pad baseline: {:.2f} hPa".format(pad_pressure_hpa))
        return chip, pad_pressure_hpa
    except Exception as e:
        print("BMP280 unavailable:", e)
        return None, None


def read_baro():
    """Return (temp_c, press_hpa, alt_m, agl_m) or (None, None, None, None)."""
    global baro_errors
    if baro is None:
        return None, None, None, None
    try:
        t_c = baro.get_temperature()
        p_hpa = baro.get_pressure()
        alt_m = pressure_to_altitude_m(p_hpa, t_c, SEA_LEVEL_HPA)
        agl_m = pressure_to_altitude_m(p_hpa, t_c, pad_pressure_hpa)
        return t_c, p_hpa, alt_m, agl_m
    except Exception:
        baro_errors += 1
        return None, None, None, None


imu_ok = init_imu()
mag_ok = init_mag()
baro, pad_pressure_hpa = init_baro()
baro_errors = 0

# --- LoRa radio (the one HARD failure: without it there is no telemetry) ---
try:
    lora = LoRa(spi_channel=LORA_SPI_CHANNEL,   # CS on CE1, physical pin 26
                interrupt_pin=LORA_IRQ_PIN,     # G0
                reset_pin=LORA_RESET_PIN,       # RST
                spi_port=LORA_SPI_PORT,
                my_address=TX_NODE_ADDRESS,
                freq=LORA_FREQ_MHZ,
                tx_power=LORA_TX_POWER_DBM,
                modem_config=ModemConfig.Bw125Cr45Sf128,   # RX must use the same
                acks=False)
except Exception as e:
    # "AssertionError: LoRa initialization failed" = SPI could not read back
    # sleep mode: a CS/MOSI/MISO/SCK wiring fault, not a frequency problem.
    print("LoRa init failed (check CS/MOSI/MISO/SCK wiring, not frequency):", e)
    sys.exit(1)


def on_recv(payload):            # This is our callback function that runs when a message is received
    # Runs on the lgpio interrupt thread that also services TX_DONE, so it
    # must never raise.
    try:
        print("RX:", payload.message.decode("ascii", errors="replace"))
    except Exception as e:
        print("on_recv error:", e)


lora.on_recv = on_recv #When message is recieved do this

# Start the onboard camera recording AFTER the radio (so a radio wiring fault
# that exits the script never leaves half-written video files) and BEFORE the
# telemetry loop. It runs in the background and writes video to the Pi locally;
# it is not transmitted.
camera, video_path = start_camera()
if camera is not None:
    print("Camera recording locally to:", video_path)

packets_sent = 0
send_failures = 0
i2c_errors = 0

try:
    # Sample on a fixed 10 Hz schedule. Driving the loop off absolute
    # time.monotonic() deadlines (rather than a plain sleep) keeps the rate
    # even when a given pass takes a bit longer, so the data is evenly spaced
    # in time -- important for capturing the fast boost phase and any roll
    # rate. The Zero 2 W has four Cortex-A53 cores, so a pass is quick; the
    # real ceiling is the LoRa air time (~235 ms per ~145-byte packet at
    # SF7/BW125, i.e. ~4 Hz). The deadline scheduler absorbs the overrun.
    SAMPLE_RATE_HZ = 10
    SAMPLE_PERIOD  = 1.0 / SAMPLE_RATE_HZ

    start_time = monotonic()
    next_sample = start_time

    # Bench-test rate meter: once per second, print the ACTUAL achieved
    # packets/sec so you can see whether the loop really hits SAMPLE_RATE_HZ.
    rate_window_start = start_time
    rate_window_count = 0

    fmt = lambda v: "" if v is None else "{:.2f}".format(v)

    while (monotonic() - start_time) < FLIGHT_DURATION_S:

        #Read Accelerometer, Gyroscope and Temperature raw values
        acc_x = acc_y = acc_z = gyro_x = gyro_y = gyro_z = temp = 0
        if imu_ok:
            try:
                acc_x = read_MPU_data(ACCEL_XOUT_H)
                acc_y = read_MPU_data(ACCEL_YOUT_H)
                acc_z = read_MPU_data(ACCEL_ZOUT_H)
                gyro_x = read_MPU_data(GYRO_XOUT_H)
                gyro_y = read_MPU_data(GYRO_YOUT_H)
                gyro_z = read_MPU_data(GYRO_ZOUT_H)
                temp = read_MPU_data(TEMP_OUT_H)
            except OSError:
                acc_x = acc_y = acc_z = gyro_x = gyro_y = gyro_z = temp = 0
                i2c_errors += 1

        #Read Magnetometer raw value (register order X, Z, Y)
        mag_x = mag_y = mag_z = 0
        if mag_ok:
            try:
                mag_x = read_MAG_data(X_axis_H)
                mag_z = read_MAG_data(Z_axis_H)
                mag_y = read_MAG_data(Y_axis_H)
            except OSError:
                mag_x = mag_y = mag_z = 0
                i2c_errors += 1

        #Read Barometer (BMP280): temp C, pressure hPa, altitude m, AGL m
        baro_t, baro_p, baro_alt, baro_agl = read_baro()

        # Non-blocking GPS: reuse the latest GGA fix (GPS only updates ~1 Hz).
        GPS_strg = poll_gps()

        # Packet layout (keep identical in telemetry_decode.py and RX_Temenetry.py):
        #   0-9   acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp, mag_x, mag_y, mag_z (raw ints)
        #   10-13 baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m ("%.2f", empty if unavailable)
        #   14..  raw GGA sentence (contains commas; may be empty)
        message = (",".join(str(v) for v in (acc_x, acc_y, acc_z,
                                             gyro_x, gyro_y, gyro_z, temp,
                                             mag_x, mag_y, mag_z))
                   + "," + ",".join(fmt(v) for v in (baro_t, baro_p, baro_alt, baro_agl))
                   + "," + GPS_strg)

        try:
            status = lora.send(message, RX_NODE_ADDRESS)
        except Exception:
            status = False
        if status:
            packets_sent += 1
        else:
            send_failures += 1

        # --- rate meter: report achieved packets/sec once per second ---
        rate_window_count += 1
        now = monotonic()
        elapsed = now - rate_window_start
        if elapsed >= 1.0:
            actual_hz = rate_window_count / elapsed
            gps_state = "nofix"
            if GPS_strg:
                try:
                    msg = pynmea2.parse(GPS_strg)
                    gps_state = "fix sats={}".format(msg.num_sats) if int(msg.gps_qual) else "nofix"
                except Exception:
                    gps_state = "bad"
            if baro_p is not None:
                baro_state = "{:.1f}hPa agl={:+.1f}m".format(baro_p, baro_agl)
            else:
                baro_state = "n/a"
            print("t={:5.0f}s  pkts={:<6d}  rate={:.1f} Hz (target {} Hz)  gps={}  baro={}  i2c_err={}  tx_fail={}".format(
                now - start_time, packets_sent, actual_hz, SAMPLE_RATE_HZ,
                gps_state, baro_state, i2c_errors + baro_errors, send_failures))
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
    # Stop the camera cleanly so the video file is finalised. No GPIO cleanup
    # call is needed: pyLoraRFM9x uses lgpio and releases it itself.
    if camera is not None:
        try:
            camera.stop_recording()
            camera.close()
            print("Camera recording saved:", video_path)
        except Exception as e:
            print("Camera shutdown error:", e)
    print("TX done: {} packets sent, {} send failures, {} sensor errors, video: {}".format(
        packets_sent, send_failures, i2c_errors + baro_errors, video_path or "none"))
