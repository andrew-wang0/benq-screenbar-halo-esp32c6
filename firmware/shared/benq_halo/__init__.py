import time
import halo2_address as _halo2_address
import bc5602

HALO2_ADDRESS = _halo2_address.HALO2_ADDRESS
RF_CHANNEL = getattr(_halo2_address, "RF_CHANNEL", 5)

RF_CHANNEL_1 = 5  # 2405 - 2400 MHz (default)
RF_CHANNEL_2 = 46 # 2446 - 2400 MHz
RF_CHANNEL_3 = 75 # 2475 - 2400 MHz

DATA_RATE_125k = 0b00000010 # 125Kbps

HALO2_PKT_END = [0x01, 0x02]

class benq_halo():
    def __init__(self):
        self._bc5602 = bc5602.bc5602()
        self._bc5602_ack_mode = False
        self._bc5602_tx_mode = False
        self._debug = True

        self.ultrasonic_sensor_status = True
        self.front_lamp_status = True
        self.onoff_status = True
        self.back_lamp_status = False

        self.front_lamp_brightness = 1
        self.back_lamp_brightness = 1
        self.front_lamp_brightness_min = 1
        self.front_lamp_brightness_max = 100
        self.back_lamp_brightness_min = 1
        self.back_lamp_brightness_max = 100
        self.front_lamp_color_temp = 4000
        self.front_lamp_color_temp_min = 2700
        self.front_lamp_color_temp_max = 6500

        self.prepare_to_transfer()
        print("RF address", HALO2_ADDRESS, "channel", 2400 + RF_CHANNEL, "MHz")

    def __str__(self):
        return  f"On/Off status: {self.onoff_status}\n" \
                f"Ultrasonic status: {self.ultrasonic_sensor_status}\n" \
                f"Front lamp status: {self.front_lamp_status}\n" \
                f"Back lamp status: {self.back_lamp_status}\n" \
                f"Front lamp brightness: {self.front_lamp_brightness}\n" \
                f"Back lamp brightness: {self.back_lamp_brightness}\n" \
                f"Front lamp color temp: {self.front_lamp_color_temp}"

    def print_hex(self, data, title, registers=True):
        print(f"{title}: ", end="")
        for byte in data:
            print("{:02X} ".format(byte), end="")
        if registers:
            status = self._bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
            sta1 = self._bc5602.read_register(bc5602.BANK0_STA1_REGISTER | bc5602.CMD_READ_REGISTER)[0]
            omst = ("deep-sleep", "mid-sleep", "light-sleep", "reserved", "TX", "RX", "cal", "undef")[sta1 & 0x07]
            print(f" STATUS: {status:08b} STA1: {sta1:08b} {omst}", end="")
        print()

    def shared_transceiver_config(self):
        self._bc5602.send_command(bc5602.CMD_LIGHT_SLEEP)

        self._bc5602.configure_spi_output()
        self._bc5602.set_bank(0)

        # Set channel 1 = 2405 MHz
        self._bc5602.set_register(bc5602.RFCH_REGISTER | bc5602.CMD_WRITE_REGISTER, RF_CHANNEL)

        # Set datarate 125Kbps + address length 0x04
        self._bc5602.set_register(bc5602.DM1_REGISTER | bc5602.CMD_WRITE_REGISTER, DATA_RATE_125k | 0b10000000)

        # Set address
        self._bc5602.send_data([bc5602.CMD_WRITE_PTX_ADDRESS] + HALO2_ADDRESS)

    def prepare_to_transfer(self):
        """
        Standard mode with hardware support of Auto-ACK, dynamic payload length, CRC, delay control and retransmissions
        """
        self.shared_transceiver_config()

        # Set PRM_RX to 0
        mask_register = self._bc5602.read_register(bc5602.MASK_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        self._bc5602.set_register(bc5602.MASK_REGISTER | bc5602.CMD_WRITE_REGISTER, mask_register & 0b11111110)

        # set dynamic payload len
        self._bc5602.set_register(bc5602.BANK0_DPL1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000001)
        self._bc5602.set_register(bc5602.BANK0_DPL2_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000100)

        # Enable CRC
        self._bc5602.set_register(bc5602.PKT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00100000)

        # Enable Auto-ACK
        self._bc5602.set_register(bc5602.BANK0_ENAA_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00111111)

        # Auto retransmission delay control -> 2ms and and 2 retransmissions
        self._bc5602.set_register(bc5602.RT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x72)
        
        self._bc5602_ack_mode = True

    def send_with_ack(self, packet):
        """
        Send packet in standard mode.

        Light-sleep strobes clear CE. For PTX, CE=1 is what actually starts TX
        once the FIFO is not empty (BC5602 datasheet CE register).
        """
        self._bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
        self._bc5602.send_command(bc5602.CMD_FLUSH_TX_FIFO)
        time.sleep_ms(1)
        self._bc5602.send_data([bc5602.CMD_WRITE_TX_FIFO_WITH_ACK] + packet)
        self._bc5602.set_register(bc5602.CE_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x01)
        self._bc5602.send_command(bc5602.CMD_TX_MODE)
        tx_empty = False
        for _ in range(80):
            status = self._bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
            if status & 0b00010000:
                tx_empty = True
                break
            time.sleep_ms(1)
        if not tx_empty:
            self._bc5602.send_command(bc5602.CMD_TX_MODE)
            time.sleep_ms(10)
            status = self._bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
            tx_empty = bool(status & 0b00010000)
        if not tx_empty:
            self._bc5602.set_register(bc5602.CE_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)
            self._bc5602.send_command(bc5602.CMD_FLUSH_TX_FIFO)
        if self._debug:
            self.print_hex(packet, "Sent")
            if not tx_empty:
                print("TX FIFO did not empty")
        return tx_empty

    def read_ack(self):
        for _ in range(80):
            status = self._bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
            if (status & 0b00000001) == 0:
                return bytearray(self._bc5602.receive_data(10))
            time.sleep_ms(1)
        if self._debug:
            print("No ACK from lamp")
        return bytearray()

    def prepare_to_receive_without_ack(self):
        """
        Sniffer mode without Auto-ACK, which means no dynamic payload and CRC
        """
        self.shared_transceiver_config()

        # Set PRM_RX to 1
        mask_register = self._bc5602.read_register(bc5602.MASK_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        self._bc5602.set_register(bc5602.MASK_REGISTER | bc5602.CMD_WRITE_REGISTER, mask_register | 0x01)

        # Set static payload
        self._bc5602.set_register(bc5602.BANK0_DPL1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)
        self._bc5602.set_register(bc5602.BANK0_DPL2_REGISTER | bc5602.CMD_WRITE_REGISTER, 0b00000000)

        # Payload len (10 bytes payload + 9 bits PCF -> 12 bytes)
        self._bc5602.set_register(bc5602.BANK0_RXPW0_REGISTER | bc5602.CMD_WRITE_REGISTER, 12)

        # Disable CRC
        self._bc5602.set_register(bc5602.PKT1_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)

        # Disable Auto-ACK
        self._bc5602.set_register(bc5602.BANK0_ENAA_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x00)

        # Clear buffer and start RX mode (CE is cleared by light-sleep above)
        self._bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
        self._bc5602.set_register(bc5602.CE_REGISTER | bc5602.CMD_WRITE_REGISTER, 0x01)
        self._bc5602.send_command(bc5602.CMD_RX_MODE)

        self._bc5602_ack_mode = False

    def receive_without_ack(self):
        """
        Monitor packets from remote control
        """
        # Make sure that transceiver is not busy
        if self._bc5602_tx_mode:
            return False
        # Check if transceiver configured in correct mode
        if self._bc5602_ack_mode:
            self.prepare_to_receive_without_ack()
        # Check if RX FIFO has some data
        status = self._bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0] & 0b00000001
        if status == 0x00:
            rxd_len = self._bc5602.read_register(bc5602.PKT4_REGISTER | bc5602.CMD_READ_REGISTER)[0]
            # Check if RX FIFO has data more than 10 bytes (10 bytes payload + 9 bits PCF -> 12 bytes)
            if rxd_len > 10:
                data = self._bc5602.receive_data(rxd_len, shift_one_bit=True)
                pkt_payload = data[1:11]
                pkt_control_field = data[0]
                # Extract packet length, PID and No-ACK from Packet Control Field
                len = (pkt_control_field & 0b11111000) >> 3
                pid = (pkt_control_field & 0b00000110) >> 1
                no_ack = pkt_control_field & 0b00000001
                # Check packet length and validate payload
                if (len == 10) and (self.validate_packet(pkt_payload)):
                    if no_ack:
                        self.parse_lamp_status(pkt_payload)
                    if self._debug:
                        print(f"Remote control packet len: {len} pid:{pid:02b} no_ack:{no_ack:01b} ", end="")
                        self.print_hex(pkt_payload, "Rcvt")
                    # Clear buffers and start RX mode
                    self._bc5602.send_command(bc5602.CMD_FLUSH_TX_FIFO)
                    self._bc5602.send_command(bc5602.CMD_FLUSH_RX_FIFO)
                    self._bc5602.send_command(bc5602.CMD_RX_MODE)
                    return True
        else:
            return False

    def validate_packet(self, ack_data):
        """
        Validate packet length, commands, Brightness and Color Temp
        """
        if len(ack_data) != 10:
            return False
        if ack_data[0] > 5:
            return False
        if (ack_data[2] > self.front_lamp_brightness_max) or (ack_data[5] > self.back_lamp_brightness_max):
            return False
        if (ack_data[8:10] != bytearray(HALO2_PKT_END)):
            return False
        color_temp = ack_data[3] << 8 | ack_data[4]
        if (color_temp < self.front_lamp_color_temp_min) or (color_temp > self.front_lamp_color_temp_max):
            return False
        color_temp = ack_data[6] << 8 | ack_data[7]
        if (color_temp < self.front_lamp_color_temp_min) or (color_temp > self.front_lamp_color_temp_max):
            return False
        return True

    def parse_lamp_status(self, ack_data):
        """
        Parse packet and update internal lamp state
        """
        self.onoff_status = ack_data[1] & 0b00000001
        self.ultrasonic_sensor_status = (ack_data[1] & 0b00100000) >> 5
        #auto_status = (ack_data[1] & 0b00000010) >> 1
        #favorite_status = (ack_data[1] & 0b00000100) >> 2
        lamp_status = (ack_data[1] & 0b00011000) >> 3
        self.front_lamp_status = (lamp_status == 0) or (lamp_status == 2)
        self.back_lamp_status = (lamp_status == 1) or (lamp_status == 2)
        self.front_lamp_brightness = ack_data[2]
        self.back_lamp_brightness = ack_data[5]
        self.front_lamp_color_temp = ack_data[3] * 0x100 + ack_data[4]

    def prepare_payload(self):
        lamp_status = 0
        if self.back_lamp_status:
            lamp_status = self.back_lamp_status + self.front_lamp_status
        control = (self.ultrasonic_sensor_status << 5) | (lamp_status << 3) | (self.onoff_status)
        color_temp_byte1 = self.front_lamp_color_temp >> 8
        color_temp_byte2 = self.front_lamp_color_temp & 0x00FF
        return (control, color_temp_byte1, color_temp_byte2)

    def check_tx_fifo(self):
        status = self._bc5602.read_register(bc5602.STATUS_REGISTER | bc5602.CMD_READ_REGISTER)[0]
        # Check if TX FIFO is not empty or full
        if ((status & 0b00010000) == 0) or ((status & 0b00100000) > 0):
            self._bc5602.send_command(bc5602.CMD_SOFTWARE_RESET)
            time.sleep_ms(20)
            self.prepare_to_transfer()
            if self._debug:
              print("Software reset")

    def request_lamp_status(self, command=0x04, parse_lamp_status=True):
        """
        Request status from the lamp
        """
        self.prepare_to_transfer()
        # Prepare packet payload and send it
        (control, color_temp_byte1, color_temp_byte2) = self.prepare_payload()
        self.send_with_ack([ command, control, self.front_lamp_brightness, color_temp_byte1, color_temp_byte2, 
                             self.back_lamp_brightness, color_temp_byte1, color_temp_byte2 ] + HALO2_PKT_END)
        ack_data = self.read_ack()
        if self.validate_packet(ack_data):
            if self._debug:
                self.print_hex(ack_data, "Rcvt")
            if parse_lamp_status:
                self.parse_lamp_status(ack_data)
            self.check_tx_fifo()
        elif self._debug:
            if ack_data:
                self.print_hex(ack_data, "Bad ACK")
        return self.get_lamp_status()

    def get_lamp_status(self):
        """
        Fill in dictinary with lamp status
        """
        lamp_status = {
            'ultrasonic_sensor_status': self.ultrasonic_sensor_status,
            'onoff_status': self.onoff_status,
            'front_lamp_status': self.front_lamp_status,
            'back_lamp_status': self.back_lamp_status,
            'front_lamp_brightness': self.front_lamp_brightness,
            'back_lamp_brightness': self.back_lamp_brightness,
            'front_lamp_color_temp': self.front_lamp_color_temp
        }
        return lamp_status

    def update_lamp_status(self, command=0x03, auto=False):
        """
        Update lamp with our internal state
        """
        self._bc5602_tx_mode = True
        if not self._bc5602_ack_mode:
            self.prepare_to_transfer()
        # Prepare packet payload and send it
        (control, color_temp_byte1, color_temp_byte2) = self.prepare_payload()
        if auto:
            control = control | 0b00000010
        pkt_payload = [ control, self.front_lamp_brightness, color_temp_byte1, color_temp_byte2, 
                             self.back_lamp_brightness, color_temp_byte1, color_temp_byte2] + HALO2_PKT_END
        self.send_with_ack([command] + pkt_payload)

        # wait for status sync
        for _ in range(10):
            self.send_with_ack([0x04] + pkt_payload)
            ack_data = self.read_ack()
            if self._debug and ack_data:
                self.print_hex(ack_data, "Rcvt")
            if bytearray(pkt_payload) == ack_data[1:]:
                break
            time.sleep_ms(500)

        self.check_tx_fifo()

        self._bc5602_tx_mode = False

class OnOff:
    """
    On/Off switch
    """
    def __init__(self, benq_halo):
        self._benq_halo = benq_halo
        if self._benq_halo._debug:
            print("OnOff init")

    def on(self):
        self._benq_halo.onoff_status = True
        self._benq_halo.update_lamp_status(command=0x02)
        if self._benq_halo._debug:
            print("onoff_status True")

    def off(self):
        self._benq_halo.onoff_status = False
        self._benq_halo.update_lamp_status(command=0x02)
        if self._benq_halo._debug:
            print("onoff_status False")

class UltrasonicSensor:
    """
    Ultrasonic sensor switch
    """
    def __init__(self, benq_halo):
        self._benq_halo = benq_halo
        if self._benq_halo._debug:
            print("UltrasonicSensor init")

    def on(self):
        self._benq_halo.ultrasonic_sensor_status = True
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print("ultrasonic_sensor_status True")

    def off(self):
        self._benq_halo.ultrasonic_sensor_status = False
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print("ultrasonic_sensor_status False")

class BackLamp:
    """
    Back lamp class
    """
    def __init__(self, benq_halo):
        self._benq_halo = benq_halo
        if self._benq_halo._debug:
            print("BackLamp init")

    def on(self):
        self._benq_halo.onoff_status = True
        self._benq_halo.back_lamp_status = True
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print("back_lamp_status True")

    def off(self):
        if self._benq_halo.onoff_status:
            if self._benq_halo.front_lamp_status:
                self._benq_halo.back_lamp_status = False
                self._benq_halo.update_lamp_status()
                if self._benq_halo._debug:
                    print("back_lamp_status False")
            else:
                self._benq_halo.onoff_status = False
                self._benq_halo.update_lamp_status(command=0x02)
                if self._benq_halo._debug:
                    print("onoff_status False")

    def brightness(self, value):
        value_percent = round((value / 256) * 100)
        self._benq_halo.back_lamp_brightness = value_percent
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print(f"back_lamp_brightness {value} -> {value_percent}%")

class FrontLamp:
    """
    Front lamp class
    """
    def __init__(self, benq_halo):
        self._benq_halo = benq_halo
        if self._benq_halo._debug:
            print("FrontLamp init")

    def on(self):
        self._benq_halo.onoff_status = True
        self._benq_halo.front_lamp_status = True
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print("front_lamp_status True")

    def off(self):
        if self._benq_halo.onoff_status:
            if self._benq_halo.back_lamp_status:
                self._benq_halo.front_lamp_status = False
                self._benq_halo.update_lamp_status()
                if self._benq_halo._debug:
                    print("front_lamp_status False")
            else:
                self._benq_halo.onoff_status = False
                self._benq_halo.update_lamp_status(command=0x02)
                if self._benq_halo._debug:
                    print("onoff_status False")

    def brightness(self, value):
        value_percent = round((value / 256) * 100)
        self._benq_halo.front_lamp_brightness = value_percent
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print(f"front_lamp_brightness {value} -> {value_percent}%")

    def color_temp(self, value):
        self._benq_halo.front_lamp_color_temp = value
        self._benq_halo.update_lamp_status()
        if self._benq_halo._debug:
            print(f"front_lamp_color_temp {value}")

class AutoConfig:
    """
    Auto mode button
    """
    def __init__(self, benq_halo):
        self._benq_halo = benq_halo
        if self._benq_halo._debug:
            print("AutoConfig init")

    def start_auto(self):
        self._benq_halo.update_lamp_status(auto=True)
        if self._benq_halo._debug:
            print("start_auto")
