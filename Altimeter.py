from smbus2 import SMBus
from bmp280 import BMP280
from time import sleep

chip = BMP280(i2c_dev=SMBus(1), i2c_addr=0x76)
sleep(1)
for _ in range(5):
    print(chip.get_temperature(), chip.get_pressure())
    sleep(1)
print(chip.get_altitude())