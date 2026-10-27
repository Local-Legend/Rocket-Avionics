from pyLoraRFM9x import LoRa, ModemConfig

lora = LoRa(
    spi_channel=1,      # CS on CE1, physical pin 26
    interrupt_pin=5,    # G0
    reset_pin=25,       # RST
    spi_port=0,
    my_address=1,
    freq=915,
    tx_power=5,
    modem_config=ModemConfig.Bw125Cr45Sf128,
    acks=False,
)
lora.send("hello", 255)
print("sent")