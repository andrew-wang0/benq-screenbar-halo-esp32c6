from machine import Pin, SPI
import time

CFG1_REGISTER = 0x00
RC1_REGISTER = 0x01
MASK_REGISTER = 0x03
IRQ1_REGISTER = 0x04
STATUS_REGISTER = 0x05
IO1_REGISTER = 0x06
IO2_REGISTER = 0x07
IO3_REGISTER = 0x08
PKT1_REGISTER = 0x09
PKT2_REGISTER = 0x0a
PKT3_REGISTER = 0x0b
PKT4_REGISTER = 0x0c
RFCH_REGISTER = 0x10
DM1_REGISTER = 0x11
RT1_REGISTER = 0x13
RT2_REGISTER = 0x14
RSV2_REGISTER = 0x0d
RSV3_REGISTER = 0x0f
RSV4_REGISTER = 0x16
RSV5_REGISTER = 0x17
RSV6_REGISTER = 0x18

BANK0_OM_REGISTER = 0x20
BANK0_CFO1_REGISTER = 0x21
BANK0_STA1_REGISTER = 0x26
BANK0_RSSI1_REGISTER = 0x27
BANK0_RSSI2_REGISTER = 0x28
BANK0_RSSI3_REGISTER = 0x29
BANK0_DPL1_REGISTER = 0x2A
BANK0_DPL2_REGISTER = 0x2B
BANK0_RXPW0_REGISTER = 0x2C
BANK0_ENAA_REGISTER = 0x32
BANK0_P2B0_REGISTER = 0x33
BANK0_P3B0_REGISTER = 0x34
BANK0_P4B0_REGISTER = 0x35
BANK0_P5B0_REGISTER = 0x36

BANK1_SET0_REGISTER = 0x20
BANK1_SET1_REGISTER = 0x21
BANK1_SET2_REGISTER = 0x23
BANK1_RSV1_REGISTER = 0x25
BANK1_RSV2_REGISTER = 0x26
BANK1_RSV3_REGISTER = 0x27

BANK2_SET1_REGISTER = 0x28
BANK2_RSV1_REGISTER = 0x2d
BANK2_RSV2_REGISTER = 0x2e
BANK2_SET2_REGISTER = 0x36
BANK2_RSV3_REGISTER = 0x38
BANK2_RSV4_REGISTER = 0x39
BANK2_RSV5_REGISTER = 0x3b
BANK2_RSV6_REGISTER = 0x3c
BANK2_RFTXP_1_REGISTER = 0x34
BANK2_RFTXP_2_REGISTER = 0x35

ACAL_ENABLE = 0x08

CMD_SET_REGISTER_BANK = 0b00100000

CMD_WRITE_REGISTER = 0b01000000
CMD_READ_REGISTER = 0b11000000
CMD_READ_CHIP_VERSION = 0b10011111
CMD_SOFTWARE_RESET = 0b00001000

CMD_WRITE_PTX_ADDRESS = 0b00010000
CMD_READ_PTX_ADDRESS = 0b10010000

CMD_WRITE_TX_FIFO_NO_ACK = 0b00010011
CMD_WRITE_TX_FIFO_WITH_ACK = 0b00010001

CMD_FLUSH_TX_FIFO = 0b00001001
CMD_FLUSH_RX_FIFO = 0b10001001
CMD_READ_RX_FIFO = 0b10111111

CMD_DEEP_SLEEP = 0b00001010
CMD_LIGHT_SLEEP = 0b00001100
CMD_MIDDLE_SLEEP = 0b00001011
CMD_STANDBY = 0b00001101
CMD_TX_MODE = 0b00001110
CMD_RX_MODE = 0b10001110

