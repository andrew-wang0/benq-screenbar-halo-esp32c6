"""Run in RAM with mpremote on the XIAO ESP32C6; no project imports needed.

Wiring: CSN=D10/GPIO18, SCK=D9/GPIO20, SDIO=D8/GPIO19,
BM5602 GIO4=D7/GPIO17. No MQTT, RF transmission, or saved-address writes.
Register definitions/settings: Holtek BC5602 v1.20 and AN0560SC v1.10:
https://www.holtek.com.tw/webapi/11842/BC5602v120.pdf
https://www.holtek.com.cn/webapi/116740/an0560scv110.pdf
"""

from machine import Pin, SoftSPI
import time

CHANNEL = 5
CHECK_ADDRESS_ONLY = True  # False reruns the v2 clock/calibration comparison.
# Latest capture, after upstream's right shift: 55 25 E3 39 B1.
# 55 is a legal preamble; following PCF decodes to length 10.
# Candidate only: verify with a direct address filter before persisting it.
ADDRESS_CANDIDATE = b"\xb1\x39\xe3\x25"
LISTEN_MS = 15000  # Two startup methods x three configurations = 90 seconds.
SYNC = b"\x55\x0f\x0a"
MODES = ("deep-sleep", "middle-sleep", "light-sleep", "reserved",
         "TX", "RX", "cal", "undefined")

cs = Pin(18, Pin.OUT, value=1)
spi = SoftSPI(baudrate=500000, polarity=0, phase=0,
              sck=Pin(20), mosi=Pin(19), miso=Pin(17, Pin.IN))


def transfer(data, count=0):
    cs.value(0)
    try:
        spi.write(bytes(data))
        return spi.read(count) if count else b""
    finally:
        cs.value(1)


def read(reg):
    return transfer([0xC0 | reg], 1)[0]


def write(reg, value):
    transfer([0x40 | reg, value])


def checked_write(reg, value, mask=0xFF):
    write(reg, value)
    actual = read(reg)
    if actual & mask != value & mask:
        raise RuntimeError("Readback failed reg=%02X wrote=%02X read=%02X mask=%02X"
                           % (reg, value, actual, mask))


def bank(number):
    checked_write(0x00, (read(0x00) & 0xFC) | number)


def route_sdo():
    # Writes only: reset may have disconnected GIO4 from SPI SDO.
    write(0x06, 0x40)
    write(0x07, 0x10)


def snapshot(label):
    # Caller must have selected bank 0.
    regs = [(name, read(reg)) for name, reg in (
        ("CFG1", 0x00), ("RC1", 0x01), ("MASK", 0x03),
        ("IRQ1", 0x04), ("STATUS", 0x05), ("CE", 0x15),
        ("OM", 0x20), ("STA1", 0x26), ("RSSI2", 0x28))]
    print(label, " ".join("%s=%02X" % pair for pair in regs),
          "mode=" + MODES[dict(regs)["STA1"] & 7])


