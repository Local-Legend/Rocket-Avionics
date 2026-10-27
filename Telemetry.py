import RPi.GPIO as GPIO
import serial
import pynmea2
import smbus
from time import sleep
import math
from pyLoraRFM9x import LoRa, ModemConfig

serialPort = serial.Serial("/dev/ttyS0", 9600, timeout=0.5)
bus = smbus.SMBus(1)
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

lora = LoRa(1, 5, 10, reset_pin = 25, modem_config=ModemConfig.Bw125Cr45Sf128, tx_power=5, acks=True)

#MPU write to sample rate register
bus.write_byte_data(MPU_Address, SMPLRT_DIV, 7)
sleep(1)
#MPU Write to power management register
bus.write_byte_data(MPU_Address, PWR_MGMT_1, 1)
sleep(1)
#MPU Write to Configuration register
bus.write_byte_data(MPU_Address, CONFIG, 0)
sleep(1)
#MPU Write to Gyro configuration register
bus.write_byte_data(MPU_Address, GYRO_CONFIG, 24)
sleep(1)
#MPU Write to interrupt enable register
bus.write_byte_data(MPU_Address, INT_ENABLE, 1)
sleep(1)
#MAG write to Configuration Register A
bus.write_byte_data(MAG_Address, Register_A, 0x70)
sleep(1)
#MAG Write to Configuration Register B for gain
bus.write_byte_data(MAG_Address, Register_B, 0xa0)
sleep(1)
#MAG Write to mode Register for selecting mode
bus.write_byte_data(MAG_Address, Register_mode, 0)
sleep(1)


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

x=0
while x < 3:
    
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

    #Read Accelerometer raw value
    mag_x = read_MAG_data(X_axis_H)
    mag_z = read_MAG_data(Z_axis_H)
    mag_y = read_MAG_data(Y_axis_H)

    GPS_strg = serialPort.readline()
    GPS_strg = GPS_strg.decode('utf-8')

    message = (str(acc_x) + ',' + str(acc_y) + ',' + str(acc_z) + ',' + str(gyro_x) + ',' + str(gyro_y) + ',' + str(gyro_z) + ',' + str(temp) + ',' + str(mag_x) + ',' + str(mag_y) + ',' + str(mag_z) + ',' + GPS_strg)

    status = lora.send(message, 2)
    print(message)
#    print(str(acc_x) + ',' + str(acc_y) + ',' + str(acc_z) + ',' + str(gyro_x) + ',' + str(gyro_y) + ',' + str(gyro_z) + ',' + str(temp) + ',' + str(mag_x) + ',' + str(mag_y) + ',' + str(mag_z) + ',' + GPS_strg)
    sleep(1)
    x=x+1
GPIO.cleanup()