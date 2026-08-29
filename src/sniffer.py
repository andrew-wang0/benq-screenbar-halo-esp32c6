import time

while True:
    try:
        from halo2_address import HALO2_ADDRESS
        if (isinstance(HALO2_ADDRESS, list)) and (len(HALO2_ADDRESS) == 4) and all(isinstance(x, int) for x in HALO2_ADDRESS):
             break
    except ImportError:
        print("No HALO2_ADDRESS: set brightness 10% and color temperature to 3925K on remote control.")
        exec(open("/find_halo2_address.py").read())

import bc5602

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


_bc5602.send_command(bc5602.CMD_LIGHT_SLEEP)

# Configure GIO2 as SPI data output: 4-wire mode
_bc5602.set_register(bc5602.IO1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b01001000)

# Set channel 1 = 2405 MHz
_bc5602.set_register(bc5602.RFCH_REGISTER | bc5602.CMD_WRITE_REGISTER, RF_CHANNEL_1)

# Set datarate 125Kbps + address length 0x04
_bc5602.set_register(bc5602.DM1_REGISTER | bc5602.CMD_WRITE_REGISTER, DATA_RATE_125k | 0b10000000)

# Set address
_bc5602.send_data([bc5602.CMD_WRITE_PTX_ADDRESS] + HALO2_ADDRESS)

# Set PRM_RX to 1
mask_register = _bc5602.read_register(bc5602.MASK_REGISTER | bc5602.CMD_READ_REGISTER)[0]
_bc5602.set_register(bc5602.MASK_REGISTER | bc5602.CMD_WRITE_REGISTER, mask_register | 0x01)

# Set static payload
_bc5602.set_register(bc5602.BANK0_DPL1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)
_bc5602.set_register(bc5602.BANK0_DPL2_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)

# Payload len (10 bytes payload + 9 bits PCF -> 12 bytes)
_bc5602.set_register(bc5602.BANK0_RXPW0_REGISTER | bc5602.CMD_WRITE_REGISTER, 12)

# Disable CRC
_bc5602.set_register(bc5602.PKT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)

# Disable Auto-ACK
_bc5602.set_register(bc5602.BANK0_ENAA_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)

# Clear buffer and start RX mode
_bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
_bc5602.send_command(bc5602.CMD_RX_MODE)

while True:
    status = _bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0] & 0b00000001
    if status == 0x00:
        rxd_len = _bc5602.read_register(bc5602.PKT4_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        # Check if RX FIFO has data more than 10 bytes (10 bytes payload + 9 bits PCF -> 12 bytes)
        if rxd_len > 10:
            data = _bc5602.receive_data(rxd_len, shift_one_bit=True)
            pkt_payload = data[1:11]
            pkt_control_field = data[0]
            # Extract packet length, PID and No-ACK from Packet Control Field
            len = (pkt_control_field & 0b11111000) >> 3
            pid = (pkt_control_field & 0b00000110) >> 1
            no_ack = pkt_control_field & 0b00000001
            # Check packet length and validate payload
            if (len == 10):
                    print(f"len: {len} pid:{pid:02b} no_ack:{no_ack:01b} ", end="")
                    print_hex(pkt_payload, "Data")
    else:
         time.sleep_ms(1)
