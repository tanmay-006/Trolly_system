#!/usr/bin/env python3
"""
Smart Trolley Pi Runtime — Complete Implementation
Integrates: camera barcode scanning, HX711 weight, TFT display,
            quantity management, checkout flow, and receipt printing.

Terminal controls (placeholder for GPIO button):
  - Type 'done' to trigger checkout
  - Type 'clear' to reset cart
"""

from __future__ import annotations

import os
import sys
import time
import signal
import logging
import threading
import queue
import json
import uuid
import fcntl
from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager

# ── Dependencies ─────────────────────────────────────────────────────────────
try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except Exception:
    psycopg2 = None
    _PSYCOPG2_AVAILABLE = False

try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except Exception:
    _DOTENV_AVAILABLE = False
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
    from hx711 import HX711
    _HX711_AVAILABLE = True
except Exception:
    HX711 = None
    _HX711_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    GPIO.setwarnings(False)
except Exception:
    GPIO = None

try:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735
    from PIL import Image, ImageDraw, ImageFont
    import qrcode
    _DISPLAY_AVAILABLE = True
except ImportError:
    _DISPLAY_AVAILABLE = False

try:
    from escpos.printer import Bluetooth as BTWPrinter
    _BLUETOOTH_PRINTER_AVAILABLE = True
except ImportError:
    BTWPrinter = None
    _BLUETOOTH_PRINTER_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("trolley_runtime")

# ── Configuration ────────────────────────────────────────────────────────────
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
UPI_ID = os.getenv("UPI_ID", "yourshop@upi")
SHOP_NAME = os.getenv("SHOP_NAME", "Smart Trolley Shop")
BLUETOOTH_PRINTER_MAC = os.getenv("BLUETOOTH_PRINTER_MAC", "").strip()

HX711_DOUT_PIN = int(os.getenv("HX711_DOUT_PIN", "5"))
HX711_SCK_PIN = int(os.getenv("HX711_SCK_PIN", "6"))
HX711_DISABLE = os.getenv("HX711_DISABLE", "0").strip().lower() in {"1", "true", "yes"}
SCAN_DEBOUNCE_SECONDS = float(os.getenv("SCAN_DEBOUNCE_SECONDS", "1.2"))
SMART_TROLLEY_AUTO_TAKEOVER = os.getenv("SMART_TROLLEY_AUTO_TAKEOVER", "1").strip().lower() in {
    "1", "true", "yes"
}
TFT_SPI_PORT = int(os.getenv("TFT_SPI_PORT", "0"))
TFT_SPI_DEVICE = int(os.getenv("TFT_SPI_DEVICE", "0"))
TFT_DC_PIN = int(os.getenv("TFT_DC_PIN", "24"))
TFT_RST_PIN = int(os.getenv("TFT_RST_PIN", "25"))
TFT_BUS_SPEED_HZ = int(os.getenv("TFT_BUS_SPEED_HZ", "4000000"))
TFT_REINIT_SECONDS = float(os.getenv("TFT_REINIT_SECONDS", "25"))
TFT_CLEANUP_ON_EXIT = os.getenv("TFT_CLEANUP_ON_EXIT", "0").strip().lower() in {
    "1", "true", "yes"
}

# Special barcode commands
BARCODE_REMOVE_LAST = "REMOVE_LAST"
BARCODE_CMD_CLEAR = "CMD_CLEAR"

# TFT Display geometry
TFT_WIDTH = 160
TFT_HEIGHT = 128

# Colors
BG = "black"
WHITE = "white"
GREEN = "#00e676"
DK_GREEN = "#1b5e20"
YELLOW = "#ffd600"
CYAN = "#00e5ff"
GREY = "#555555"
LT_GREY = "#888888"
RED = "#ff5252"
DK_RED = "#b71c1c"
ORANGE = "#ff9100"

_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_PATH_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


# ── Database ─────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
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


def save_transaction(session_id: str, items: list, total: float,
                     status: str = "paid", payment_method: str = "UPI/QR",
                     upi_ref: str = "") -> int:
    """Insert a completed transaction. Returns the new row id."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions
                    (session_id, items, total_amount, payment_status, payment_method, upi_ref)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s)
                RETURNING id
                """,
                (session_id, json.dumps(items), total, status, payment_method, upi_ref)
            )
            conn.commit()
            return cur.fetchone()["id"]


# ── Data Models ──────────────────────────────────────────────────────────────
@dataclass
class CartItem:
    barcode: str
    name: str
    price: float
    quantity: int = 1

    def line_total(self) -> float:
        return self.price * self.quantity

    def to_dict(self) -> dict:
        return {
            "id": self.barcode,
            "name": self.name,
            "price": self.price,
            "quantity": self.quantity,
            "total": self.line_total()
        }


