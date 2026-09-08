"""Host-side board and protocol regression tests; no physical radio required."""
import importlib
import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "firmware/shared"
sys.path.insert(0, str(SHARED))


class Pin:
    OUT, IN = 1, 0
    instances = []

    def __init__(self, number, mode, value=None):
        self.number, self.mode, self.level = number, mode, value
        self.instances.append(self)

    def value(self, level=None):
        if level is not None:
            self.level = level
        return self.level


class SPI:
    def __init__(self, number=1, **kwargs):
        self.number, self.options = number, kwargs
        self.writes = []
        self.responses = []
        self.irq = 0x0E

    def write(self, data):
        self.writes.append(bytes(data))
        if bytes(data) == b'\x08':
            self.irq = 0x0E
        elif len(data) == 2 and data[0] == 0x44:
            self.irq &= ~(data[1] & 0x70)
        elif bytes(data) == b'\x0e':
            self.irq |= 0x20

    def read(self, count):
        if self.responses:
            return self.responses.pop(0)
        if self.writes[-1] == b'\xc4':
            return bytes([self.irq])
        return bytes([0x10] * count)

    def write_readinto(self, wbuf, rbuf):
        self.write(wbuf)
        payload = self.read(max(len(rbuf) - 1, 0))
        rbuf[0] = 0
        rbuf[1:1 + len(payload)] = payload


