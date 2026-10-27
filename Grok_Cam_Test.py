from datetime import datetime
from pathlib import Path
from time import sleep

from picamera2 import Picamera2

stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
folder = Path("/home/pi/Desktop") / f"photos_{stamp}"
folder.mkdir(parents=True, exist_ok=True)

cam = Picamera2()
cam.configure(cam.create_still_configuration())
cam.start()
sleep(1)

for i in range(1, 6):
    path = folder / f"cam_{i}.jpg"
    cam.capture_file(str(path))
    print("saved", path)
    if i < 5:
        sleep(2)

cam.stop()
print("done", folder)