class SessionCart:
    def __init__(self):
        self.items: dict[str, CartItem] = {}
        self.last_barcode: str | None = None

    def add(self, product: dict) -> tuple[str, int]:
        """Add product, increment quantity. Returns (name, new_qty)."""
        code = str(product["barcode"])
        if code in self.items:
            self.items[code].quantity += 1
            qty = self.items[code].quantity
            name = self.items[code].name
        else:
            self.items[code] = CartItem(
                barcode=code,
                name=str(product["name"]),
                price=float(product["price"]),
                quantity=1,
            )
            qty = 1
            name = str(product["name"])
        self.last_barcode = code
        return (name, qty)

    def decrement_last(self) -> tuple[str, int] | None:
        """Decrement quantity of last scanned item. Returns (name, new_qty) or None."""
        if not self.last_barcode or self.last_barcode not in self.items:
            return None
        item = self.items[self.last_barcode]
        item.quantity -= 1
        if item.quantity <= 0:
            name = item.name
            del self.items[self.last_barcode]
            return (name, 0)
        return (item.name, item.quantity)

    def remove_last(self) -> str | None:
        """Remove all instances of last scanned product. Returns name or None."""
        if not self.last_barcode or self.last_barcode not in self.items:
            return None
        name = self.items[self.last_barcode].name
        del self.items[self.last_barcode]
        return name

    def clear(self):
        """Clear all items from cart."""
        self.items.clear()
        self.last_barcode = None

    @property
    def unique_item_count(self) -> int:
        return len(self.items)

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items.values())

    @property
    def subtotal(self) -> float:
        return sum(item.line_total() for item in self.items.values())

    def to_list(self) -> list[dict]:
        return [item.to_dict() for item in self.items.values()]


# ── HX711 Weight Reader ──────────────────────────────────────────────────────
class WeightReader:
    INIT_TIMEOUT_SECONDS = float(os.getenv("HX711_INIT_TIMEOUT_SECONDS", "1.0"))
    READ_TIMEOUT_SECONDS = float(os.getenv("HX711_READ_TIMEOUT_SECONDS", "0.35"))

    def __init__(self, dout_pin: int, sck_pin: int):
        self._hx = None
        self._dout_pin = dout_pin
        self._sck_pin = sck_pin
        if HX711_DISABLE:
            logger.warning("HX711 disabled by HX711_DISABLE flag")
            return
        if not _HX711_AVAILABLE:
            logger.warning("HX711 package unavailable; weight reads disabled")
            return
        try:
            if hasattr(HX711, "GPIO") and hasattr(HX711.GPIO, "setwarnings"):
                HX711.GPIO.setwarnings(False)

            self._hx = HX711(dout_pin=dout_pin, pd_sck_pin=sck_pin)
            self._safe_call("reset", self.INIT_TIMEOUT_SECONDS)
            self._safe_call("tare", self.INIT_TIMEOUT_SECONDS)
            logger.info("HX711 initialized on DOUT=%s SCK=%s", dout_pin, sck_pin)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.warning("HX711 init failed: %s", exc)
            self._hx = None

    def _safe_call(self, method_name: str, timeout_seconds: float, *args):
        if not self._hx or not hasattr(self._hx, method_name):
            return None

        target = getattr(self._hx, method_name)
        result_box = {"value": None, "error": None}

        def _runner():
            try:
                result_box["value"] = target(*args)
            except Exception as err:
                result_box["error"] = err

        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()
        worker.join(timeout_seconds)

        if worker.is_alive():
            logger.warning("HX711 %s timed out after %.2fs", method_name, timeout_seconds)
            self._hx = None
            return None
        if result_box["error"] is not None:
            raise result_box["error"]
        return result_box["value"]

    def read_grams(self) -> float | None:
        if not self._hx:
            return None
        try:
            if hasattr(self._hx, "get_weight_mean"):
                val = self._safe_call("get_weight_mean", self.READ_TIMEOUT_SECONDS, 5)
                return None if val is None else float(val)
            if hasattr(self._hx, "get_raw_data_mean"):
                val = self._safe_call("get_raw_data_mean", self.READ_TIMEOUT_SECONDS, 5)
                return None if val is None else float(val)
            if hasattr(self._hx, "get_weight"):
                val = self._safe_call("get_weight", self.READ_TIMEOUT_SECONDS, 5)
                return None if val is None else float(val)
            return None
        except Exception as exc:
            logger.warning("HX711 read failed: %s", exc)
            return None

    def close(self) -> None:
        """Release HX711/GPIO resources so reruns do not require reboot."""
        try:
            if self._hx and hasattr(self._hx, "power_down"):
                self._hx.power_down()
        except Exception:
            pass
        try:
            if hasattr(HX711, "GPIO") and hasattr(HX711.GPIO, "cleanup"):
                HX711.GPIO.cleanup([self._dout_pin, self._sck_pin])
        except Exception:
            pass
        self._hx = None


