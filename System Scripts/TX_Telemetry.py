# Target board : Raspberry Pi Zero 2 W (rocket flight computer, TX side)
# Interpreter  : /home/pi/libs-venv/bin/python3 (venv, include-system-site-packages = true)
# Authority    : Grok_GPSTest.py, Grok_TX_test.py, Grok_Cam_Test.py, bench transcript, user decisions 2026-10-08, flight timeline spec 2026-10-08
import csv
import os
import sys
import signal
import time
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

SEA_LEVEL_HPA = 1013.25             # pre-lock fallback; not local-weather calibrated
FLIGHT_DIR_BASE = Path("/home/pi/Desktop")

# Create one durable run folder and telemetry CSV at process startup. Camera
# output, when enabled at GPS lock, uses this same folder and timestamp.
RUN_STAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
RUN_FOLDER = FLIGHT_DIR_BASE / "flight_{}".format(RUN_STAMP)
TELEMETRY_LOG_PATH = RUN_FOLDER / "telemetry_{}.csv".format(RUN_STAMP)
telemetry_log_file = None
telemetry_log_writer = None
telemetry_log_rows = 0
telemetry_log_errors = 0
try:
    RUN_FOLDER.mkdir(parents=True, exist_ok=True)
    telemetry_log_file = open(str(TELEMETRY_LOG_PATH), "w", newline="")
    telemetry_log_writer = csv.writer(telemetry_log_file)
    telemetry_log_writer.writerow(("tx_epoch", "tx_iso", "tx_monotonic", "send_ok", "payload"))
    telemetry_log_file.flush()
    os.fsync(telemetry_log_file.fileno())
    print("Local telemetry log:", TELEMETRY_LOG_PATH)
except Exception as e:
    telemetry_log_errors += 1
    if telemetry_log_file is not None:
        try:
            telemetry_log_file.close()
        except Exception:
            pass
    telemetry_log_file = None
    telemetry_log_writer = None
    print("Local telemetry log unavailable:", e)


def log_packet(payload, send_ok):
    """Append one attempted transmission; fail soft if local storage fails."""
    global telemetry_log_rows, telemetry_log_errors
    global telemetry_log_file, telemetry_log_writer
    if telemetry_log_writer is None:
        return
    try:
        epoch = time.time()
        telemetry_log_writer.writerow((
            "{:.6f}".format(epoch),
            datetime.fromtimestamp(epoch).isoformat(),
            "{:.6f}".format(monotonic()),
            "1" if send_ok else "0",
            payload,
        ))
        telemetry_log_rows += 1
        telemetry_log_file.flush()
        if telemetry_log_rows % 10 == 0:
            os.fsync(telemetry_log_file.fileno())
    except Exception as e:
        telemetry_log_errors += 1
        print("Local telemetry log write failed:", e)
        try:
            telemetry_log_file.close()
        except Exception:
            pass
        telemetry_log_file = None
        telemetry_log_writer = None

# Avionics window in seconds measured from T-0 (systemd unit sets
# TX_FLIGHT_DURATION_S=480); after it the TX goes to RECOVERY and keeps sending
# GPS-only packets until power off.
FLIGHT_DURATION_S = float(os.environ.get("TX_FLIGHT_DURATION_S", 8 * 60))

# Local video duration measured from T-0. The camera starts at GPS lock (T-30),
# records through launch, then finalises at T+3 minutes. No photo or video bytes
# are ever included in the LoRa telemetry packet.
CAMERA_STOP_TPLUS_S = float(os.environ.get("TX_CAMERA_STOP_TPLUS_S", 3 * 60))

# --- Flight timeline (spec 2026-10-08) -------------------------------------
COUNTDOWN_S       = 30.0            # T clock starts at T-30 when GPS lock is acquired
RECOVERY_PERIOD_S = 1.0             # GPS-only packet period (s) once the avionics window has elapsed
GYRO_CALIBRATION_SAMPLES = 100      # keep payload stationary for ~1 s at TX startup
GYRO_CALIBRATION_DELAY_S = 0.01
PHASE_NAMES = {"P": "PAD", "C": "COUNTDOWN", "F": "FLIGHT", "R": "RECOVERY"}

