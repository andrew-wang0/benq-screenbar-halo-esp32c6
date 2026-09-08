"""Seeed Studio XIAO ESP32C6; numbers are GPIOs, not D labels."""
NAME = "xiao_esp32c6"
PLATFORM = "esp32"
SPI_ID = 1
CS = 18   # D10 -> BM5602 CSN
SCK = 20  # D9 -> BM5602 SCK
MOSI = 19 # D8 -> BM5602 SDIO
MISO = 17 # D7 -> BM5602 GIO4
RADIO_MISO_GIO = 4
# Retain the SoftSPI transport verified with this wiring.
USE_SOFT_SPI = True
SPI_BAUDRATE = 500_000
# An MCU soft reset does not reset the external radio. The before/after
# hardware check recovered RX only after resetting the BM5602 itself.
RESET_RADIO_ON_INIT = True
# Upstream leaves calibration disabled; our hardware test stalled at OM=08.
CALIBRATE_ON_CHANNEL_CHANGE = False
# No periodic RF writes while this lamp provides no status payload in its ACKs.
STATUS_POLL_INTERVAL_MS = 0
LED = 15
LED_ACTIVE_LOW = True
ANTENNA_ENABLE = 3  # Active low
ANTENNA_SELECT = 14 # Low: onboard ceramic antenna; high: external U.FL
