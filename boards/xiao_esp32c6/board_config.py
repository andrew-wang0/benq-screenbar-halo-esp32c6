"""Seeed Studio XIAO ESP32C6; numbers are GPIOs, not D labels."""
NAME = "xiao_esp32c6"
PLATFORM = "esp32"
SPI_ID = 1
CS = 18   # D10 -> BM5602 CSN
SCK = 20  # D9 -> BM5602 SCK
MOSI = 19 # D8 -> BM5602 SDIO
MISO = 17 # D7 -> BM5602 GIO4
RADIO_MISO_GIO = 4
# The BM5602 is rated for at most 8 Mbps; 4 MHz leaves wiring margin.
SPI_BAUDRATE = 4_000_000
LED = 15
LED_ACTIVE_LOW = True
ANTENNA_ENABLE = 3  # Active low
ANTENNA_SELECT = 14 # Low: onboard ceramic antenna; high: external U.FL
