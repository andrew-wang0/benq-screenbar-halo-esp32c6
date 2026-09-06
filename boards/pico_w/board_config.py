"""Raspberry Pi Pico W wiring (GPIO numbers)."""
NAME = "pico_w"
PLATFORM = "rp2"
SPI_ID = 0
CS = 1
SCK = 2
MOSI = 3
MISO = 4
RADIO_MISO_GIO = 2
# The BM5602 is rated for at most 8 Mbps; 4 MHz leaves wiring margin.
SPI_BAUDRATE = 4_000_000
LED = "LED"
LED_ACTIVE_LOW = False
ANTENNA_ENABLE = None
ANTENNA_SELECT = None