class bc5602:
    def __init__(self, spi_num=0, cs_pin=Pin(1, Pin.OUT), sck_pin=Pin(2, Pin.OUT), mosi_pin=Pin(3, Pin.OUT), miso_pin=Pin(4, Pin.IN), baudrate=100000):
        self._spi_num = spi_num
        self._cs = cs_pin
        self._sck = sck_pin
        self._mosi = mosi_pin
        self._miso = miso_pin
        self._spi = SPI(spi_num, baudrate=baudrate, polarity=0, phase=0, sck=sck_pin, mosi=mosi_pin, miso=miso_pin)

        # Configure GIO2 as SPI data output: 4-wire mode
        self.set_register(IO1_REGISTER | CMD_WRITE_REGISTER, 0b01001000)

        self.send_command(CMD_LIGHT_SLEEP)
        time.sleep_ms(1)

        # Check if BM5602 is connected
        chip_version = self.read_register(CMD_READ_CHIP_VERSION | CMD_READ_REGISTER, bytes=3).hex().upper()
        if (chip_version == "000000") or (chip_version == "FFFFFFFF"):
            raise RuntimeError("Transceiver BM5602 not found!")
        else:
            print(f"Chip version: {chip_version}")

        self.set_bank(0)

        # Set PRM_RX to 1
        mask_register = self.read_register(MASK_REGISTER | CMD_READ_REGISTER)[0]
        self.set_register(MASK_REGISTER | CMD_WRITE_REGISTER, mask_register | 0x01)

        # Disable retransmissions
        self.set_register(RT1_REGISTER | CMD_WRITE_REGISTER, 0x00)

        # Check if the transceiver has just been powered on
        rc1_register = self.read_register(RC1_REGISTER | CMD_READ_REGISTER)[0]
        if (rc1_register & 0b10000000) > 0:
            # Unstable for some reason
            #self.calibrate()
            self.set_register(RC1_REGISTER | CMD_WRITE_REGISTER, rc1_register & 0b01111111)

    def calibrate(self):
        # start autocalibration
        print("Starting calibration")
        self.set_register(BANK0_OM_REGISTER | CMD_WRITE_REGISTER, ACAL_ENABLE)
        time.sleep_ms(1)
        while self.read_register(BANK0_OM_REGISTER | CMD_READ_REGISTER)[0] == ACAL_ENABLE:
            time.sleep_ms(1)
        print("Complete calibration")

    def shift_left_one_bit(self, data: bytes):
        '''
        Correct data for 1 bit becase of 9 bits Packet Control Fiels
        Required only in no-ack mode
        '''
        result = bytearray(len(data))
        for i in range(len(data) - 1):
            byte = data[i]
            carry = (data[i + 1] & 0x80) >> 7
            result[i] = ((byte << 1) & 0xFF) | carry
        return bytes(result)

    def send_data(self, command_and_data):
        self._cs.low()
        self._spi.write(bytearray(command_and_data))
        self._cs.high()

    def receive_data(self, len, shift_one_bit=False):
        self._cs.low()
        self._spi.write(bytearray([CMD_READ_RX_FIFO]))
        data = self._spi.read(len)
        self._cs.high()
        if shift_one_bit:
            data = self.shift_left_one_bit(data)
        return data

    def send_command(self, command, read_bytes=0):
        self._cs.low()
        try:
            self._spi.write(bytearray([command]))
            if read_bytes > 0:
                data = self._spi.read(read_bytes)
                return data
        finally:
            self._cs.high()

    def set_register(self, register, value):
        self._cs.low()
        self._spi.write(bytearray([register, value]))
        self._cs.high()

    def read_register(self, register, bytes=1):
        self._cs.low()
        self._spi.write(bytearray([register]))
        data = self._spi.read(bytes)
        self._cs.high()
        return data

    def get_bank(self):
        return self.read_register(CFG1_REGISTER | CMD_READ_REGISTER)[0] & 0b00000011

    def set_bank(self, bank):
        cfg1 = self.read_register(CFG1_REGISTER | CMD_READ_REGISTER)[0] & 0b11111100
        self.set_register(CFG1_REGISTER | CMD_WRITE_REGISTER, cfg1 | bank)

    def dump_registers(self):
        current_bank = self.get_bank()

        control_regs = {
            0x00: "CFG1  ",
            0x01: "RC1   ",
            0x03: "MASK  ",
            0x04: "IRQ1  ",
            0x05: "STATUS",
            0x06: "IO1   ",
            0x07: "IO2   ",
            0x08: "IO3   ",
            0x09: "PKT1  ",
            0x0A: "PKT2  ",
            0x0B: "PKT3  ",
            0x0C: "PKT4  ",
            0x0D: "RCV2  ",
            0x0F: "RCV3  ",
            0x10: "RFCH  ",
            0x11: "DM1   ",
            0x13: "RT1   ",
            0x14: "RT2   ",
            0x15: "CE    ",
            0x16: "RSV4  ",
            0x17: "RSV5  ",
            0x18: "RSV6  "
        }

        print("--- CONTROL REGISTERS: Common area ---")
        for reg, name in control_regs.items():
            val = self.read_register(reg | CMD_READ_REGISTER)[0]
            print("{} (0x{:02X}): 0x{:02X} 0b{:08b}".format(name, reg, val, val))
        print("--- END OF CONTROL REGS: Common area ---\n")

        bank0_regs = {
            0x20: "OM    ",
            0x21: "CFO1  ",
            0x26: "STA1  ",
            0x27: "RSSI1 ",
            0x28: "RSSI2 ",
            0x29: "RSSI3 ",
            0x2A: "DPL1  ",
            0x2B: "DPL2  ",
            0x2C: "RXPW0 ",
            0x2D: "RXPW1 ",
            0x2E: "RXPW2 ",
            0x2F: "RXPW3 ",
            0x30: "RXPW4 ",
            0x31: "RXPW5 ",
            0x32: "ENAA  ",
            0x33: "P2B0  ",
            0x34: "P3B0  ",
            0x35: "P4B0  ",
            0x36: "P5B0  ",
            0x37: "PEN   ",
            0x38: "XO1   "
        }

        self.set_bank(0)
        bank = self.get_bank()
        print(f"--- CONTROL REGISTERS: Bank {bank} ---")
        for reg, name in bank0_regs.items():
            val = self.read_register(reg | CMD_READ_REGISTER)[0]
            print("{} (0x{:02X}): 0x{:02X} 0b{:08b}".format(name, reg, val, val))
        print("--- END OF CONTROL REGS: Bank {bank} ---\n")

        bank1_regs = {
            0x20: "SET0    ",
            0x21: "SET1    ",
            0x23: "SET2    ",
            0x25: "RSV1    ",
            0x26: "RSV2    ",
            0x27: "RSV3    "
        }

        self.set_bank(1)
        bank = self.read_register(CFG1_REGISTER | CMD_READ_REGISTER)[0] & 0b00000011
        print(f"--- CONTROL REGISTERS: Bank {bank} ---")
        for reg, name in bank1_regs.items():
            val = self.read_register(reg | CMD_READ_REGISTER)[0]
            print("{} (0x{:02X}): 0x{:02X} 0b{:08b}".format(name, reg, val, val))
        print("--- END OF CONTROL REGS: Bank {bank} ---\n")

        bank2_regs = {
            0x28: "SET1    ",
            0x2D: "RSV1    ",
            0x2E: "RSV2    ",
            0x34: "RFTXP_1 ",
            0x35: "RFTXP_2 ",
            0x36: "SET2    ",
            0x38: "RSV3    ",
            0x39: "RSV4    ",
            0x3B: "RSV5    ",
            0x3C: "RSV6    "
        }

        self.set_bank(2)
        bank = self.read_register(CFG1_REGISTER | CMD_READ_REGISTER)[0] & 0b00000011
        print(f"--- CONTROL REGISTERS: Bank {bank} ---")
        for reg, name in bank2_regs.items():
            val = self.read_register(reg | CMD_READ_REGISTER)[0]
            print("{} (0x{:02X}): 0x{:02X} 0b{:08b}".format(name, reg, val, val))
        print("--- END OF CONTROL REGS: Bank {bank} ---\n")
        
        self.set_bank(current_bank)