# Decimal status_flags bitmask shared with both RX decoders.
IMU_AVAILABLE = 1 << 0
MAG_AVAILABLE = 1 << 1
BARO_AVAILABLE = 1 << 2
GPS_AVAILABLE = 1 << 3
GPS_LOCKED = 1 << 4
CAMERA_AVAILABLE = 1 << 5
CAMERA_RECORDING = 1 << 6
BARO_GPS_REFERENCED = 1 << 7
GPS_FRESH_S = 3.0


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
CAM_BITRATE    = 4_000_000       # ~4 Mbps H.264 (~90 MB for 3 min after T-0, plus T-30)
CAM_ROTATION   = 180             # match the rest of the avionics orientation
CAMERA_DISABLED = os.environ.get("TX_DISABLE_CAMERA", "0").lower() in ("1", "true", "yes")


def init_camera():
    """Configure the camera without recording; return its handle or None."""
    picam2 = None
    try:
        from picamera2 import Picamera2

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
        return picam2
    except Exception as e:
        print("Camera unavailable:", e)
        if picam2 is not None:
            try:
                picam2.close()
            except Exception:
                pass
        return None


def start_camera():
    """Start the configured camera recording; return its output path or None."""
    global camera_ok
    if camera is None:
        return None
    try:
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        path = RUN_FOLDER / "flight_{}.h264".format(RUN_STAMP)
        camera.start_recording(H264Encoder(bitrate=CAM_BITRATE), FileOutput(str(path)))
        return str(path)
    except Exception as e:
        print("Camera recording unavailable:", e)
        camera_ok = False
        try:
            camera.close()
        except Exception:
            pass
        return None


# Camera is configured after the required LoRa radio initializes. Recording
# still starts only at the PAD -> COUNTDOWN transition.
camera = None
camera_ok = False
camera_recording = False
video_path = None


def stop_camera():
    """Stop the recording and finalise the video file. Idempotent.

    Called when the local-video window reaches T+3 minutes and again from
    `finally:` (SIGTERM / crash), so a second call must be a no-op.
    """
    global camera, camera_recording
    if camera is not None:
        try:
            if camera_recording:
                camera.stop_recording()
            camera.close()
            if video_path:
                print("Camera recording saved:", video_path)
        except Exception as e:
            print("Camera shutdown error:", e)
        finally:
            camera_recording = False
            camera = None


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
last_gps_update_mono = None


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
    global _gps_buffer, last_gps_line, last_gps_update_mono
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
                        last_gps_update_mono = monotonic()
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


