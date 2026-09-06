"""Board setup shared by the application, address finder and sniffer."""
import sys
from machine import Pin
import board_config as board

if sys.platform != board.PLATFORM:
    raise RuntimeError("Wrong firmware bundle: {} requires {}".format(board.NAME, board.PLATFORM))

_led = None
_antenna_enable = None
_antenna_select = None


def setup():
    global _led, _antenna_enable, _antenna_select
    if _led is not None:
        return
    if board.ANTENNA_ENABLE is not None:
        _antenna_enable = Pin(board.ANTENNA_ENABLE, Pin.OUT, value=0)
        _antenna_select = Pin(board.ANTENNA_SELECT, Pin.OUT, value=0)
    _led = Pin(board.LED, Pin.OUT, value=int(board.LED_ACTIVE_LOW))


def wifi_status(connected):
    setup()
    _led.value(int(bool(connected) != board.LED_ACTIVE_LOW))
