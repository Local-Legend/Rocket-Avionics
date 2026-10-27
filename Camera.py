#!/usr/bin/env python3
"""
Camera.py - Standalone test / reference for the ArduCam OV5647 on the Pi Zero.

This records video LOCALLY to the Pi (it is never transmitted over the LoRa
radio). It uses picamera2 (the libcamera stack), which is the supported way to
drive the OV5647 on Raspberry Pi OS Bullseye and Bookworm.

The same recording approach is embedded into TX_Telemetry.py so the camera runs
in the background while telemetry is read and transmitted.

Run directly to do a quick 10 second local recording test:
    python3 Camera.py
"""

import os
from datetime import datetime
from time import sleep

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

# Where recordings are stored on the Pi. Created if it does not exist.
RECORD_DIR = "/home/pi/Desktop/Camera"

# OV5647 does solid 1080p30 H.264. Drop to 1280x720 if the Zero struggles.
RESOLUTION = (1920, 1080)
FRAMERATE = 30
ROTATION = 180          # matches the old picamera rotation=180 orientation
BITRATE = 8_000_000     # ~8 Mbps, good quality for 1080p


def _timestamped_path(ext):
    """Build a unique, timestamped output path so runs never overwrite."""
    os.makedirs(RECORD_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(RECORD_DIR, "flight_{}.{}".format(stamp, ext))


def create_camera():
    """Configure a Picamera2 instance for local H.264 video recording."""
    picam2 = Picamera2()

    # transform handles the 180 degree flip that rotation=180 used to do.
    try:
        from libcamera import Transform
        transform = Transform(hflip=1, vflip=1) if ROTATION == 180 else Transform()
    except Exception:
        transform = None

    config_kwargs = {"main": {"size": RESOLUTION}}
    if transform is not None:
        config_kwargs["transform"] = transform

    video_config = picam2.create_video_configuration(**config_kwargs)
    video_config["controls"]["FrameRate"] = FRAMERATE
    picam2.configure(video_config)
    return picam2


def start_recording(picam2):
    """Begin a background recording. Returns the output file path.

    This call is non-blocking: the camera records to disk on its own thread
    while the caller continues doing other work (reading sensors, TX, etc.).
    """
    output_path = _timestamped_path("h264")
    encoder = H264Encoder(bitrate=BITRATE)
    picam2.start_recording(encoder, FileOutput(output_path))
    return output_path


def stop_recording(picam2):
    """Stop recording and release the camera."""
    try:
        picam2.stop_recording()
    finally:
        picam2.close()


if __name__ == "__main__":
    cam = create_camera()
    path = start_recording(cam)
    print("Recording locally to:", path)
    try:
        sleep(10)
    finally:
        stop_recording(cam)
        print("Done. Saved:", path)
