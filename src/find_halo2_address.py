import time
import bc5602

'''
The script will attempt to find HALO2_ADDRESS (the address or sync word which used to communicate with the Halo 2 lamp)
You need to start this script on the MCU and set the following parameters on the Halo 2 remote control:

    Set the brightness of back lamp to 10%
    Set the color temperature to 3925K

The HALO2_ADDRESS will be printed on the screen several times while the remote control is communication with the lamp.

0x55, 0x0f -> Color temperature 3925K (reverse byte order; used as preambule and part of the sync word)
0x0a -> 10% brightness of the back lamp (used as part of the sync word)
'''

HALO2_ADDRESS = [0x55, 0x0f, 0x0a]

RF_CHANNEL_1 = 5  # 2405 - 2400 MHz (default)
RF_CHANNEL_2 = 46 # 2446 - 2400 MHz
RF_CHANNEL_3 = 75 # 2475 - 2400 MHz

DATA_RATE_125k = 0b00000010 # 125Kbps

_bc5602 = bc5602.bc5602()

def print_hex(data, title):
    print(f"{title}: ", end="")
    for byte in data:
        print("{:02X} ".format(byte), end="")
    print()

def shift_right_one_bit(data: bytes):
    result = bytearray(len(data))
    for i in range(len(data)):
        byte = data[i]
        carry = (data[i - 1] & 0x01) << 7 if i > 0 else 0
        result[i] = (byte >> 1) | carry
    return bytes(result)


_bc5602.send_command(bc5602.CMD_LIGHT_SLEEP)

# Configure GIO2 as SPI data output: 4-wire mode
_bc5602.set_register(bc5602.IO1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b01001000)

# Set channel 1 = 2405 MHz
_bc5602.set_register(bc5602.RFCH_REGISTER | bc5602.CMD_WRITE_REGISTER, RF_CHANNEL_1)

# Set datarate 125Kbps + address length 0x03
_bc5602.set_register(bc5602.DM1_REGISTER | bc5602.CMD_WRITE_REGISTER, DATA_RATE_125k | 0b01000000)

# Set address
_bc5602.send_data([bc5602.CMD_WRITE_PTX_ADDRESS] + HALO2_ADDRESS)

# Set PRM_RX to 1
mask_register = _bc5602.read_register(bc5602.MASK_REGISTER | bc5602.CMD_READ_REGISTER)[0]
_bc5602.set_register(bc5602.MASK_REGISTER | bc5602.CMD_WRITE_REGISTER, mask_register | 0x01)

# Set static payload
_bc5602.set_register(bc5602.BANK0_DPL1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)
_bc5602.set_register(bc5602.BANK0_DPL2_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)

# Payload len (10 bytes payload + 9 bits PCF -> 12 bytes)
_bc5602.set_register(bc5602.BANK0_RXPW0_REGISTER | bc5602.CMD_WRITE_REGISTER, 16)

# Disable CRC
_bc5602.set_register(bc5602.PKT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)

# Disable Auto-ACK
_bc5602.set_register(bc5602.BANK0_ENAA_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)

# Clear buffer and start RX mode
_bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
_bc5602.send_command(bc5602.CMD_RX_MODE)

addresses = []
addresses_count = 0
while True:
    status = _bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0] & 0b00000001
    if status == 0x00:
        rxd_len = _bc5602.read_register(bc5602.PKT4_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        data = shift_right_one_bit(_bc5602.receive_data(rxd_len, shift_one_bit=False))[7:12]
        if data[0] == 0xAA:
            addresses.append("HALO2_ADDRESS = [0x{:02x}, 0x{:02x}, 0x{:02x}, 0x{:02x}]".format(data[4], data[3], data[2], data[1]))
            addresses_count += 1
            if addresses_count == 5:
                break
    else:
         time.sleep_ms(1)

counts = {}

for item in addresses:
    if item in counts:
        counts[item] += 1
    else:
        counts[item] = 1

most_frequent = max(counts, key=counts.get)

with open("/halo2_address.py", "w") as f:
    f.write(most_frequent)
