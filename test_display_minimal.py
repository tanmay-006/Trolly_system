#!/usr/bin/env python3
"""
Minimal display test using main.py initialization style
"""
import os
import sys
import threading
import fcntl

try:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735
    from PIL import Image, ImageDraw, ImageFont
    _DISPLAY_AVAILABLE = True
    print("[OK] Display libraries imported")
except ImportError as e:
    print(f"[ERROR] Import failed: {e}")
    _DISPLAY_AVAILABLE = False
    sys.exit(1)

# TFT parameters
TFT_WIDTH = 160
TFT_HEIGHT = 128
BG = "black"
WHITE = "white"

class SingleInstanceLock:
    """Prevent multiple instances."""
    def __init__(self, lock_path: str = "/tmp/test_display_main.lock"):
        self._lock_path = lock_path
        self._fd = None

    def acquire(self) -> bool:
        self._fd = open(self._lock_path, "w")
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        return True

    def release(self) -> None:
        if not self._fd:
            return
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fd.close()
        except Exception:
            pass
        self._fd = None

# Initialize lock
lock = SingleInstanceLock()
if not lock.acquire():
    print("[ERROR] Could not acquire lock - another instance running")
    sys.exit(1)
print("[OK] Lock acquired")

try:
    # Initialize display
    print("[INFO] Creating SPI interface...")
    serial = spi(
        port=0, device=0,
        gpio_DC=24, gpio_RST=25,
        bus_speed_hz=16_000_000,
    )
    print("[OK] SPI created")

    print("[INFO] Creating st7735 device...")
    device = st7735(
        serial,
        width=TFT_WIDTH, height=TFT_HEIGHT,
        rotate=2, bgr=True,
    )
    print(f"[OK] Device created: {device}")

    # Test display
    print("[INFO] Displaying WHITE...")
    img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), "white")
    device.display(img)
    print("[OK] WHITE displayed. Should see white screen.")
    input("Press Enter to continue...")

    print("[INFO] Displaying BLACK with WHITE text...")
    img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), "black")
    draw = ImageDraw.Draw(img)
    draw.text((30, 50), "Hello!", fill="white")
    device.display(img)
    print("[OK] BLACK with text displayed. Should see 'Hello!' in white")
    input("Press Enter to continue...")

    print("[INFO] Testing with minimal image (black bg)...")
    img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw.text((22, 24), "SMART", fill=WHITE)
    device.display(img)
    print("[OK] Text image displayed. Should see 'SMART' text")
    input("Press Enter to finish...")

    print("[SUCCESS] All tests passed")

finally:
    lock.release()
    print("[INFO] Lock released")
