#!/usr/bin/env python3
"""
Scan barcodes from Pi camera and log product details from the database.

Goals:
- reliable reruns (single-instance lock + clean camera shutdown)
- camera-only scanning (no stdin fallback)
- clear product logs for each scanned barcode
"""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import time
import threading
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except Exception:
    psycopg2 = None
    _PSYCOPG2_AVAILABLE = False

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return False

try:
    from picamera2 import Picamera2
    _PICAMERA_AVAILABLE = True
except Exception:
    Picamera2 = None
    _PICAMERA_AVAILABLE = False

try:
    from pyzbar.pyzbar import decode as zbar_decode
    _PYZBAR_AVAILABLE = True
except Exception:
    zbar_decode = None
    _PYZBAR_AVAILABLE = False

try:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735
    from PIL import Image, ImageDraw, ImageFont
    _DISPLAY_AVAILABLE = True
except Exception:
    spi = None
    st7735 = None
    Image = None
    ImageDraw = None
    ImageFont = None
    _DISPLAY_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    GPIO.setwarnings(False)
except Exception:
    GPIO = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("camera_product_logger")

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SCAN_DEBOUNCE_SECONDS = float(os.getenv("SCAN_DEBOUNCE_SECONDS", "1.2"))
LOCK_PATH = "/tmp/camera_product_logger.lock"
AUTO_TAKEOVER = os.getenv("CAMERA_LOGGER_AUTO_TAKEOVER", "1").strip().lower() in {
    "1", "true", "yes"
}
TFT_SPI_PORT = int(os.getenv("TFT_SPI_PORT", "0"))
TFT_SPI_DEVICE = int(os.getenv("TFT_SPI_DEVICE", "0"))
TFT_DC_PIN = int(os.getenv("TFT_DC_PIN", "24"))
TFT_RST_PIN = int(os.getenv("TFT_RST_PIN", "25"))
TFT_BUS_SPEED_HZ = int(os.getenv("TFT_BUS_SPEED_HZ", "4000000"))
TFT_CLEANUP_ON_EXIT = os.getenv("TFT_CLEANUP_ON_EXIT", "0").strip().lower() in {
    "1", "true", "yes"
}
TFT_REINIT_SECONDS = float(os.getenv("TFT_REINIT_SECONDS", "12"))


def _call_with_timeout(fn, timeout_seconds: float, name: str) -> bool:
    """Run a callable in a thread and continue even if it blocks."""
    done = {"ok": False}

    def _runner():
        try:
            fn()
        finally:
            done["ok"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout_seconds)
    if t.is_alive():
        logger.warning("Timeout while running %s", name)
        return False
    return True

TFT_WIDTH = 160
TFT_HEIGHT = 128

WHITE = "white"
BLACK = "black"
GREEN = "#00e676"
YELLOW = "#ffd600"
CYAN = "#00e5ff"
RED = "#ff5252"
GREY = "#888888"

_FONT_PATH_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_PATH_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _load_font(size: int, bold: bool = True):
    if not _DISPLAY_AVAILABLE:
        return None
    try:
        path = _FONT_PATH_BOLD if bold else _FONT_PATH_REG
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


@contextmanager
def get_db():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()