# ── Barcode Scanner ──────────────────────────────────────────────────────────
class BarcodeScanner:
    def __init__(self):
        self._camera = None
        self._camera_error = False
        self._mock_stdin = not (_PICAMERA_AVAILABLE and _PYZBAR_AVAILABLE)

        if self._mock_stdin:
            logger.warning(
                "Camera scanner unavailable (picamera2=%s pyzbar=%s). Using stdin fallback.",
                _PICAMERA_AVAILABLE,
                _PYZBAR_AVAILABLE,
            )
            return

        try:
            self._camera = Picamera2()
            config = self._camera.create_preview_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
            self._camera.configure(config)
            self._camera.start()
            logger.info("Camera started for continuous barcode scanning")
        except Exception as exc:
            logger.error("Camera init failed: %s. Camera will show error state.", exc)
            self._camera = None
            self._mock_stdin = True
            self._camera_error = True

    def is_camera_ready(self) -> bool:
        """True if camera is operational, False otherwise."""
        return self._camera is not None and not self._camera_error

    def read_barcode(self) -> str | None:
        if self._mock_stdin:
            try:
                value = input("Scan barcode (or 'quit'): ").strip()
            except EOFError:
                return None
            if not value:
                return None
            if value.lower() == "quit":
                raise KeyboardInterrupt
            return value

        try:
            frame = self._camera.capture_array()
            decoded = zbar_decode(frame)
            if not decoded:
                return None
            return decoded[0].data.decode("utf-8").strip()
        except Exception as exc:
            logger.error("Camera capture error: %s", exc)
            self._camera_error = True
            return None

    def close(self) -> None:
        if self._camera:
            try:
                self._camera.stop()
            except Exception:
                pass
            try:
                self._camera.close()
            except Exception:
                pass
            self._camera = None


# ── TFT Display Driver ───────────────────────────────────────────────────────
def _load_font(size: int, bold: bool = True):
    """Return a font — falls back to default if font file missing."""
    if not _DISPLAY_AVAILABLE:
        return None
    try:
        path = _FONT_PATH if bold else _FONT_PATH_REG
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