def calibrate_gyro_bias():
    """Average stationary gyro readings and return raw X/Y/Z zero offsets."""
    print("Gyro calibration: keep the payload stationary and vertical for 1 second...")
    sums = [0, 0, 0]
    try:
        for _ in range(GYRO_CALIBRATION_SAMPLES):
            sums[0] += read_MPU_data(GYRO_XOUT_H)
            sums[1] += read_MPU_data(GYRO_YOUT_H)
            sums[2] += read_MPU_data(GYRO_ZOUT_H)
            sleep(GYRO_CALIBRATION_DELAY_S)
    except OSError as e:
        print("Gyro calibration failed; using zero offsets:", e)
        return (0, 0, 0)
    offsets = tuple(int(round(total / float(GYRO_CALIBRATION_SAMPLES))) for total in sums)
    print("Gyro zero offsets (raw): X={} Y={} Z={}".format(*offsets))
    return offsets


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
    """Set up the BMP280. Returns the chip, or None if unavailable.

    Bench facts (transcript): the first conversion after init is incomplete
    (e.g. 724 hPa / 3009 m) so one reading is discarded; good values are
    ~1011.7 hPa, ~13 m vs 1013.25 hPa at Cocoa. The pad baseline is NOT
    captured here any more: see capture_pad_baseline(), called at GPS lock.
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
        return chip
    except Exception as e:
        print("BMP280 unavailable:", e)
        return None


def capture_pad_baseline():
    """Mean of 10 BMP280 pressure readings 0.1 s apart (zero altitude on the pad).

    Called in the PAD -> COUNTDOWN transition, when the rocket is definitely
    still on the pad. Blocks ~1 s. Returns the baseline in hPa, or None if the
    barometer is unavailable or the capture fails (fail-soft: AGL stays empty).
    """
    if baro is None:
        return None
    try:
        samples = []
        for _ in range(10):
            samples.append(baro.get_pressure())
            sleep(0.1)
        baseline = sum(samples) / len(samples)
        print("BMP280 pad baseline: {:.2f} hPa".format(baseline))
        return baseline
    except Exception as e:
        print("BMP280 pad baseline unavailable:", e)
        return None


def read_baro():
    """Return (chip temp C, pressure hPa, absolute altitude m, AGL m)."""
    global baro_errors, baro_ok
    if baro is None:
        return None, None, None, None
    try:
        t_c = baro.get_temperature()
        p_hpa = baro.get_pressure()
        standard_alt_m = pressure_to_altitude_m(p_hpa, t_c, SEA_LEVEL_HPA)
        if pad_pressure_hpa is None:
            return t_c, p_hpa, standard_alt_m, None
        agl_m = pressure_to_altitude_m(p_hpa, t_c, pad_pressure_hpa)
        # After lock, anchor pressure-relative AGL to the GPS pad altitude.
        # If that GGA omitted altitude, retain the standard-atmosphere fallback.
        alt_m = pad_gps_alt_m + agl_m if pad_gps_alt_m is not None else standard_alt_m
        return t_c, p_hpa, alt_m, agl_m
    except Exception:
        baro_errors += 1
        baro_ok = False
        return None, None, None, None


imu_ok = init_imu()
gyro_bias_x, gyro_bias_y, gyro_bias_z = calibrate_gyro_bias() if imu_ok else (0, 0, 0)
mag_ok = init_mag()
baro = init_baro()
baro_ok = baro is not None
pad_pressure_hpa = None             # fresh mean captured at this process's GPS lock
pad_gps_alt_m = None                # valid GGA altitude captured at the same lock event
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
                modem_config=ModemConfig.Bw500Cr45Sf128,   # RX must use the same
                acks=False)
    # A typical 140-150-byte telemetry packet takes about 58-62 ms at
    # SF7/BW500/CR4/5. Keep the proven 0.5 s timeout with ample margin.
    lora.wait_packet_sent_timeout = 0.5
except Exception as e:
    # "AssertionError: LoRa initialization failed" = SPI could not read back
    # sleep mode: a CS/MOSI/MISO/SCK wiring fault, not a frequency problem.
    print("LoRa init failed (check CS/MOSI/MISO/SCK wiring, not frequency):", e)
    if telemetry_log_file is not None:
        try:
            telemetry_log_file.flush()
            os.fsync(telemetry_log_file.fileno())
            telemetry_log_file.close()
        except Exception:
            pass
    sys.exit(1)

# Probe/configure the fail-soft camera only after the required radio is ready.
# TX_DISABLE_CAMERA=1 skips camera initialization for telemetry-only bench tests.
if CAMERA_DISABLED:
    print("Camera disabled by TX_DISABLE_CAMERA")
    camera = None
else:
    camera = init_camera()
camera_ok = camera is not None


def on_recv(payload):            # This is our callback function that runs when a message is received
    # Runs on the lgpio interrupt thread that also services TX_DONE, so it
    # must never raise.
    try:
        print("RX:", payload.message.decode("ascii", errors="replace"))
    except Exception as e:
        print("on_recv error:", e)


lora.on_recv = on_recv #When message is recieved do this

# --- Flight timeline state machine (spec 2026-10-08) -----------------------
# PAD ("P")       boot .. GPS lock: full telemetry, no video, T clock blank.
# COUNTDOWN ("C") GPS lock (gps_qual >= 1 on 3 consecutive GGA sentences):
#                 camera starts, BMP280 pad baseline captured, T clock = T-30.
# FLIGHT ("F")    T clock reaches T-0; camera records locally until T+3 min.
# RECOVERY ("R")  T+ reaches FLIGHT_DURATION_S:
#                 IMU / mag / baro no longer read, GPS-only packets at 1 Hz
#                 until power off. T clock keeps counting up.
# Transitions are forward only. The clock runs on time.monotonic() from the
# lock instant, so losing the GPS fix after lock changes nothing.
phase = "P"
lock_streak = 0                 # consecutive new GGA sentences with gps_qual >= 1
last_lock_sentence = ""         # last GGA examined (GGA is ~1 Hz, loop is 7 Hz)
t0_mono = None                  # monotonic() instant of T-0; None until GPS lock

# Loop period; RECOVERY lowers it to RECOVERY_PERIOD_S (advance_phase rebinds it).
SAMPLE_RATE_HZ = 7
SAMPLE_PERIOD  = 1.0 / SAMPLE_RATE_HZ
next_sample = monotonic()


def _gps_utc(gps_line):
    """HH:MM:SS from a GGA sentence for the transition log, or 'n/a'."""
    try:
        ts = pynmea2.parse(gps_line).timestamp
        return str(ts) if ts else "n/a"
    except Exception:
        return "n/a"


def _log_phase(old, new, t_clock, gps_line, reason):
    t_text = "----" if t_clock is None else "{:+.1f}".format(t_clock)
    print("PHASE {} -> {}  T{}  gps_utc={}  {}".format(
        PHASE_NAMES[old], PHASE_NAMES[new], t_text, _gps_utc(gps_line), reason))


def advance_phase(gps_line):
    """Run the lock detection / time-based transitions. Returns t_clock (None in PAD)."""
    global phase, lock_streak, last_lock_sentence, t0_mono
    global pad_pressure_hpa, pad_gps_alt_m, camera, camera_ok, camera_recording, video_path
    global SAMPLE_PERIOD, next_sample

    if phase == "P":
        # Count only NEW GGA sentences (the 7 Hz loop sees each ~1 Hz
        # sentence many times); a qual-0 sentence, parse error or empty line
        # resets the streak so a single flicker never counts as lock.
        if gps_line != last_lock_sentence:
            last_lock_sentence = gps_line
            locked = False
            try:
                locked = int(pynmea2.parse(gps_line).gps_qual) >= 1
            except Exception:
                locked = False
            lock_streak = lock_streak + 1 if locked else 0
            if lock_streak >= 3:
                lock_mono = monotonic()
                t0_mono = lock_mono + COUNTDOWN_S
                phase = "C"
                _log_phase("P", "C", -COUNTDOWN_S, gps_line, "GPS lock (3 consecutive GGA fixes)")
                # Never reuse a baseline in this process: clear both anchors,
                # capture the lock GGA altitude, then take a fresh pressure mean.
                pad_pressure_hpa = None
                pad_gps_alt_m = None
                try:
                    lock_msg = pynmea2.parse(gps_line)
                    if int(lock_msg.gps_qual) >= 1 and lock_msg.altitude not in (None, ""):
                        pad_gps_alt_m = float(lock_msg.altitude)
                        print("GPS pad altitude reference: {:.2f} m".format(pad_gps_alt_m))
                except Exception as e:
                    print("GPS pad altitude reference unavailable; using standard atmosphere:", e)
                pad_pressure_hpa = capture_pad_baseline()
                video_path = start_camera()
                camera_recording = video_path is not None
                if camera_recording:
                    print("Camera recording locally to:", video_path)
        if t0_mono is None:
            return None

    # The same rounded value drives the transitions and goes into the packet,
    # so the phase letter and the clock in one packet never disagree.
    t_clock = round(monotonic() - t0_mono, 1) + 0.0     # + 0.0 turns -0.0 into 0.0
    if phase == "C" and t_clock >= 0.0:
        phase = "F"
        _log_phase("C", "F", t_clock, gps_line, "T-0")
    if phase == "F" and camera_recording and t_clock >= CAMERA_STOP_TPLUS_S:
        print("Camera local-video window reached T+{:.0f} s; finalising video".format(
            CAMERA_STOP_TPLUS_S))
        stop_camera()
    if phase == "F" and t_clock >= FLIGHT_DURATION_S:
        phase = "R"
        _log_phase("F", "R", t_clock, gps_line,
                   "avionics window {:.0f} s elapsed".format(FLIGHT_DURATION_S))
        stop_camera()
        SAMPLE_PERIOD = RECOVERY_PERIOD_S
        next_sample = monotonic()           # no catch-up burst after the change
    return t_clock


packets_sent = 0
send_failures = 0
i2c_errors = 0

try:
    # Sample on a fixed 7 Hz schedule. Driving the loop off absolute
    # time.monotonic() deadlines (rather than a plain sleep) keeps the rate
    # even when a given pass takes a bit longer. The typical 140-150-byte packet
    # needs about 58-62 ms at SF7/BW500/CR4/5, leaving margin at 7 Hz. BW500
    # trades receiver sensitivity and expected range for this higher rate;
    # flight range has not been verified.
    # SAMPLE_PERIOD is a module global (set above) because advance_phase()
    # lowers it to RECOVERY_PERIOD_S (1 s) when the avionics window ends.
    start_time = monotonic()
    next_sample = start_time

    # Bench-test rate meter: once per second, print the ACTUAL achieved
    # packets/sec so you can see whether the loop really hits SAMPLE_RATE_HZ.
    rate_window_start = start_time
    rate_window_count = 0

    fmt = lambda v: "" if v is None else "{:.2f}".format(v)

    # Runs until power is removed (or `systemctl stop` -> SIGTERM -> finally:).
    while True:

        # Non-blocking GPS: reuse the latest GGA fix (GPS only updates ~1 Hz).
        GPS_strg = poll_gps()

        # Phase state machine: lock detection in PAD, T-0 and avionics-window
        # transitions afterwards. t_clock is None until GPS lock.
        t_clock = advance_phase(GPS_strg)

        #Read Accelerometer, Gyroscope and Temperature raw values
        acc_x = acc_y = acc_z = gyro_x = gyro_y = gyro_z = temp = 0
        if imu_ok and phase != "R":
            try:
                acc_x = read_MPU_data(ACCEL_XOUT_H)
                acc_y = read_MPU_data(ACCEL_YOUT_H)
                acc_z = read_MPU_data(ACCEL_ZOUT_H)
                gyro_x = read_MPU_data(GYRO_XOUT_H) - gyro_bias_x
                gyro_y = read_MPU_data(GYRO_YOUT_H) - gyro_bias_y
                gyro_z = read_MPU_data(GYRO_ZOUT_H) - gyro_bias_z
                temp = read_MPU_data(TEMP_OUT_H)
            except OSError:
                acc_x = acc_y = acc_z = gyro_x = gyro_y = gyro_z = temp = 0
                imu_ok = False
                i2c_errors += 1

        #Read Magnetometer raw value (register order X, Z, Y)
        mag_x = mag_y = mag_z = 0
        if mag_ok and phase != "R":
            try:
                mag_x = read_MAG_data(X_axis_H)
                mag_z = read_MAG_data(Z_axis_H)
                mag_y = read_MAG_data(Y_axis_H)
            except OSError:
                mag_x = mag_y = mag_z = 0
                mag_ok = False
                i2c_errors += 1

        #Read Barometer (BMP280): temp C, pressure hPa, altitude m, AGL m
        # (AGL is empty in PAD: the pad baseline is captured at GPS lock.)
        # In RECOVERY the barometer is no longer read: all four fields empty.
        baro_t = baro_p = baro_alt = baro_agl = None
        if phase != "R":
            baro_t, baro_p, baro_alt, baro_agl = read_baro()

        # Recompute explicit component state for every packet. IMU/MAG/BARO
        # bits mean initialized/known hardware even in RECOVERY, where those
        # readings are intentionally inactive. GPS availability requires a
        # recently received GGA sentence; lock is the current GGA fix state.
        gps_fresh = (serialPort is not None and last_gps_update_mono is not None
                     and monotonic() - last_gps_update_mono <= GPS_FRESH_S)
        gps_locked = False
        if gps_fresh and GPS_strg:
            try:
                gps_locked = int(pynmea2.parse(GPS_strg).gps_qual) >= 1
            except Exception:
                gps_locked = False
        status_flags = 0
        if imu_ok:
            status_flags |= IMU_AVAILABLE
        if mag_ok:
            status_flags |= MAG_AVAILABLE
        if baro_ok:
            status_flags |= BARO_AVAILABLE
        if gps_fresh:
            status_flags |= GPS_AVAILABLE
        if gps_locked:
            status_flags |= GPS_LOCKED
        if camera_ok:
            status_flags |= CAMERA_AVAILABLE
        if camera_recording:
            status_flags |= CAMERA_RECORDING
        # This bit describes the current process's calibration state. It stays
        # set through RECOVERY so RX can correctly label the retained altitude.
        if pad_pressure_hpa is not None and pad_gps_alt_m is not None:
            status_flags |= BARO_GPS_REFERENCED

        # Packet layout (keep identical in telemetry_decode.py and RX_Temenetry.py):
        #   0-9   acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z, temp, mag_x, mag_y, mag_z (raw ints; 0 in RECOVERY)
        #   10-13 baro_temp_c, baro_press_hpa, baro_alt_m, baro_agl_m ("%.2f", empty if unavailable / RECOVERY)
        #   14    phase letter: P (PAD), C (COUNTDOWN), F (FLIGHT), R (RECOVERY)
        #   15    t_clock: seconds relative to T-0, signed, "%.1f" (empty in PAD)
        #   16    status_flags: decimal component availability/state bitmask
        #   17..  raw GGA sentence (contains commas; may be empty)
        message = (",".join(str(v) for v in (acc_x, acc_y, acc_z,
                                             gyro_x, gyro_y, gyro_z, temp,
                                             mag_x, mag_y, mag_z))
                   + "," + ",".join(fmt(v) for v in (baro_t, baro_p, baro_alt, baro_agl))
                   + "," + phase
                   + "," + ("" if t_clock is None else "{:.1f}".format(t_clock))
                   + "," + str(status_flags)
                   + "," + GPS_strg)

        try:
            status = lora.send(message, RX_NODE_ADDRESS)
        except Exception:
            status = False
        if status:
            packets_sent += 1
        else:
            send_failures += 1
        log_packet(message, bool(status))

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
            if baro_p is not None and baro_agl is not None:
                baro_state = "{:.1f}hPa agl={:+.1f}m".format(baro_p, baro_agl)
            elif baro_p is not None:
                baro_state = "{:.1f}hPa agl=n/a".format(baro_p)
            else:
                baro_state = "n/a"
            if phase == "P":
                phase_state = "PAD(waiting for GPS lock)"
            else:
                phase_state = "{} T{:+.1f}".format(PHASE_NAMES[phase], t_clock)
            print("t={:5.0f}s  pkts={:<6d}  rate={:.1f} Hz (target {} Hz)  gps={}  baro={}  i2c_err={}  tx_fail={}  log_err={}  phase={}".format(
                now - start_time, packets_sent, actual_hz, SAMPLE_RATE_HZ,
                gps_state, baro_state, i2c_errors + baro_errors, send_failures,
                telemetry_log_errors, phase_state))
            rate_window_start = now
            rate_window_count = 0

        # Sleep until the next scheduled sample instant. If we have fallen
        # behind (a pass overran), skip ahead so we don't accumulate drift.
        # SAMPLE_PERIOD is 1/7 s until RECOVERY lowers it to 1 s.
        next_sample += SAMPLE_PERIOD
        delay = next_sample - monotonic()
        if delay > 0:
            sleep(delay)
        else:
            next_sample = monotonic()
finally:
    # Stop the camera cleanly so the video file is finalised (no-op if it was
    # already stopped at RECOVERY or never started). No GPIO cleanup call is
    # needed: pyLoraRFM9x uses lgpio and releases it itself.
    stop_camera()
    if telemetry_log_file is not None:
        try:
            telemetry_log_file.flush()
            os.fsync(telemetry_log_file.fileno())
            telemetry_log_file.close()
        except Exception as e:
            telemetry_log_errors += 1
            print("Local telemetry log close failed:", e)
    print("TX done: {} packets sent, {} send failures, {} sensor errors, {} local log errors, phase: {}, video: {}, telemetry log: {}".format(
        packets_sent, send_failures, i2c_errors + baro_errors, telemetry_log_errors,
        PHASE_NAMES[phase], video_path or "none",
        str(TELEMETRY_LOG_PATH) if telemetry_log_rows else "unavailable"))