def load_board(name):
    spec = importlib.util.spec_from_file_location("board_config", ROOT / "boards" / name / "board_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, {
            "machine": types.SimpleNamespace(Pin=Pin, SPI=SPI, SoftSPI=SPI),
            "halo2_address": types.SimpleNamespace(HALO2_ADDRESS=[0x12, 0x34, 0x56, 0x78]),
        })
        self.modules.start()
        self.sleep = patch("time.sleep_ms", lambda _: None, create=True)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.modules.stop()
        for name in ("board_config", "hardware", "bc5602", "benq_halo", "address"):
            sys.modules.pop(name, None)

    def board(self, name):
        for mod in ("hardware", "bc5602", "benq_halo"):
            sys.modules.pop(mod, None)
        config = load_board(name)
        sys.modules['board_config'] = config
        with patch.object(sys, "platform", config.PLATFORM):
            driver = importlib.import_module("bc5602")
        return driver

    def test_board_wiring_and_antenna(self):
        for name, expected in (("pico_w", (0, 1, 2, 3, 4)),
                               ("xiao_esp32c6", (1, 18, 20, 19, 17)),
                               ("esp32_wroom", (1, 5, 18, 23, 19))):
            with self.subTest(board=name):
                Pin.instances = []
                radio = self.board(name).bc5602()
                self.assertEqual((radio._spi_num, radio._cs.number, radio._sck.number,
                                  radio._mosi.number, radio._miso.number), expected)
                self.assertEqual(radio._cs.value(), 1)
                expected_baud = 500_000 if name == 'xiao_esp32c6' else 4_000_000
                self.assertEqual(radio._spi.options['baudrate'], expected_baud)
                # Route SDO before attempting any register reads.
                route = [b'\x46\x40', b'\x47\x10'] if name == 'xiao_esp32c6' else [b'\x46\x48', b'\x47\x00']
                prefix = [b'\x08'] if name == 'xiao_esp32c6' else []
                self.assertEqual(radio._spi.writes[:len(prefix) + 2], prefix + route)
                self.assertIn(b'\x9f', radio._spi.writes)
                self.assertNotIn(b'\xdf', radio._spi.writes)
                if name == 'xiao_esp32c6':
                    self.assertTrue(sys.modules['board_config'].USE_SOFT_SPI)
                hardware = sys.modules['hardware']
                hardware.wifi_status(True)
                self.assertEqual(hardware._led.level, 0 if name == 'xiao_esp32c6' else 1)
                hardware.wifi_status(False)
                self.assertEqual(hardware._led.level, 1 if name == 'xiao_esp32c6' else 0)
                if name == 'xiao_esp32c6':
                    self.assertEqual((hardware._antenna_enable.number, hardware._antenna_enable.level), (3, 0))
                    self.assertEqual((hardware._antenna_select.number, hardware._antenna_select.level), (14, 0))

    def test_wrong_platform_rejected(self):
        sys.modules['board_config'] = load_board('pico_w')
        with patch.object(sys, 'platform', 'esp32'), self.assertRaises(RuntimeError):
            importlib.import_module('hardware')

    def test_tx_retry_limit_stops_single_transaction_and_clears_failure(self):
        self.board('xiao_esp32c6')
        lamp = importlib.import_module('benq_halo').benq_halo()
        lamp._debug = False
        spi = lamp._bc5602._spi
        spi.writes.clear()

        def read(count):
            if spi.writes[-1] == b'\xc4':
                return b'\x10' if b'\x0e' in spi.writes else b'\x0e'
            return b'\x11'  # Initially flushed FIFOs.

        spi.read = read
        self.assertFalse(lamp.send_with_ack([0] * 10))
        writes = spi.writes
        self.assertEqual(writes.count(b'\x0e'), 1)
        self.assertNotIn(b'\x55\x01', writes)  # No continuous TX.
        self.assertLess(writes.index(b'\x44\x70'), writes.index(b'\x11' + bytes(10)))
        self.assertIn(b'\x44\x30', writes)  # Failure IRQ flags cleared.
        self.assertEqual(writes[-1], b'\x09')  # Flush after stopping the radio.

    def test_tx_does_not_append_to_fifo_that_failed_to_flush(self):
        self.board('xiao_esp32c6')
        lamp = importlib.import_module('benq_halo').benq_halo()
        lamp._debug = False
        spi = lamp._bc5602._spi
        spi.writes.clear()
        spi.read = lambda count: b'\x01'
        self.assertFalse(lamp.send_with_ack([0] * 10))
        self.assertFalse(any(p[0] == 0x11 for p in spi.writes))
        self.assertNotIn(b'\x0e', spi.writes)
        self.assertEqual(spi.writes.count(b'\x08'), 1)  # Bounded recovery.

    def test_pico_and_xiao_produce_identical_radio_traffic(self):
        traces = []
        for board in ('pico_w', 'xiao_esp32c6'):
            self.board(board)
            halo = importlib.import_module('benq_halo')
            lamp = halo.benq_halo()
            # Startup differs by board; compare application traffic after init.
            lamp._bc5602._spi.writes.clear()
            # Fixed valid ACK: power, both lamps, sensor; 50/10%, 4000 K.
            ack = bytes([4, 49, 50, 15, 160, 10, 15, 160, 1, 2])
            original_read = lamp._bc5602._spi.read
            lamp._bc5602._spi.read = lambda n: ack if n == 10 else original_read(n)
            lamp.request_lamp_status(command=0)
            self.assertEqual(lamp.get_lamp_status(), {
                'ultrasonic_sensor_status': 1, 'onoff_status': 1,
                'front_lamp_status': True, 'back_lamp_status': True,
                'front_lamp_brightness': 50, 'back_lamp_brightness': 10,
                'front_lamp_color_temp': 4000,
            })
            halo.OnOff(lamp).off()
            halo.OnOff(lamp).on()
            halo.FrontLamp(lamp).off()
            halo.FrontLamp(lamp).on()
            halo.FrontLamp(lamp).brightness(128)
            halo.FrontLamp(lamp).color_temp(6500)
            halo.BackLamp(lamp).off()
            halo.BackLamp(lamp).on()
            halo.BackLamp(lamp).brightness(64)
            halo.UltrasonicSensor(lamp).off()
            halo.UltrasonicSensor(lamp).on()
            halo.AutoConfig(lamp).start_auto()
            lamp.prepare_to_receive_without_ack()
            writes = lamp._bc5602._spi.writes
            expected_io1 = 0x40 if board == 'xiao_esp32c6' else 0x48
            expected_io2 = 0x10 if board == 'xiao_esp32c6' else 0x00
            # All mode transitions must retain this board's output route.
            for packet in writes:
                if packet[0] == 0x46:
                    self.assertEqual(packet, bytes((0x46, expected_io1)))
                elif packet[0] == 0x47:
                    self.assertEqual(packet, bytes((0x47, expected_io2)))
            # Only SDO routing differs; lamp commands must remain identical.
            traces.append([p for p in writes if p[0] not in (0x46, 0x47)])
        self.assertEqual(traces[0], traces[1])
        self.assertTrue(any(p[0] == 0x11 and p[1] == 2 for p in traces[0]))
        self.assertTrue(any(p[0] == 0x11 and p[1] == 3 and p[2] & 2 for p in traces[0]))

    def test_sniffed_packet_decoding(self):
        for board in ('pico_w', 'xiao_esp32c6'):
            driver = self.board(board)
            halo = importlib.import_module('benq_halo')
            lamp = halo.benq_halo()
            lamp.prepare_to_receive_without_ack()
            payload = bytes([3, 49, 75, 15, 160, 25, 15, 160, 1, 2])
            decoded = bytes([81]) + payload # length 10, no_ack=1
            wire = (int.from_bytes(decoded, 'big') << 7).to_bytes(12, 'big')
            lamp._bc5602._spi.responses = [b'\x00', b'\x0c', wire]
            self.assertTrue(lamp.receive_without_ack())
            self.assertEqual(lamp.front_lamp_brightness, 75)
            self.assertEqual(lamp.back_lamp_brightness, 25)
            self.assertFalse(lamp.validate_packet(payload[:-1]))

    def test_discovery_alignment_from_captured_remote_frame(self):
        tree = ast.parse((SHARED / 'find_halo2_address.py').read_text())
        functions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name in ('shift_right_one_bit', 'address_from_offset')], type_ignores=[])
        scope = {}
        exec(compile(functions, 'finder-functions', 'exec'), scope)
        raw = bytes.fromhex('00021b6844a7f0aa4bc6736251050964')
        corrected = scope['shift_right_one_bit'](raw)
        self.assertEqual(scope['address_from_offset'](corrected), [0xb1, 0x39, 0xe3, 0x25])
        self.assertIsNone(scope['address_from_offset'](raw))
        self.assertIsNone(scope['address_from_offset'](corrected[:12]))

    def test_captured_crc_valid_packet_with_per_lamp_trailer(self):
        self.board('xiao_esp32c6')
        sys.modules['halo2_address'].HALO2_PKT_END = [0, 2]
        halo = importlib.import_module('benq_halo')
        lamp = halo.benq_halo()
        raw = bytes.fromhex('290204b207aa8507aa80017ee6c461fc')
        bits = ''.join(format(v, '08b') for v in raw)
        crc = 0xffff
        for bit in ''.join(format(v, '08b') for v in bytes.fromhex('25e339b1')) + bits[:89]:
            top = (crc >> 15) ^ int(bit)
            crc = (crc << 1) & 0xffff
            if top:
                crc ^= 0x1021
        self.assertEqual(crc, int(bits[89:105], 2))
        payload = lamp._bc5602.shift_left_one_bit(raw)[1:11]
        self.assertEqual(payload, bytes.fromhex('0409640f550a0f550002'))
        self.assertTrue(lamp.validate_packet(payload))
        lamp.parse_lamp_status(payload)
        self.assertEqual(lamp.front_lamp_brightness, 100)
        self.assertEqual(lamp.back_lamp_brightness, 10)
        self.assertEqual(lamp.front_lamp_color_temp, 3925)
        self.assertEqual(halo.HALO2_PKT_END, [0, 2])
        # Periodic sync requests carry cached settings, even with a valid CRC.
        # Repeated queries must not overwrite a more recent HA setting.
        lamp.prepare_to_receive_without_ack()
        lamp.front_lamp_brightness = 1
        for _ in range(3):
            lamp._bc5602._spi.responses = [b'\x00', b'\x0c', raw[:12]]
            self.assertFalse(lamp.receive_without_ack())
            self.assertEqual(lamp.front_lamp_brightness, 1)
        # Explicit remote actions still update state with NO_ACK=0.
        action = bytearray(payload)
        action[0] = 3
        decoded = bytes([80]) + action
        wire = (int.from_bytes(decoded, 'big') << 7).to_bytes(12, 'big')
        lamp._bc5602._spi.responses = [b'\x00', b'\x0c', wire]
        self.assertTrue(lamp.receive_without_ack())
        self.assertEqual(lamp.front_lamp_brightness, 100)

    def test_home_assistant_discovery_and_commands_on_both_boards(self):
        import asyncio
        import json
        from unittest.mock import Mock

        class Client:
            def __init__(self, config):
                self.config = config
                self.publications = []
                self.subscriptions = []

            async def publish(self, *args, **kwargs):
                self.publications.append(args)

            async def subscribe(self, topic):
                self.subscriptions.append(topic)

        # Import-time tasks are tested through their methods below.
        loop = types.SimpleNamespace(create_task=lambda coroutine: coroutine.close())
        for board in ('pico_w', 'xiao_esp32c6'):
            self.board(board)
            sys.modules.pop('benq_halo.halo_mqtt', None)
            with patch.dict(sys.modules, {
                    'mqtt_as': types.SimpleNamespace(MQTTClient=Client, config={}),
                    'settings': types.SimpleNamespace(MQTT={'ssid': 'test', 'server': 'broker'}),
            }), patch.object(asyncio, 'get_event_loop', return_value=loop):
                mqtt = importlib.import_module('benq_halo.halo_mqtt')
                controls = [Mock() for _ in range(5)]
                entities = [
                    mqtt.HaMqttSwitch('onoff_status', 'On/Off', controls[0], True),
                    mqtt.HaMqttBrightnessLightWithColorTemp('front_light', 'Front Light', controls[1], True, 50, 4000, True),
                    mqtt.HaMqttBrightnessLight('back_light', 'Back Light', controls[2], True, 10, True),
                    mqtt.HaMqttSwitch('ultrasonic_sensor', 'Ultrasonic Sensor', controls[3], True),
                    mqtt.HaMqttButton('auto_config', 'Auto', controls[4]),
                ]
                asyncio.run(mqtt.conn_han(mqtt.mqtt_client))
                self.assertEqual(len(mqtt.mqtt_client.publications), 5)
                self.assertEqual(len(mqtt.mqtt_client.subscriptions), 5)
                for topic, data in mqtt.mqtt_client.publications:
                    discovery = json.loads(data)
                    self.assertEqual(discovery['device']['identifiers'], ['benq_halo2'])
                    self.assertTrue(topic.endswith(b'/config'))
                for entity, payload in zip(entities, (
                        {'state': 'OFF'}, {'state': 'ON', 'brightness': 128, 'color_temp': 5000},
                        {'state': 'OFF', 'brightness': 64}, {'state': 'OFF'}, {'state': 'Auto'})):
                    mqtt.sub_cb((entity.base_topic + '/set').encode(), json.dumps(payload).encode(), False)
                controls[0].off.assert_called_once()
                controls[1].on.assert_called_once()
                controls[1].brightness.assert_called_once_with(128)
                controls[1].color_temp.assert_called_once_with(5000)
                controls[2].off.assert_called_once()
                controls[2].brightness.assert_called_once_with(64)
                controls[3].off.assert_called_once()
                controls[4].start_auto.assert_called_once()
                entities[1].update(True, 75, 4500, False)
                asyncio.run(entities[1].update_state())
                self.assertEqual(entities[1].current_state['color_temp'], 4500)
                self.assertEqual(entities[1].availability_state, 'offline')
                # A real combined HA light message must produce one final RF
                # command even when its ACK contains no state payload.
                lamp = importlib.import_module('benq_halo').benq_halo()
                lamp._debug = False
                lamp.send_with_ack = Mock(return_value=True)
                lamp.read_ack = Mock(return_value=bytearray())
                front = mqtt.HaMqttBrightnessLightWithColorTemp(
                    'combined_test', 'Combined', sys.modules['benq_halo'].FrontLamp(lamp),
                    True, 50, 4000, True)
                front.receive((front.base_topic + '/set').encode(),
                    json.dumps({'state': 'ON', 'brightness': 128, 'color_temp_kelvin': 4500}).encode())
                lamp.send_with_ack.assert_called_once()
                sent = lamp.send_with_ack.call_args.args[0]
                self.assertEqual(sent[0], 0x03)
                self.assertEqual(sent[2], 50)
                self.assertEqual(sent[3:5], [0x11, 0x94])
                lamp.read_ack.assert_called_once()
                self.assertFalse(lamp._bc5602_tx_mode)
                self.assertEqual(front.discover_conf['brightness_scale'], 255)
                for percent, brightness in ((1, 3), (100, 255)):
                    front.receive((front.base_topic + '/set').encode(),
                        json.dumps({'state': 'ON', 'brightness': brightness}).encode())
                    self.assertEqual(lamp.front_lamp_brightness, percent)
                    front.is_updated = False
                    front.update(True, percent, 4500, True)
                    self.assertEqual(front.current_state['brightness'], brightness)
                    self.assertFalse(front.is_updated)
                # Puck actions, retries, and stale sync requests must produce
                # one state publication per actual change, with no RF echo.
                back = mqtt.HaMqttBrightnessLight(
                    'puck_back', 'Back', sys.modules['benq_halo'].BackLamp(lamp),
                    True, 25, True)
                lamp.prepare_to_receive_without_ack()
                lamp.send_with_ack.reset_mock()
                mqtt.mqtt_client.publications.clear()

                def puck(command, brightness, temperature=4500, control=49):
                    payload = bytes([command, control, brightness,
                        temperature >> 8, temperature & 255, 25,
                        temperature >> 8, temperature & 255, 1, 2])
                    wire = (int.from_bytes(bytes([80]) + payload, 'big') << 7).to_bytes(12, 'big')
                    lamp._bc5602._spi.responses = [b'\x00', b'\x0c', wire]
                    if lamp.receive_without_ack():
                        front.update(lamp.front_lamp_status, lamp.front_lamp_brightness,
                            lamp.front_lamp_color_temp, lamp.onoff_status)
                        back.update(lamp.back_lamp_status, lamp.back_lamp_brightness,
                            lamp.onoff_status)
                    # Exercise publication deduplication even with redundant
                    # dirty notifications from the application.
                    asyncio.run(front.update_state())
                    asyncio.run(back.update_state())

                for brightness in (1, 100, 75, 1):
                    puck(3, brightness)
                    puck(3, brightness)  # retransmission
                    puck(4, 50)          # cached query, not state
                    puck(0, 50)          # wake request
                    puck(5, 50)          # sleep request
                front_topic = front.base_topic + '/state'
                back_topic = back.base_topic + '/state'
                states = [json.loads(data) for topic, data in mqtt.mqtt_client.publications
                    if topic == front_topic]
                self.assertEqual([state['brightness'] for state in states], [3, 255, 191, 3])
                self.assertEqual(sum(topic == back_topic for topic, _ in mqtt.mqtt_client.publications), 1)
                puck(3, 1, temperature=5000)
                puck(3, 1, temperature=5000)
                puck(2, 1, temperature=5000, control=48)  # power off
                puck(2, 1, temperature=5000, control=48)
                states = [json.loads(data) for topic, data in mqtt.mqtt_client.publications
                    if topic == front_topic]
                self.assertEqual(len(states), 5)  # power uses availability
                self.assertEqual(states[-1]['color_temp'], 5000)
                availability = [json.loads(data) for topic, data in mqtt.mqtt_client.publications
                    if topic == front.base_topic + '/availability']
                self.assertEqual(availability, ['online', 'offline'])
                lamp.send_with_ack.assert_not_called()
                # Reconnect must restore retained state, despite the cache.
                mqtt.mqtt_client.publications.clear()
                asyncio.run(front.on_connect())
                asyncio.run(front.update_state())
                self.assertTrue(any(topic == front_topic for topic, _ in mqtt.mqtt_client.publications))
            sys.modules.pop('benq_halo.halo_mqtt', None)

    def test_valid_saved_address_skips_acquisition(self):
        address = importlib.import_module('address')
        with patch('builtins.open', side_effect=AssertionError('must not acquire')):
            address.ensure_address()

    def test_invalid_saved_address_triggers_acquisition_and_reset(self):
        from unittest.mock import mock_open, Mock
        address = importlib.import_module('address')
        for invalid in ([1, 2, 3], [1, 2, 3, 256], [1, 2, 3, -1], [True, 2, 3, 4]):
            sys.modules['halo2_address'].HALO2_ADDRESS = invalid
            address.machine.reset = Mock()
            with patch('builtins.open', mock_open(read_data='pass')) as source:
                address.ensure_address()
                source.assert_called_once_with('/find_halo2_address.py')
                address.machine.reset.assert_called_once()


if __name__ == '__main__':
    unittest.main()