class TFTDisplay:
    def __init__(self):
        self._device = None
        self._lock = threading.Lock()
        self._blink_state = False  # For camera status blink
        if not _DISPLAY_AVAILABLE:
            logger.warning("TFT display unavailable (luma.lcd/PIL not found)")
            return

        self._init_device()

    def _hardware_reset(self) -> None:
        """Pulse RST pin to recover panel state after abrupt stop/restart."""
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

    def _render(self, image: Image.Image) -> None:
        """Push PIL image to display (thread-safe)."""
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
        """Recreate TFT device to recover from stale display state."""
        self._device = None
        return self._init_device()

    def show_boot_splash(self) -> None:
        """High-contrast startup splash to confirm panel is alive."""
        if not self._device:
            return
        try:
            white = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), "white")
            self._render(white)
            time.sleep(0.15)

            img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), "black")
            draw = ImageDraw.Draw(img)
            draw.text((16, 34), "TFT READY", font=_load_font(20), fill=YELLOW)
            draw.text((28, 66), "SMART TROLLEY", font=_load_font(12, bold=False), fill=CYAN)
            self._render(img)
            time.sleep(0.35)
        except Exception as exc:
            logger.warning("TFT boot splash failed: %s", exc)

    def _blank(self, bg: str = BG) -> tuple:
        img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), bg)
        draw = ImageDraw.Draw(img)
        return img, draw

    def render_screen(self, image: Image.Image, screen_name: str) -> None:
        """Render a prepared PIL image to TFT with debug prints."""
        if image is None:
            print(f"[TFT DEBUG] render_screen({screen_name}) image is None")
            return
        print(f"[TFT DEBUG] before render_screen({screen_name})")
        print(f"[TFT DEBUG] image.size={image.size}")
        self._render(image)
        print(f"[TFT DEBUG] after render_screen({screen_name})")

    def _text_width(self, text: str, font) -> int:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]

    def _truncate(self, text: str, font, max_width: int) -> str:
        while len(text) > 0:
            if self._text_width(text, font) <= max_width:
                return text
            text = text[:-1]
        return ""

    def _draw_camera_indicator(self, draw, camera_ready: bool):
        """Draw camera status indicator in top-right corner."""
        x = TFT_WIDTH - 14
        y = 6
        r = 4
        if camera_ready:
            # Green blinking dot
            color = GREEN if self._blink_state else DK_GREEN
            draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=color, outline=GREEN)
        else:
            # Red solid dot + warning text
            draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=RED, outline=RED)
            draw.text((x - 38, y + 6), "No Cam", font=_load_font(8, bold=False), fill=RED)

    def _draw_cart_footer(self, draw, unique_items: int, total_qty: int, subtotal: float):
        """Draw persistent cart summary footer."""
        y = TFT_HEIGHT - 20
        draw.rectangle([(0, y), (TFT_WIDTH, TFT_HEIGHT)], fill="#111111")
        draw.text((4, y + 2), f"{unique_items} items", font=_load_font(9, bold=False), fill=LT_GREY)
        draw.text((60, y + 2), f"Qty:{total_qty}", font=_load_font(9, bold=False), fill=LT_GREY)
        draw.text((106, y + 1), f"₹{subtotal:.2f}", font=_load_font(11), fill=YELLOW)

    def toggle_blink(self):
        """Toggle blink state for camera indicator."""
        self._blink_state = not self._blink_state

    # ── Screen Compositions ──────────────────────────────────────────────────
    def compose_idle_screen(self, camera_ready: bool, unique_items: int, total_qty: int, subtotal: float) -> Image.Image:
        img, draw = self._blank()

        self._draw_camera_indicator(draw, camera_ready)
        draw.text((22, 24), "SMART", font=_load_font(22), fill=WHITE)
        draw.text((14, 50), "TROLLEY", font=_load_font(22), fill=YELLOW)
        draw.line([(0, 78), (TFT_WIDTH, 78)], fill=GREY, width=1)
        draw.text((14, 84), "Scan your items", font=_load_font(11, bold=False), fill=CYAN)

        if unique_items > 0:
            self._draw_cart_footer(draw, unique_items, total_qty, subtotal)
        else:
            draw.text((42, TFT_HEIGHT - 16), "• Ready •", font=_load_font(10), fill=GREEN)
        return img

    def compose_product_found_screen(
        self,
        camera_ready: bool,
        product_name: str,
        price: float,
        qty: int,
        weight: float | int | None,
        unique_items: int,
        total_qty: int,
        subtotal: float,
    ) -> Image.Image:
        img, draw = self._blank("#1a1a2e")

        self._draw_camera_indicator(draw, camera_ready)
        draw.text((6, 3), "✓ ADDED", font=_load_font(14), fill=GREEN)
        draw.line([(0, 22), (TFT_WIDTH, 22)], fill=GREY, width=1)

        short = (product_name[:18] + "…") if len(product_name) > 18 else product_name
        draw.text((6, 28), short, font=_load_font(13), fill=WHITE)
        draw.text((6, 46), f"₹{price:.2f}", font=_load_font(16), fill=YELLOW)
        draw.text((6, 66), f"Qty in cart: {qty}", font=_load_font(11, bold=False), fill=WHITE)

        weight_txt = "-" if weight is None else str(int(weight))
        draw.text((6, 82), f"Weight: {weight_txt}g", font=_load_font(10, bold=False), fill=WHITE)

        self._draw_cart_footer(draw, unique_items, total_qty, subtotal)
        return img

    def compose_not_found_screen(
        self,
        camera_ready: bool,
        barcode_string: str,
        unique_items: int,
        total_qty: int,
        subtotal: float,
    ) -> Image.Image:
        img, draw = self._blank("#1a1a2e")

        self._draw_camera_indicator(draw, camera_ready)
        draw.text((10, 26), "✗ NOT FOUND", font=_load_font(16), fill=RED)
        draw.text((6, 56), "Barcode:", font=_load_font(10, bold=False), fill=LT_GREY)
        short = self._truncate(barcode_string, _load_font(10, bold=False), TFT_WIDTH - 12)
        draw.text((6, 70), short, font=_load_font(10, bold=False), fill=WHITE)

        self._draw_cart_footer(draw, unique_items, total_qty, subtotal)
        return img

    def compose_qty_updated_screen(
        self,
        camera_ready: bool,
        product_name: str,
        new_qty: int,
        unique_items: int,
        total_qty: int,
        subtotal: float,
    ) -> Image.Image:
        img, draw = self._blank("#1a1a2e")

        self._draw_camera_indicator(draw, camera_ready)
        draw.text((6, 24), "QTY UPDATED", font=_load_font(15), fill=YELLOW)
        short = self._truncate(product_name, _load_font(12), TFT_WIDTH - 10)
        draw.text((6, 50), short, font=_load_font(12), fill=WHITE)
        draw.text((6, 70), f"x{new_qty} in cart", font=_load_font(16), fill=GREEN)

        self._draw_cart_footer(draw, unique_items, total_qty, subtotal)
        return img

    def compose_item_removed_screen(
        self,
        camera_ready: bool,
        product_name: str,
        new_qty: int,
        unique_items: int,
        total_qty: int,
        subtotal: float,
    ) -> Image.Image:
        img, draw = self._blank("#1a1a2e")

        self._draw_camera_indicator(draw, camera_ready)
        draw.text((6, 24), "REMOVED", font=_load_font(16), fill=RED)
        short = self._truncate(product_name, _load_font(12), TFT_WIDTH - 10)
        draw.text((6, 50), short, font=_load_font(12), fill=WHITE)
        if new_qty > 0:
            draw.text((6, 70), f"x{new_qty} remains", font=_load_font(12, bold=False), fill=CYAN)

        self._draw_cart_footer(draw, unique_items, total_qty, subtotal)
        return img

    def show_idle(self, camera_ready: bool, unique_items: int, total_qty: int, subtotal: float):
        """STAGE 1: Idle / waiting for scan."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT IDLE] items=%d qty=%d total=₹%.2f", unique_items, total_qty, subtotal)
            return
        try:
            img = self.compose_idle_screen(camera_ready, unique_items, total_qty, subtotal)
            print("[TFT DEBUG] about to render idle")
            print(f"[TFT DEBUG] idle image size={img.size}")
            self.render_screen(img, "idle")
            print("[TFT DEBUG] idle rendered")
        except Exception as exc:
            logger.error("TFT show_idle: %s", exc)

    def show_product_added(
        self,
        camera_ready: bool,
        name: str,
        price: float,
        qty: int,
        unique_items: int,
        total_qty: int,
        subtotal: float,
        weight: float | int | None = None,
    ):
        """STAGE 2: Product found and added."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT+] %s ₹%.2f×%d", name, price, qty)
            return
        try:
            img = self.compose_product_found_screen(
                camera_ready,
                name,
                price,
                qty,
                weight,
                unique_items,
                total_qty,
                subtotal,
            )
            print("[TFT DEBUG] about to render product_found")
            print(f"[TFT DEBUG] product_found image size={img.size}")
            self.render_screen(img, "product_found")
            print("[TFT DEBUG] product_found rendered")
        except Exception as exc:
            logger.error("TFT show_product_added: %s", exc)

    def show_qty_updated(
        self,
        camera_ready: bool,
        name: str,
        new_qty: int,
        unique_items: int,
        total_qty: int,
        subtotal: float,
    ) -> None:
        """STAGE 2b: Duplicate barcode scanned, quantity increased."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT QTY] %s x%d", name, new_qty)
            return
        try:
            img = self.compose_qty_updated_screen(
                camera_ready,
                name,
                new_qty,
                unique_items,
                total_qty,
                subtotal,
            )
            print("[TFT DEBUG] about to render qty_updated")
            print(f"[TFT DEBUG] qty_updated image size={img.size}")
            self.render_screen(img, "qty_updated")
            print("[TFT DEBUG] qty_updated rendered")
        except Exception as exc:
            logger.error("TFT show_qty_updated: %s", exc)

    def show_product_not_found(self, camera_ready: bool, barcode: str,
                               unique_items: int, total_qty: int, subtotal: float):
        """STAGE 3: Product not found."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT MISS] barcode=%s", barcode)
            return
        try:
            img = self.compose_not_found_screen(
                camera_ready,
                barcode,
                unique_items,
                total_qty,
                subtotal,
            )
            print("[TFT DEBUG] about to render not_found")
            print(f"[TFT DEBUG] not_found image size={img.size}")
            self.render_screen(img, "not_found")
            print("[TFT DEBUG] not_found rendered")
        except Exception as exc:
            logger.error("TFT show_product_not_found: %s", exc)

    def show_item_removed(self, camera_ready: bool, name: str, new_qty: int,
                         unique_items: int, total_qty: int, subtotal: float):
        """STAGE 4: Item quantity decreased or removed."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT-] %s new_qty=%d", name, new_qty)
            return
        try:
            img = self.compose_item_removed_screen(
                camera_ready,
                name,
                new_qty,
                unique_items,
                total_qty,
                subtotal,
            )
            print("[TFT DEBUG] about to render item_removed")
            print(f"[TFT DEBUG] item_removed image size={img.size}")
            self.render_screen(img, "item_removed")
            print("[TFT DEBUG] item_removed rendered")
        except Exception as exc:
            logger.error("TFT show_item_removed: %s", exc)

    def show_cart_summary(self, camera_ready: bool, cart: SessionCart):
        """Show full cart summary before checkout."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT CART] %d items total=₹%.2f", cart.unique_item_count, cart.subtotal)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Header
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill="#004d5e")
            draw.text((6, 3), f"CART ({cart.unique_item_count} items)", font=_load_font(12), fill=CYAN)

            # Items (show up to 3)
            y = 24
            row_h = 18
            for i, item in enumerate(list(cart.items.values())[:3]):
                short = self._truncate(item.name, _load_font(10, bold=False), 90)
                draw.text((4, y), short, font=_load_font(10, bold=False), fill=WHITE)
                right = f"{item.quantity}×₹{item.price:.0f}"
                rw = self._text_width(right, _load_font(10))
                draw.text((TFT_WIDTH - rw - 4, y), right, font=_load_font(10), fill=CYAN)
                y += row_h

            if cart.unique_item_count > 3:
                draw.text((4, y), f"+ {cart.unique_item_count - 3} more...",
                         font=_load_font(10, bold=False), fill=ORANGE)

            # Footer
            self._draw_cart_footer(draw, cart.unique_item_count, cart.total_quantity, cart.subtotal)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_cart_summary: %s", exc)

    def show_payment_qr(self, camera_ready: bool, total: float, qr_image: Image.Image):
        """STAGE 6: Show UPI QR code for payment."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT QR] total=₹%.2f", total)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Header
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill="#4a3a00")
            draw.text((6, 3), "SCAN TO PAY", font=_load_font(13), fill=YELLOW)

            # QR code (centered, 90x90)
            qr_size = 90
            qr_resized = qr_image.convert("RGB").resize((qr_size, qr_size), Image.NEAREST)
            qr_x = (TFT_WIDTH - qr_size) // 2
            img.paste(qr_resized, (qr_x, 22))

            # Amount below QR
            amount_str = f"₹{total:.2f}"
            aw = self._text_width(amount_str, _load_font(14))
            draw.text(((TFT_WIDTH - aw) // 2, TFT_HEIGHT - 14), amount_str,
                     font=_load_font(14), fill=WHITE)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_payment_qr: %s", exc)

    def show_payment_success(self, camera_ready: bool, total: float, receipt_id: str):
        """STAGE 7: Payment confirmed."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT ✓] Payment ₹%.2f receipt=%s", total, receipt_id)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Header
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill=DK_GREEN)
            draw.text((6, 3), "PAYMENT SUCCESS", font=_load_font(12), fill=GREEN)

            # Big checkmark
            draw.text((10, 26), "✓", font=_load_font(40), fill=GREEN)
            draw.text((70, 32), "PAID", font=_load_font(12), fill=WHITE)
            draw.text((70, 50), f"₹{total:.2f}", font=_load_font(14), fill=WHITE)

            draw.line([(0, 76), (TFT_WIDTH, 76)], fill=GREY, width=1)
            draw.text((20, 82), "Thank you!", font=_load_font(16), fill=YELLOW)

            # Receipt ID
            short_id = self._truncate(receipt_id, _load_font(8, bold=False), TFT_WIDTH - 10)
            draw.text((6, TFT_HEIGHT - 12), short_id, font=_load_font(8, bold=False), fill=GREY)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_payment_success: %s", exc)

    def close(self) -> None:
        """Release display resources cleanly for repeat launches."""
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