def initialize(recommended, enable_fsyck=False):
    transfer([0x08])  # Software reset, then restore SDO before reading.
    time.sleep_ms(5)
    route_sdo()
    transfer([0x0C])  # Light sleep; clears CE.
    time.sleep_ms(5)
    bank(0)
    snapshot("After reset")
    # Clear PWRON and RSTLL, enable XCLK; retain clock divider/output choice.
    # Explicitly compare FSYCK disabled/enabled; this is a diagnostic variable,
    # not a claim that FSYCK_RDY measures synthesizer lock.
    checked_write(0x01, 0x12 if enable_fsyck else 0x10, 0x9F)
    start = time.ticks_ms()
    while not read(0x01) & 0x20:
        if time.ticks_diff(time.ticks_ms(), start) >= 200:
            snapshot("XCLK timeout")
            raise RuntimeError("XCLK_RDY never set; stop before FIFO/RF tests")
        time.sleep_ms(1)
    print("XCLK ready; FSYCK_RDY is not a PLL-lock indicator")
    time.sleep_ms(5)
    snapshot("Clock configuration")

    # Round-trip several legal channels while idle to exercise changing bits.
    for channel in (5, 46, 75, CHANNEL):
        checked_write(0x10, channel)
    print("RFCH read/write PASS")

    if recommended:
        for reg, value in ((0x0D, 0x20), (0x0F, 0x14), (0x16, 0x66),
                           (0x17, 0xAA), (0x18, 0x45)):
            checked_write(reg, value)
        bank(1)
        for reg, value in ((0x20, 0x0C), (0x21, 0x03), (0x23, 0x10),
                           (0x25, 0xCC), (0x26, 0x4C), (0x27, 0x80)):
            checked_write(reg, value)
        bank(2)
        for reg, value in ((0x28, 0xA0), (0x2D, 0x18), (0x2E, 0xEC),
                           (0x36, 0x03), (0x38, 0x0A), (0x39, 0x12),
                           (0x3B, 0x94), (0x3C, 0x43)):
            checked_write(reg, value)
        bank(0)
        print("Recommended analog settings readback PASS")

    # Packet handling, 125 kbps, 3-byte sync, static 16-byte RX, CRC/ACK off.
    checked_write(0x00, read(0x00) & ~0x10)  # DIR_EN=0
    for reg, value in ((0x03, 0), (0x13, 0), (0x15, 0), (0x11, 0x42),
                       (0x09, 0), (0x0A, 0x36), (0x0B, 0),
                       (0x2A, 0), (0x2B, 0), (0x2C, 16),
                       (0x32, 0), (0x37, 1)):
        checked_write(reg, value)
    transfer(b"\x10" + SYNC)
    actual = transfer([0x90], 3)
    print("Sync readback:", actual.hex())
    if actual != SYNC:
        raise RuntimeError("Sync readback failed")


def fifo_check():
    # CE=0 and no TX strobe: test storage only, never transmit dummy bytes.
    transfer([0x09])
    transfer([0x89])
    write(0x04, 0x70)  # Clear latched IRQ flags (write-one-to-clear).
    snapshot("FIFO flushed (expect STATUS=11)")
    transfer(b"\x11" + bytes(range(10)))
    snapshot("One enqueue, CE=0 (expect STATUS=01)")
    transfer([0x09])
    snapshot("TX flushed again (expect STATUS=11)")
    time.sleep_ms(20)
    snapshot("TX flush +20ms (expect STATUS=11)")


def calibrate():
    transfer([0x0C])
    time.sleep_ms(5)
    write(0x20, read(0x20) | 8)
    start = time.ticks_ms()
    first = read(0x20)
    while read(0x20) & 8:
        if time.ticks_diff(time.ticks_ms(), start) >= 500:
            snapshot("CAL TIMEOUT")
            # Attempt RX independently, with calibration explicitly disabled.
            # Receiving here would not prove calibrated frequency accuracy.
            checked_write(0x20, read(0x20) & ~8)
            print("Calibration disabled after timeout; testing RX anyway")
            return False
        time.sleep_ms(1)
    print("CAL bit clear after %d ms; first OM=%02X (may finish before first read)"
          % (time.ticks_diff(time.ticks_ms(), start), first))
    snapshot("After calibration")
    return True


