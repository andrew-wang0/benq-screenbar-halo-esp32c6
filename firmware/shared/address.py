"""Acquire and persist the lamp address before starting either application."""
import machine


def ensure_address():
    try:
        from halo2_address import HALO2_ADDRESS
    except (ImportError, SyntaxError):
        HALO2_ADDRESS = None
    if (isinstance(HALO2_ADDRESS, list) and len(HALO2_ADDRESS) == 4
            and all(type(x) is int and 0 <= x <= 255 for x in HALO2_ADDRESS)):
        return
    print("No valid HALO2_ADDRESS: set back brightness 10% and color temperature 3925K on remote control.")
    # Use a separate namespace so the acquisition sync word cannot replace the
    # application address. Reboot releases acquisition buffers/SPI on both MCUs.
    with open("/find_halo2_address.py") as source:
        exec(source.read(), {})
    machine.reset()