# ── Receipt Printer ──────────────────────────────────────────────────────────
class ReceiptPrinter:
    def __init__(self, mac_address: str):
        self._printer = None
        if not mac_address:
            logger.warning("Bluetooth printer MAC not configured; skipping printer init")
            return
        if not _BLUETOOTH_PRINTER_AVAILABLE:
            logger.warning("python-escpos not found; receipt printing disabled")
            return
        try:
            self._printer = BTWPrinter(mac_address)
            logger.info("Bluetooth printer initialized: %s", mac_address)
        except Exception as exc:
            logger.error("Printer init failed: %s", exc)
            self._printer = None

    def print_receipt(self, session_id: str, cart: SessionCart, total: float, payment_ref: str):
        """Print a receipt for the transaction."""
        if not self._printer:
            logger.warning("No printer available; skipping receipt for %s", session_id)
            return
        try:
            self._printer.text(f"{SHOP_NAME}\n")
            self._printer.text("=" * 32 + "\n")
            self._printer.text(f"Receipt: {session_id}\n")
            self._printer.text(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._printer.text("=" * 32 + "\n\n")

            for item in cart.items.values():
                self._printer.text(f"{item.name[:20]:<20}\n")
                self._printer.text(f"  {item.quantity}x ₹{item.price:.2f} = ₹{item.line_total():.2f}\n")

            self._printer.text("\n" + "-" * 32 + "\n")
            self._printer.text(f"{'Subtotal':<20} ₹{cart.subtotal:.2f}\n")
            tax = cart.subtotal * 0.18
            self._printer.text(f"{'GST @18%':<20} ₹{tax:.2f}\n")
            self._printer.text(f"{'TOTAL':<20} ₹{total:.2f}\n")
            self._printer.text("=" * 32 + "\n")
            self._printer.text(f"Payment: {payment_ref}\n")
            self._printer.text("\nThank you for shopping!\n\n")
            self._printer.cut()
            logger.info("Receipt printed for %s", session_id)
        except Exception as exc:
            logger.error("Receipt print failed: %s", exc)

    def close(self) -> None:
        if not self._printer:
            return
        try:
            if hasattr(self._printer, "close"):
                self._printer.close()
        except Exception:
            pass
        self._printer = None


class SingleInstanceLock:
    """Prevent multiple runtime instances from colliding on camera/SPI/GPIO."""

    def __init__(self, lock_path: str = "/tmp/smart_trolley_main.lock"):
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


def _is_main_runtime_process(pid: int) -> bool:
    """Best-effort check to avoid killing unrelated processes."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="ignore").replace("\x00", " ")
        return "python" in cmdline and "main.py" in cmdline and "Trolly_system" in cmdline
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


# ── Terminal Control Thread ─────────────────────────────────────────────────
class TerminalController:
    """Background thread that listens for terminal commands (placeholder for GPIO button)."""

    def __init__(self, command_queue: queue.Queue):
        self._queue = command_queue
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._running = False

    def start(self):
        print("\n" + "=" * 60)
        print("Terminal controls (placeholder for GPIO button):")
        print("  - Type 'done' to trigger checkout")
        print("  - Type 'clear' to reset cart")
        print("  - Type 'quit' to exit")
        print("=" * 60 + "\n")
        self._running = True
        self._thread.start()

    def _run(self):
        while self._running:
            try:
                # Use a timeout to avoid blocking forever and allow graceful shutdown
                import select
                import sys

                # Check if there's input available (non-blocking)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    line = sys.stdin.readline().strip().lower()
                    if line in ("done", "clear", "quit"):
                        self._queue.put(line)
                else:
                    time.sleep(0.1)  # Small sleep to prevent busy loop
            except (EOFError, KeyboardInterrupt):
                break
            except Exception:
                time.sleep(0.1)

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUNTIME
# ══════════════════════════════════════════════════════════════════════════════
def main() -> int:
    if not _PSYCOPG2_AVAILABLE:
        logger.error("Missing psycopg2-binary. Install requirements before running.")
        return 1
    if not DATABASE_URL:
        logger.error("DATABASE_URL missing. Set it in .env before running.")
        return 1

    lock = SingleInstanceLock()
    if not lock.acquire():
        holder = lock.holder_pid()
        if holder and SMART_TROLLEY_AUTO_TAKEOVER and holder != os.getpid() and _is_main_runtime_process(holder):
            logger.warning("Detected existing runtime pid=%d, attempting safe takeover", holder)
            if _terminate_process(holder):
                logger.info("Previous runtime pid=%d stopped, retrying lock", holder)
                if not lock.acquire():
                    logger.error("Could not acquire lock after takeover attempt")
                    return 1
            else:
                logger.error("Could not stop existing runtime pid=%d", holder)
                return 1
        else:
            if holder:
                logger.error(
                    "Another main.py instance is already running (pid=%d). "
                    "Stop it first: pgrep -af \"python.*main.py\" && kill %d",
                    holder,
                    holder,
                )
            else:
                logger.error(
                    "Another main.py instance is already running. "
                    "Stop it first: pgrep -af \"python.*main.py\""
                )
            return 1

    # Initialize hardware
    try:
        display = TFTDisplay()
        display.show_boot_splash()
        cart = SessionCart()
        scanner = BarcodeScanner()
        weights = WeightReader(HX711_DOUT_PIN, HX711_SCK_PIN)
        printer = ReceiptPrinter(BLUETOOTH_PRINTER_MAC)
    except KeyboardInterrupt:
        logger.info("Startup interrupted by user")
        lock.release()
        return 130

    # Terminal control thread (only when camera uses stdin fallback)
    # When camera works, commands should be handled via special barcodes
    terminal_queue = queue.Queue()
    terminal_ctrl = None
    camera_ready = scanner.is_camera_ready()

    if not camera_ready:
        # Camera not working, use stdin for barcode input with terminal commands
        print("\n" + "=" * 60)
        print("Camera unavailable - using stdin for barcode input")
        print("Special commands: 'done', 'clear', 'quit'")
        print("=" * 60 + "\n")
    else:
        # Camera working, no terminal controller needed
        print("\n" + "=" * 60)
        print("Camera ready - scanning barcodes with camera")
        print("To checkout: scan barcode 'done' or press Ctrl+C")
        print("To clear cart: scan barcode 'clear'")
        print("=" * 60 + "\n")

    running = True
    last_barcode = ""
    last_seen_at = 0.0
    checkout_triggered = False
    blink_timer = time.time()
    tft_reinit_at = time.monotonic()

    def _handle_stop(_sig=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    # Show initial idle screen
    camera_ready = scanner.is_camera_ready()
    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
    logger.info("Pi runtime ready. Waiting for barcode scans...")

    # Main event loop
    try:
        while running:
            try:
            # Camera indicator blink (every 1 second)
                if time.time() - blink_timer > 1.0:
                    display.toggle_blink()
                    camera_ready = scanner.is_camera_ready()
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    blink_timer = time.time()

                now_mono = time.monotonic()
                if TFT_REINIT_SECONDS > 0 and (now_mono - tft_reinit_at) > TFT_REINIT_SECONDS:
                    logger.info("Periodic TFT re-init to recover stale panel state")
                    if display.force_reinit():
                        camera_ready = scanner.is_camera_ready()
                        display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    tft_reinit_at = now_mono

            # Terminal controller not used when camera is working
            # Commands are handled through barcode scans ('done', 'clear', 'quit')

            # Barcode scan
                barcode = scanner.read_barcode()
                if not barcode:
                    if scanner._mock_stdin:
                        time.sleep(0.2)
                    continue

            # Debounce
                now = time.monotonic()
                if barcode == last_barcode and (now - last_seen_at) < SCAN_DEBOUNCE_SECONDS:
                    continue
                last_barcode = barcode
                last_seen_at = now

                camera_ready = scanner.is_camera_ready()

            # Handle special commands (when using stdin fallback)
                if barcode.lower() in ("quit", "done", "clear"):
                    if barcode.lower() == "quit":
                        logger.info("Quit command received")
                        running = False
                        break
                    elif barcode.lower() == "clear":
                        logger.info("Clear cart command")
                        cart.clear()
                        display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                        print("Cart cleared.")
                    elif barcode.lower() == "done":
                        if cart.unique_item_count == 0:
                            print("Cart is empty. Add items before checkout.")
                        else:
                            logger.info("Checkout triggered")
                            checkout_triggered = True
                    continue

            # Handle special barcodes
                if barcode == BARCODE_REMOVE_LAST:
                    result = cart.decrement_last()
                    if result:
                        name, new_qty = result
                        logger.info("Decremented %s → qty=%d", name, new_qty)
                        display.show_item_removed(camera_ready, name, new_qty,
                                                 cart.unique_item_count, cart.total_quantity, cart.subtotal)
                        time.sleep(2.0)
                    else:
                        logger.info("No item to decrement")
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    continue

                if barcode == BARCODE_CMD_CLEAR:
                    name = cart.remove_last()
                    if name:
                        logger.info("Removed all of: %s", name)
                        display.show_item_removed(camera_ready, name, 0,
                                                 cart.unique_item_count, cart.total_quantity, cart.subtotal)
                        time.sleep(2.0)
                    else:
                        logger.info("No item to remove")
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    continue

            # Regular product lookup
                t0 = time.monotonic()
                product = get_product_by_barcode(barcode)
                lookup_ms = (time.monotonic() - t0) * 1000.0

                if not product:
                    logger.info("Barcode=%s not found (lookup %.0f ms)", barcode, lookup_ms)
                    display.show_product_not_found(camera_ready, barcode,
                                                   cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    time.sleep(2.5)
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    continue

            # Add to cart
                was_already_in_cart = barcode in cart.items
                name, qty = cart.add(product)
                measured_weight = weights.read_grams()
                expected_weight = product.get("weight_grams")

                logger.info(
                    "Scan OK barcode=%s name=%s qty=%d lookup=%.0fms exp_g=%s meas_g=%s items=%d total_qty=%d subtotal=₹%.2f",
                    barcode,
                    name,
                    qty,
                    lookup_ms,
                    expected_weight,
                    measured_weight,
                    cart.unique_item_count,
                    cart.total_quantity,
                    cart.subtotal,
                )

                if was_already_in_cart:
                    display.show_qty_updated(
                        camera_ready,
                        name,
                        qty,
                        cart.unique_item_count,
                        cart.total_quantity,
                        cart.subtotal,
                    )
                    time.sleep(2.0)
                else:
                    display.show_product_added(
                        camera_ready,
                        name,
                        float(product["price"]),
                        qty,
                        cart.unique_item_count,
                        cart.total_quantity,
                        cart.subtotal,
                        expected_weight,
                    )
                    time.sleep(2.5)

                display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)

            # ── CHECKOUT FLOW ────────────────────────────────────────────────
                if checkout_triggered and cart.unique_item_count > 0:
                    checkout_triggered = False
                    logger.info("Starting checkout for %d items", cart.unique_item_count)

                    # Show cart summary
                    display.show_cart_summary(camera_ready, cart)
                    time.sleep(3.0)

                    # Generate UPI QR
                    subtotal = cart.subtotal
                    gst = subtotal * 0.18
                    final_total = subtotal + gst

                    session_id = f"SESSION_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
                    upi_string = f"upi://pay?pa={UPI_ID}&pn={SHOP_NAME}&am={final_total:.2f}&cu=INR&tn={session_id}"

                    qr = qrcode.QRCode(version=1, box_size=10, border=2)
                    qr.add_data(upi_string)
                    qr.make(fit=True)
                    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

                    display.show_payment_qr(camera_ready, final_total, qr_img)
                    logger.info("QR code displayed for ₹%.2f", final_total)

                    # Wait for payment (simulated - in real system, wait for payment confirmation)
                    print(f"\n{'='*60}")
                    print(f"CHECKOUT: {cart.unique_item_count} items, ₹{final_total:.2f}")
                    print("Scan QR code to complete payment (or press Enter to simulate payment)")
                    print("="*60)
                    input()

                    # Payment confirmed
                    payment_ref = f"UPI_{uuid.uuid4().hex[:8]}"
                    tx_id = save_transaction(
                        session_id=session_id,
                        items=cart.to_list(),
                        total=final_total,
                        status="paid",
                        payment_method="UPI/QR",
                        upi_ref=payment_ref,
                    )
                    logger.info("Transaction saved: id=%d session=%s", tx_id, session_id)

                    display.show_payment_success(camera_ready, final_total, session_id)

                    # Print receipt
                    printer.print_receipt(session_id, cart, final_total, payment_ref)

                    time.sleep(5.0)

                    # Reset cart
                    cart.clear()
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    logger.info("Checkout complete. Ready for next customer.")

            except KeyboardInterrupt:
                running = False
            except Exception as exc:
                logger.exception("Runtime error: %s", exc)
                try:
                    time.sleep(1.0)
                except Exception:
                    pass
    finally:
        # Cleanup
        try:
            scanner.close()
        except Exception:
            pass
        if terminal_ctrl:
            terminal_ctrl.stop()
        try:
            weights.close()
        except Exception:
            pass
        try:
            camera_ready = scanner.is_camera_ready()
            display.show_idle(camera_ready, 0, 0, 0.0)
        except Exception:
            pass
        try:
            display.close()
        except Exception:
            pass
        try:
            printer.close()
        except Exception:
            pass
        lock.release()

    logger.info("Pi runtime stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