def listen(method, duration_ms=LISTEN_MS, max_printed=30):
    transfer([0x0C])
    time.sleep_ms(5)
    checked_write(0x03, 1)  # PRX
    transfer([0x89])
    write(0x04, 0x70)
    print("LISTEN %s: %d MHz for %d seconds; operate remote now"
          % (method, 2400 + CHANNEL, duration_ms // 1000))
    if method == "CE only":
        write(0x15, 1)
    else:
        transfer([0x8E])  # Sets CE itself; no preceding CE write.
    start = time.ticks_ms()
    last_log = start
    snapshot("RX entry")
    packets = 0
    rx_samples = 0
    while time.ticks_diff(time.ticks_ms(), start) < duration_ms:
        if read(0x26) & 7 == 5:
            rx_samples += 1
        if not read(0x05) & 1:
            length = read(0x0C)
            if not 1 <= length <= 32:
                snapshot("Invalid RX length=%d; flushing" % length)
                transfer([0x89])
            else:
                raw = transfer([0xBF], length)
                packets += 1
                if packets <= max_printed:
                    print("RAW len=%d: %s" % (length, raw.hex()))
            write(0x04, 0x40)
        now = time.ticks_ms()
        if time.ticks_diff(now, last_log) >= 1000:
            snapshot("RX %d ms" % time.ticks_diff(now, start))
            last_log = now
        time.sleep_ms(2)
    print("RESULT %s: packets=%d samples_in_RX=%d" % (method, packets, rx_samples))
    transfer([0x0C])
    return packets


def check_address():
    # Bracket address/length comparisons with the previously working filter.
    # Reset identically for each case; calibration and FSYCK stay disabled.
    reversed_address = bytes(ADDRESS_CANDIDATE[i]
                             for i in range(len(ADDRESS_CANDIDATE) - 1, -1, -1))
    cases = (
        ("control-before", SYNC, 16),
        ("corrected-16", ADDRESS_CANDIDATE, 16),
        ("corrected-reverse-16", reversed_address, 16),
        ("control-after", SYNC, 16),
    )
    results = []
    print("Keep the v2 settings: BACK 10% and 3925 K; wake/operate remote at those settings")
    for label, address, width in cases:
        print("\nCASE %s address=%s width=%d" % (label, address.hex(), width))
        initialize(False, False)
        checked_write(0x20, read(0x20) & ~8)
        checked_write(0x11, 0x42 if len(address) == 3 else 0x82)
        transfer(b"\x10" + address)
        actual = transfer([0x90], len(address))
        if actual != address:
            raise RuntimeError("Address readback failed: " + actual.hex())
        # Static raw reception: CRC and auto-ACK remain disabled.
        checked_write(0x2C, width)
        print("Case readback PASS; raw capture, no bit shifting, no CRC validation")
        count = listen("CE only", duration_ms=10000, max_printed=40)
        results.append((label, count))
    print("\nADDRESS COMPARISON SUMMARY")
    for label, count in results:
        print("%s packets=%d" % (label, count))
    if not results[0][1] or not results[-1][1]:
        print("Control missing packets: negative candidate results are inconclusive")
    print("Packet matches alone do not validate the HALO payload or CRC")


try:
    print("BC5602 diagnostic v5; XIAO ESP32C6, SoftSPI 500 kHz, GIO4 SDO")
    print("Fixed channel 5; address-check mode:", CHECK_ADDRESS_ONLY)
    route_sdo()
    transfer([0x0C])
    time.sleep_ms(5)
    bank(0)
    for attempt in range(3):
        print("ID read %d: command 9F=%s; old command DF=%s"
              % (attempt + 1, transfer([0x9F], 3).hex(), transfer([0xDF], 3).hex()))
    if CHECK_ADDRESS_ONLY:
        check_address()
    else:
        print("Keep remote BACK at 10% and 3925 K")
        for recommended, enable_fsyck in ((False, False), (True, False), (True, True)):
            print("\nCONFIG:", "recommended analog settings" if recommended else "baseline: no analog overrides")
            print("FSYCK_EN experiment:", int(enable_fsyck))
            initialize(recommended, enable_fsyck)
            fifo_check()
            calibrated = calibrate()
            print("Calibration completed:", calibrated)
            listen("CE only")
            listen("RX strobe only")
    print("DONE. No address saved. Power-cycle before running normal firmware.")
finally:
    # Also run on exceptions/Ctrl-C. Restore a known bank and stop the radio.
    transfer([0x0C])
    bank(0)
    transfer([0x09])
    transfer([0x89])
    cs.value(1)
    spi.deinit()
