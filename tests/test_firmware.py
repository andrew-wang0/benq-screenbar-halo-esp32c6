"""Host-side board and protocol regression tests; no physical radio required."""
import importlib
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

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, count):
        return self.responses.pop(0) if self.responses else bytes([0x10] * count)

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
                self.assertEqual(radio._spi.options['baudrate'], 4_000_000)
                # Route SDO before attempting any register reads.
                route = [b'\x46\x40', b'\x47\x10'] if name == 'xiao_esp32c6' else [b'\x46\x48', b'\x47\x00']
                self.assertEqual(radio._spi.writes[:2], route)
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

    def test_pico_and_xiao_produce_identical_radio_traffic(self):
        traces = []
        for board in ('pico_w', 'xiao_esp32c6'):
            self.board(board)
            halo = importlib.import_module('benq_halo')
            lamp = halo.benq_halo()
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
