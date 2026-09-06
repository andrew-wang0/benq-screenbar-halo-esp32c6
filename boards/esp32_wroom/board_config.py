"""Legacy ESP32-WROOM DevKit wiring."""
NAME = "esp32_wroom"
PLATFORM = "esp32"
SPI_ID = 1
CS = 5
SCK = 18
MOSI = 23
MISO = 19
RADIO_MISO_GIO = 2
# The BM5602 is rated for at most 8 Mbps; 4 MHz leaves wiring margin.
SPI_BAUDRATE = 4_000_000
LED = 2
LED_ACTIVE_LOW = False
ANTENNA_ENABLE = None
ANTENNA_SELECT = None
