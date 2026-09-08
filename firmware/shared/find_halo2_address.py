import time
import bc5602
import board_config as board

'''
Find HALO2_ADDRESS from the remote. Set back lamp 10% and color temperature 3925K.
0x55, 0x0f -> 3925K (reverse byte order; preamble / part of the sync word)
0x0a -> 10% back brightness (part of the sync word)
'''

HALO2_ADDRESS = [0x55, 0x0f, 0x0a]
RF_CHANNEL_1 = 5
RF_CHANNEL_2 = 46
RF_CHANNEL_3 = 75
DATA_RATE_125k = 0b00000010
NEED_MATCHES = 3
DWELL_MS = 30000
CHANNELS = (RF_CHANNEL_1, RF_CHANNEL_2, RF_CHANNEL_3)
OMST = ("deep-sleep", "mid-sleep", "light-sleep", "reserved", "TX", "RX", "cal", "undef")

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


def address_from_offset(buf):
    if len(buf) < 13:
        return None
    window = buf[7:12]
    # Decode only after upstream's right shift. Either preamble is legal;
    # require it to agree with the first address bit and a 10-byte PCF length.
    expected_preamble = 0xAA if window[1] & 0x80 else 0x55
    if window[0] == expected_preamble and (buf[12] >> 2) == 10:
        return [window[4], window[3], window[2], window[1]]
    return None


def format_address(addr, channel):
    return "HALO2_ADDRESS = [{}]\nRF_CHANNEL = {}\n".format(
        ", ".join("0x{:02x}".format(b) for b in addr), channel)


def print_hex(data, title):
    print(f"{title}: ", end="")
    for byte in data:
        print("{:02X} ".format(byte), end="")
    print()


def snapshot(tag):
    status = _bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    sta1 = _bc5602.read_register(bc5602.BANK0_STA1_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    rssi = _bc5602.read_register(bc5602.BANK0_RSSI2_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    mask = _bc5602.read_register(bc5602.MASK_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    ce = _bc5602.read_register(bc5602.CE_REGISTER | bc5602.CMD_READ_REGISTER)[0]
    bank = _bc5602.get_bank()
    print("{} bank={} mode={} STATUS=0x{:02X} STA1=0x{:02X} MASK=0x{:02X} CE=0x{:02X} RSSI=-{}dB".format(
        tag, bank, OMST[sta1 & 0x07], status, sta1, mask, ce, rssi))


def enter_rx(channel):
    # Light-sleep once so VCO cal is legal, then a single RX strobe.
    # Do not re-strobe every few ms: the synth never locks if you do.
    _bc5602.send_command(bc5602.CMD_LIGHT_SLEEP)
    time.sleep_ms(2)
    _bc5602.configure_spi_output()
    _bc5602.set_bank(0)
    _bc5602.set_register(bc5602.MASK_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x01)
    _bc5602.set_register(bc5602.RFCH_REGISTER | bc5602.CMD_WRITE_REGISTER, channel)
    if getattr(board, "CALIBRATE_ON_CHANNEL_CHANGE", True):
        _bc5602.calibrate()
    _bc5602.set_register(bc5602.DM1_REGISTER | bc5602.CMD_WRITE_REGISTER, DATA_RATE_125k | 0b01000000)
    _bc5602.send_data([bc5602.CMD_WRITE_PTX_ADDRESS] + HALO2_ADDRESS)
    _bc5602.set_register(bc5602.BANK0_DPL1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0)
    _bc5602.set_register(bc5602.BANK0_DPL2_REGISTER | bc5602.CMD_WRITE_REGISTER, 0)
    _bc5602.set_register(bc5602.BANK0_RXPW0_REGISTER | bc5602.CMD_WRITE_REGISTER, 16)
    _bc5602.set_register(bc5602.PKT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0)
    _bc5602.set_register(bc5602.BANK0_ENAA_REGISTER | bc5602.CMD_WRITE_REGISTER, 0)
    _bc5602.set_register(bc5602.BANK0_PEN_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x01)
    _bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
    _bc5602.set_register(bc5602.CE_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x01)
    _bc5602.send_command(bc5602.CMD_RX_MODE)
    time.sleep_ms(20)
    snapshot("RX 20ms ch={}MHz".format(2400 + channel))
    time.sleep_ms(180)
    snapshot("RX 200ms ch={}MHz".format(2400 + channel))


print("Finder 5")
enter_rx(CHANNELS[0])
print("Listening. BM5602 antenna against the remote; BACK 10% and 3925K.")

counts = {}
channel_index = 0
last_wait = time.ticks_ms()
while True:
    status = _bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0] & 0b00000001
    if status == 0x00:
        rxd_len = _bc5602.read_register(bc5602.PKT4_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        if not 1 <= rxd_len <= 32:
            _bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
            continue
        raw = _bc5602.receive_data(rxd_len, shift_one_bit=False)
        addr = address_from_offset(shift_right_one_bit(raw))
        print_hex(raw[:12] if len(raw) >= 12 else raw, "Heard")
        _bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
        _bc5602.set_register(bc5602.CE_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x01)
        _bc5602.send_command(bc5602.CMD_RX_MODE)
        if addr:
            found = format_address(addr, CHANNELS[channel_index])
            counts[found] = counts.get(found, 0) + 1
            print("Candidate {} ({}/{})".format(found.strip().replace("\n", " "), counts[found], NEED_MATCHES))
            if counts[found] >= NEED_MATCHES:
                break
    else:
        now = time.ticks_ms()
        if time.ticks_diff(now, last_wait) >= DWELL_MS:
            snapshot("Waiting ch={}MHz".format(2400 + CHANNELS[channel_index]))
            channel_index = (channel_index + 1) % len(CHANNELS)
            enter_rx(CHANNELS[channel_index])
            last_wait = now
        time.sleep_ms(1)

with open("/halo2_address.py", "w") as f:
    f.write(max(counts, key=counts.get))
