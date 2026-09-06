#!/usr/bin/env python3
"""Assemble a board's MicroPython filesystem without modifying device state."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ("pico_w", "xiao_esp32c6", "esp32_wroom")


def build(board, settings=None):
    if board not in BOARDS:
        raise ValueError("Unknown board: " + board)
    settings = Path(settings) if settings else ROOT / "config/settings.py"
    if not settings.is_file():
        raise FileNotFoundError("Copy config/settings.example.py to config/settings.py and fill in credentials")
    output = ROOT / "build" / board
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(ROOT / "firmware/shared", output,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "boards" / board / "board_config.py", output / "board_config.py")
    shutil.copy2(settings, output / "settings.py")
    saved_address = ROOT / "config/halo2_address.py"
    if saved_address.is_file():
        shutil.copy2(saved_address, output / "halo2_address.py")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", choices=BOARDS)
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    print(build(args.board, args.settings))