def get_product_by_barcode(barcode: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT barcode, name, price, category, weight_grams
                FROM products
                WHERE barcode = %s
                LIMIT 1
                """,
                (barcode,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


class SingleInstanceLock:
    """Prevent overlapping scanner runs that compete for camera resources."""

    def __init__(self, lock_path: str = LOCK_PATH):
        self._lock_path = lock_path
        self._fd = None
        self._holder_pid: int | None = None

    def acquire(self) -> bool:
        self._fd = open(self._lock_path, "a+")
        self._fd.seek(0)
        raw = self._fd.read().strip()
        try:
            self._holder_pid = int(raw) if raw else None
        except ValueError:
            self._holder_pid = None

        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None
            return False

        self._fd.seek(0)
        self._fd.truncate()
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        self._holder_pid = os.getpid()
        return True

    def holder_pid(self) -> int | None:
        return self._holder_pid

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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_camera_logger_process(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="ignore").replace("\x00", " ")
        return "python" in cmdline and "camera_product_logger.py" in cmdline and "Trolly_system" in cmdline
    except Exception:
        return False


def _terminate_process(pid: int, grace_seconds: float = 2.0) -> bool:
    if not _pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


class CameraScanner:
    def __init__(self):
        if not _PICAMERA_AVAILABLE:
            raise RuntimeError("picamera2 is not available")
        if not _PYZBAR_AVAILABLE:
            raise RuntimeError("pyzbar is not available")

        self._camera = Picamera2()
        config = self._camera.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        self._camera.configure(config)
        self._camera.start()
        logger.info("Camera initialized for barcode scanning")

    def read_barcode(self) -> str | None:
        frame = self._camera.capture_array()
        decoded = zbar_decode(frame)
        if not decoded:
            return None
        return decoded[0].data.decode("utf-8").strip()

    def close(self) -> None:
        if not self._camera:
            return

        def _stop():
            try:
                self._camera.stop()
            except Exception:
                pass

        def _close():
            try:
                self._camera.close()
            except Exception:
                pass

        _call_with_timeout(_stop, 1.0, "camera.stop")
        _call_with_timeout(_close, 1.5, "camera.close")
        self._camera = None


class TFTDisplay:
    """Small status screen for barcode logger events."""

    def __init__(self):
        self._device = None
        self._lock = threading.Lock()
        self._blink = False
        if not _DISPLAY_AVAILABLE:
            logger.warning("TFT display unavailable (luma/Pillow missing)")
            return
        if self._init_device():
            self._show_test_flash()

    def _hardware_reset(self) -> None:
        """Pulse the reset pin to recover panel after abrupt stops/restarts."""
        if GPIO is None:
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(TFT_RST_PIN, GPIO.OUT, initial=GPIO.HIGH)
            time.sleep(0.04)
            GPIO.output(TFT_RST_PIN, GPIO.LOW)
            time.sleep(0.12)
            GPIO.output(TFT_RST_PIN, GPIO.HIGH)
            time.sleep(0.18)
        except Exception as exc:
            logger.warning("TFT hardware reset failed: %s", exc)

    def _init_device(self) -> bool:
        try:
            self._hardware_reset()
            serial = spi(
                port=TFT_SPI_PORT,
                device=TFT_SPI_DEVICE,
                gpio_DC=TFT_DC_PIN,
                gpio_RST=TFT_RST_PIN,
                bus_speed_hz=TFT_BUS_SPEED_HZ,
            )
            self._device = st7735(
                serial,
                width=TFT_WIDTH,
                height=TFT_HEIGHT,
                rotate=2,
                bgr=True,
            )
            logger.info(
                "TFT display initialized (port=%d device=%d dc=%d rst=%d speed=%d)",
                TFT_SPI_PORT,
                TFT_SPI_DEVICE,
                TFT_DC_PIN,
                TFT_RST_PIN,
                TFT_BUS_SPEED_HZ,
            )
            return True
        except Exception as exc:
            logger.warning("TFT init failed: %s", exc)
            self._device = None
            return False

    def _render(self, image) -> None:
        if not self._device and not self._init_device():
            return
        with self._lock:
            try:
                self._device.display(image)
            except Exception as exc:
                logger.warning("TFT render failed (%s), retrying after re-init", exc)
                self._device = None
                if not self._init_device():
                    return
                try:
                    self._device.display(image)
                except Exception as exc2:
                    logger.warning("TFT second render attempt failed: %s", exc2)

    def force_reinit(self) -> bool:
        """Recreate the TFT device explicitly to recover from panel lockups."""
        self._device = None
        return self._init_device()

    def _show_test_flash(self) -> None:
        """Show high-contrast flashes so user can confirm panel is alive."""
        if not self._device:
            return
        try:
            for color in ("white", "red", "green", "blue", "black"):
                img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), color)
                self._render(img)
                time.sleep(0.08)
        except Exception as exc:
            logger.warning("TFT flash test failed: %s", exc)

    def _blank(self, color: str = BLACK):
        img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), color)
        return img, ImageDraw.Draw(img)

    def _truncate(self, text: str, max_width: int, size: int = 12, bold: bool = False) -> str:
        font = _load_font(size, bold=bold)
        out = text
        while out:
            bbox = font.getbbox(out)
            if (bbox[2] - bbox[0]) <= max_width:
                return out
            out = out[:-1]
        return ""

    def show_boot(self) -> None:
        if not self._device:
            return
        img, draw = self._blank()
        draw.text((18, 30), "CAM LOGGER", font=_load_font(18), fill=YELLOW)
        draw.text((18, 58), "TFT READY", font=_load_font(14), fill=GREEN)
        draw.text((12, 90), "Starting camera...", font=_load_font(11, bold=False), fill=CYAN)
        self._render(img)

    def show_waiting(self) -> None:
        if not self._device:
            return
        img, draw = self._blank()
        draw.rectangle([(0, 0), (TFT_WIDTH, 24)], fill="#003a48")
        draw.text((8, 4), "SCAN BARCODE", font=_load_font(14), fill=WHITE)

        blink_color = GREEN if self._blink else "#124a1d"
        draw.ellipse([(140, 6), (152, 18)], fill=blink_color, outline=GREEN)

        draw.line([(8, 52), (152, 52)], fill=GREY, width=1)
        draw.text((10, 62), "Point camera at code", font=_load_font(11, bold=False), fill=CYAN)
        draw.text((10, 84), "Logging product info", font=_load_font(11, bold=False), fill=YELLOW)
        draw.text((10, 104), "Status: READY", font=_load_font(11, bold=False), fill=GREEN)
        self._render(img)
        self._blink = not self._blink

    def show_not_found(self, barcode: str) -> None:
        if not self._device:
            return
        img, draw = self._blank()
        draw.text((10, 14), "NOT FOUND", font=_load_font(16), fill=RED)
        draw.line([(8, 38), (152, 38)], fill=GREY, width=1)
        short = self._truncate(barcode, max_width=146, size=11, bold=False)
        draw.text((8, 52), short, font=_load_font(11, bold=False), fill=WHITE)
        draw.text((8, 90), "No product in DB", font=_load_font(11, bold=False), fill=YELLOW)
        self._render(img)

    def show_product(self, product: dict) -> None:
        if not self._device:
            return
        img, draw = self._blank()
        draw.text((8, 8), "PRODUCT FOUND", font=_load_font(14), fill=GREEN)
        draw.line([(8, 30), (152, 30)], fill=GREY, width=1)

        name = self._truncate(str(product.get("name", "-")), max_width=146, size=12, bold=True)
        draw.text((8, 36), name, font=_load_font(12), fill=WHITE)

        price = float(product.get("price", 0.0) or 0.0)
        draw.text((8, 58), f"Price: INR {price:.2f}", font=_load_font(11, bold=False), fill=YELLOW)
        draw.text((8, 76), f"Cat: {product.get('category', '-')}", font=_load_font(10, bold=False), fill=CYAN)
        draw.text((8, 92), f"Wt: {product.get('weight_grams', '-')}", font=_load_font(10, bold=False), fill=CYAN)
        self._render(img)

    def show_error(self, message: str) -> None:
        if not self._device:
            return
        img, draw = self._blank()
        draw.text((8, 12), "ERROR", font=_load_font(18), fill=RED)
        draw.line([(8, 38), (152, 38)], fill=GREY, width=1)
        short = self._truncate(message, max_width=146, size=10, bold=False)
        draw.text((8, 52), short, font=_load_font(10, bold=False), fill=WHITE)
        self._render(img)

    def close(self) -> None:
        if not self._device:
            return
        if TFT_CLEANUP_ON_EXIT:
            try:
                if hasattr(self._device, "clear"):
                    self._device.clear()
            except Exception:
                pass
            try:
                if hasattr(self._device, "cleanup"):
                    self._device.cleanup()
            except Exception:
                pass
            if GPIO is not None:
                try:
                    GPIO.cleanup([TFT_DC_PIN, TFT_RST_PIN])
                except Exception:
                    pass
        self._device = None


def main() -> int:
    if not _PSYCOPG2_AVAILABLE:
        logger.error("Missing psycopg2-binary")
        return 1
    if not DATABASE_URL:
        logger.error("DATABASE_URL missing in .env")
        return 1

    lock = SingleInstanceLock()
    if not lock.acquire():
        holder = lock.holder_pid()
        if holder and AUTO_TAKEOVER and holder != os.getpid() and _is_camera_logger_process(holder):
            logger.warning("Detected existing camera logger pid=%d, attempting safe takeover", holder)
            if _terminate_process(holder):
                logger.info("Previous camera logger pid=%d stopped, retrying lock", holder)
                if not lock.acquire():
                    logger.error("Could not acquire lock after takeover attempt")
                    return 1
            else:
                logger.error("Could not stop existing camera logger pid=%d", holder)
                return 1
        else:
            if holder:
                logger.error(
                    "Another camera logger is running (pid=%d). Stop it first.",
                    holder,
                )
            else:
                logger.error("Another camera logger is running. Stop it first.")
            return 1

    scanner = None
    display = None
    running = True
    last_barcode = ""
    last_seen_at = 0.0
    waiting_refresh_at = 0.0
    tft_reinit_at = time.monotonic()
    stop_requested_at = 0.0
    stop_signal_count = 0

    def _handle_stop(_sig=None, _frame=None):
        nonlocal running, stop_requested_at, stop_signal_count
        stop_signal_count += 1
        stop_requested_at = time.monotonic()
        running = False
        if stop_signal_count >= 2:
            os._exit(130)

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        display = TFTDisplay()
        display.show_boot()
        scanner = CameraScanner()
        logger.info("Ready. Point camera at barcode...")
        if display:
            display.show_waiting()

        while running:
            if stop_requested_at and (time.monotonic() - stop_requested_at) > 2.0:
                logger.warning("Forced shutdown after stop timeout")
                os._exit(130)

            if display and time.monotonic() - waiting_refresh_at > 1.0:
                display.show_waiting()
                waiting_refresh_at = time.monotonic()

            if display and time.monotonic() - tft_reinit_at > TFT_REINIT_SECONDS:
                if display.force_reinit():
                    logger.info("Periodic TFT re-init completed")
                    display.show_waiting()
                tft_reinit_at = time.monotonic()

            try:
                barcode = scanner.read_barcode()
            except Exception as exc:
                logger.error("Camera read error: %s", exc)
                if display:
                    display.show_error("Camera read failed")
                time.sleep(0.2)
                continue

            if not barcode:
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if barcode == last_barcode and (now - last_seen_at) < SCAN_DEBOUNCE_SECONDS:
                continue
            last_barcode = barcode
            last_seen_at = now

            logger.info("Scanned barcode: %s", barcode)
            try:
                product = get_product_by_barcode(barcode)
            except Exception as exc:
                logger.error("Database lookup failed for %s: %s", barcode, exc)
                continue

            if not product:
                logger.warning("Product not found for barcode=%s", barcode)
                if display:
                    display.show_not_found(barcode)
                    time.sleep(1.1)
                    display.show_waiting()
                    tft_reinit_at = time.monotonic()
                continue

            logger.info(
                "Product details: barcode=%s name=%s price=%.2f category=%s weight_grams=%s",
                product.get("barcode"),
                product.get("name"),
                float(product.get("price", 0.0)),
                product.get("category"),
                product.get("weight_grams"),
            )
            if display:
                display.show_product(product)
                tft_reinit_at = time.monotonic()

    finally:
        if scanner:
            scanner.close()
        if display:
            display.close()
        lock.release()
        logger.info("Camera product logger stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
