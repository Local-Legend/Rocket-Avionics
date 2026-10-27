from pyLoraRFM9x import LoRa, ModemConfig

# This is our callback function that runs when a message is received
def on_recv(payload):
    print("From:", payload.header_from)
    print("Received:", str(payload.message))
    print("RSSI: {}; SNR: {}".format(payload.rssi, payload.snr))

# Use chip select 1. GPIO pin 5 will be used for interrupts and set reset pin to 25
# The address of this device will be set to 2
lora = LoRa(1, 5, 10, reset_pin = 25, modem_config=ModemConfig.Bw125Cr45Sf128, tx_power=5, acks=True)
lora.on_recv = on_recv #When message is recieved do this

# Send a message to a recipient device with address 10
# Retry sending the message twice if we don't get an  acknowledgment from the recipient
message = "Hello there!"
# status = lora.send_to_wait(message, 2, retries=2)
# if status is True:
#     print("Message sent!")
# else:
#     print("No acknowledgment from recipient")
lora.set_mode_rx()