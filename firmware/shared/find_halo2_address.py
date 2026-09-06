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

def shift_right_one_bit(data: bytes):
    result = bytearray(len(data))
    for i in range(len(data)):
        byte = data[i]
        carry = (data[i - 1] & 0x01) << 7 if i > 0 else 0
        result[i] = (byte >> 1) | carry
    return bytes(result)


def shift_left_one_bit(data: bytes):
    result = bytearray(len(data))
    for i in range(len(data)):
        carry = (data[i + 1] & 0x80) >> 7 if i + 1 < len(data) else 0
        result[i] = ((data[i] << 1) & 0xFF) | carry
    return bytes(result)


def address_from_window(window):
    if len(window) < 5:
        return None
    for cand in (window[:5], shift_left_one_bit(window[:5]), shift_right_one_bit(window[:5])):
        if cand[0] == 0xAA:
            return [cand[4], cand[3], cand[2], cand[1]]
    return None


def extract_address(raw):
    buffers = (raw, shift_right_one_bit(raw), shift_left_one_bit(raw))
    for buf in buffers:
        if len(buf) >= 12:
            found = address_from_window(buf[7:12])
            if found:
                return found
    for buf in buffers:
        for i in range(0, len(buf) - 4):
            found = address_from_window(buf[i:i + 5])
            if found:
                return found
    return None


NEED_MATCHES = 3


def format_address(addr, channel):
    return "HALO2_ADDRESS = [{}]\nRF_CHANNEL = {}\n".format(
        ", ".join("0x{:02x}".format(b) for b in addr), channel)


CHANNELS = (RF_CHANNEL_1, RF_CHANNEL_2, RF_CHANNEL_3)
OMST = ("deep-sleep", "mid-sleep", "light-sleep", "reserved", "TX", "RX", "cal", "undef")


def enter_rx(channel):
    _bc5602.send_command(bc5602.CMD_LIGHT_SLEEP)
    _bc5602.configure_spi_output()
    _bc5602.set_bank(0)
    _bc5602.set_register(bc5602.RFCH_REGISTER | bc5602.CMD_WRITE_REGISTER, channel)
    _bc5602.set_register(bc5602.DM1_REGISTER | bc5602.CMD_WRITE_REGISTER, DATA_RATE_125k | 0b01000000)
    _bc5602.send_data([bc5602.CMD_WRITE_PTX_ADDRESS] + HALO2_ADDRESS)
    mask_register = _bc5602.read_register(bc5602.MASK_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    _bc5602.set_register(bc5602.MASK_REGISTER | bc5602.CMD_WRITE_REGISTER, mask_register | 0x01)
    _bc5602.set_register(bc5602.BANK0_DPL1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)
    _bc5602.set_register(bc5602.BANK0_DPL2_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)
    _bc5602.set_register(bc5602.BANK0_RXPW0_REGISTER | bc5602.CMD_WRITE_REGISTER, 16)
    _bc5602.set_register(bc5602.PKT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)
    _bc5602.set_register(bc5602.BANK0_ENAA_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)
    _bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
    _bc5602.send_command(bc5602.CMD_RX_MODE)


def radio_snapshot():
    status = _bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    sta1 = _bc5602.read_register(bc5602.BANK0_STA1_REGISTER | bc5602.CMD_READ_REGISTER)[0] & 0x07
    rssi = _bc5602.read_register(bc5602.BANK0_RSSI2_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    return status, sta1, rssi


enter_rx(CHANNELS[0])
print("Listening. BM5602 antenna (not the XIAO) against the remote; BACK 10% and 3925K.")

counts = {}
channel_index = 0
locked_channel = None
last_wait = time.ticks_ms()
while True:
    status = _bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0] & 0b00000001
    if status == 0x00:
        rxd_len = _bc5602.read_register(bc5602.PKT4_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        n = rxd_len if rxd_len >= 12 else 16
        raw = _bc5602.receive_data(n, shift_one_bit=False)
        window = shift_right_one_bit(raw)[7:12]
        addr = address_from_window(window)
        if addr is None:
            addr = extract_address(raw)
        _bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
        _bc5602.send_command(bc5602.CMD_RX_MODE)
        if addr:
            locked_channel = CHANNELS[channel_index]
            found = format_address(addr, locked_channel)
            counts[found] = counts.get(found, 0) + 1
            print("Candidate {} ({}/{})".format(found.strip().replace("\n", " "), counts[found], NEED_MATCHES))
            if counts[found] >= NEED_MATCHES:
                break
    else:
        if locked_channel is None and time.ticks_diff(time.ticks_ms(), last_wait) >= 3000:
            fifo, sta1, rssi = radio_snapshot()
            ch = CHANNELS[channel_index]
            print("Waiting ch={}MHz mode={} STATUS=0x{:02X} RSSI=-{}dB".format(
                2400 + ch, OMST[sta1], fifo, rssi))
            channel_index = (channel_index + 1) % len(CHANNELS)
            enter_rx(CHANNELS[channel_index])
            last_wait = time.ticks_ms()
        time.sleep_ms(1)

most_frequent = max(counts, key=counts.get)

with open("/halo2_address.py", "w") as f:
    f.write(most_frequent)
