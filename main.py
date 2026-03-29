#!/usr/bin/env python3
"""
Smart Trolley Pi Runtime — Complete Implementation
Integrates: camera barcode scanning, HX711 weight, TFT display,
            quantity management, checkout flow, and receipt printing.

Terminal controls (placeholder for GPIO button):
    - Type 'done' to trigger checkout
    - Type 'skip' to simulate payment success while payment QR is shown
  - Type 'clear' to reset cart
"""

from __future__ import annotations

import os
import sys
import time
import socket
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
    _DISPLAY_AVAILABLE = True
except ImportError:
    _DISPLAY_AVAILABLE = False

try:
    from escpos.printer import Bluetooth as BTWPrinter
    _BLUETOOTH_PRINTER_AVAILABLE = True
except ImportError:
    BTWPrinter = None
    _BLUETOOTH_PRINTER_AVAILABLE = False

try:
    import qrcode
    _QRCODE_AVAILABLE = True
except Exception:
    qrcode = None
    _QRCODE_AVAILABLE = False

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s — %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

try:
    from payment import (
        create_razorpay_order,
        generate_payment_qr,
        download_qr_image,
        poll_payment_status,
    )
    _PAYMENT_MODULE_AVAILABLE = True
    _PAYMENT_IMPORT_ERROR = None
except Exception as exc:
    _PAYMENT_MODULE_AVAILABLE = False
    _PAYMENT_IMPORT_ERROR = exc
    _PAYMENT_IMPORT_ERROR_MSG = str(exc)

    def create_razorpay_order(*_args, **_kwargs):
        raise RuntimeError(f"Payment module unavailable: {_PAYMENT_IMPORT_ERROR_MSG}")

    def generate_payment_qr(*_args, **_kwargs):
        raise RuntimeError(f"Payment module unavailable: {_PAYMENT_IMPORT_ERROR_MSG}")

    def download_qr_image(*_args, **_kwargs):
        raise RuntimeError(f"Payment module unavailable: {_PAYMENT_IMPORT_ERROR_MSG}")

    def poll_payment_status(*_args, **_kwargs):
        raise RuntimeError(f"Payment module unavailable: {_PAYMENT_IMPORT_ERROR_MSG}")

# ── Configuration ────────────────────────────────────────────────────────────
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
UPI_ID = os.getenv("UPI_ID", "yourshop@upi")
SHOP_NAME = os.getenv("SHOP_NAME", "Smart Trolley Shop")
BLUETOOTH_PRINTER_MAC = os.getenv("BLUETOOTH_PRINTER_MAC", "").strip()
BT_PRINTER_CHANNEL = int(os.getenv("BT_PRINTER_CHANNEL", "1"))
BT_PRINTER_ROW_DELAY_SECONDS = float(os.getenv("BT_PRINTER_ROW_DELAY_SECONDS", "0.005"))
BT_PRINTER_WIDTH = int(os.getenv("BT_PRINTER_WIDTH", "384"))
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "5"))

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
SCANNER_BUTTON_ENABLED = os.getenv("SCANNER_BUTTON_ENABLED", "1").strip().lower() in {
    "1", "true", "yes"
}
SCANNER_BUTTON_PIN_MODE = os.getenv("SCANNER_BUTTON_PIN_MODE", "board").strip().lower()
SCANNER_BUTTON_PIN = int(os.getenv("SCANNER_BUTTON_PIN", "11"))
SCANNER_BUTTON_BOUNCETIME_MS = int(os.getenv("SCANNER_BUTTON_BOUNCETIME_MS", "250"))

# Checkout button aliases; defaults preserve existing scanner button wiring.
CHECKOUT_BUTTON_ENABLED = os.getenv(
    "CHECKOUT_BUTTON_ENABLED",
    "1" if SCANNER_BUTTON_ENABLED else "0",
).strip().lower() in {"1", "true", "yes"}
CHECKOUT_BUTTON_PIN_MODE = os.getenv("CHECKOUT_BUTTON_PIN_MODE", SCANNER_BUTTON_PIN_MODE).strip().lower()
CHECKOUT_BUTTON_PIN = int(os.getenv("CHECKOUT_BUTTON_PIN", str(SCANNER_BUTTON_PIN)))
CHECKOUT_BUTTON_BOUNCETIME_MS = int(
    os.getenv("CHECKOUT_BUTTON_BOUNCETIME_MS", str(SCANNER_BUTTON_BOUNCETIME_MS))
)

STATE_IDLE = "idle"
STATE_SCANNING = "scanning"
STATE_PAYMENT = "payment"
STATE_SUCCESS = "success"
app_state = STATE_IDLE

BOARD_TO_BCM_PIN = {
    3: 2,
    5: 3,
    7: 4,
    8: 14,
    10: 15,
    11: 17,
    12: 18,
    13: 27,
    15: 22,
    16: 23,
    18: 24,
    19: 10,
    21: 9,
    22: 25,
    23: 11,
    24: 8,
    26: 7,
    27: 0,
    28: 1,
    29: 5,
    31: 6,
    32: 12,
    33: 13,
    35: 19,
    36: 16,
    37: 26,
    38: 20,
    40: 21,
}
BCM_TO_BOARD_PIN = {v: k for k, v in BOARD_TO_BCM_PIN.items()}

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
                SELECT barcode, name, price, category, weight_grams, stock
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
                     upi_ref: str = "", razorpay_order_id: str | None = None,
                     razorpay_qr_id: str | None = None) -> int:
    """Insert a completed transaction. Returns the new row id."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions
                    (
                        session_id,
                        items,
                        total_amount,
                        payment_status,
                        payment_method,
                        upi_ref,
                        razorpay_order_id,
                        razorpay_qr_id
                    )
                VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    session_id,
                    json.dumps(items),
                    total,
                    status,
                    payment_method,
                    upi_ref,
                    razorpay_order_id,
                    razorpay_qr_id,
                )
            )
            conn.commit()
            return cur.fetchone()["id"]


def ensure_transaction_payment_columns() -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS razorpay_order_id TEXT")
            cur.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS razorpay_qr_id TEXT")
        conn.commit()


def mark_transaction_paid(
    session_id: str,
    payment_id: str,
    razorpay_order_id: str,
    razorpay_qr_id: str,
    items: list,
    total: float,
) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE transactions
                SET payment_status = 'paid',
                    upi_ref = %s,
                    razorpay_order_id = %s,
                    razorpay_qr_id = %s,
                    items = %s::jsonb,
                    total_amount = %s,
                    payment_method = 'UPI'
                WHERE session_id = %s
                RETURNING id
                """,
                (
                    payment_id,
                    razorpay_order_id,
                    razorpay_qr_id,
                    json.dumps(items),
                    total,
                    session_id,
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return int(row["id"])

    return save_transaction(
        session_id=session_id,
        items=items,
        total=total,
        status="paid",
        payment_method="Razorpay UPI",
        upi_ref=payment_id,
        razorpay_order_id=razorpay_order_id,
        razorpay_qr_id=razorpay_qr_id,
    )


def decrement_stock(cart_items: list[dict]) -> None:
    """Atomically decrement stock for purchased items; rollback on any failure."""
    if not cart_items:
        return

    with get_db() as conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    for item in cart_items:
                        barcode = str(item.get("id") or item.get("barcode") or "").strip()
                        name = str(item.get("name") or barcode)
                        qty = int(item.get("quantity") or item.get("qty") or 0)
                        if not barcode or qty <= 0:
                            continue

                        cur.execute(
                            """
                            UPDATE products
                            SET stock = stock - %s,
                                updated_at = NOW()
                            WHERE barcode = %s
                              AND stock >= %s
                            """,
                            (qty, barcode, qty),
                        )
                        if cur.rowcount == 0:
                            raise RuntimeError(
                                f"Stock update failed for '{name}' (barcode: {barcode}) - insufficient stock or not found"
                            )
                        logger.info("[STOCK] Decremented: '%s' -%d units", name, qty)
            logger.info("[STOCK] All %d items decremented successfully", len(cart_items))
        except Exception as exc:
            logger.error("[STOCK] Decrement transaction rolled back: %s", exc)
            raise


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
        self._last_barcode_type = "unknown"
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

    def last_barcode_type(self) -> str:
        return self._last_barcode_type

    def read_barcode(self) -> str | None:
        if self._mock_stdin:
            try:
                value = input().strip()
            except EOFError:
                return None
            if not value:
                return None
            if value.lower() == "quit":
                raise KeyboardInterrupt
            self._last_barcode_type = "stdin"
            return value

        try:
            frame = self._camera.capture_array()
            decoded = zbar_decode(frame)
            if not decoded:
                return None
            self._last_barcode_type = str(getattr(decoded[0], "type", "unknown"))
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
        """Render a prepared PIL image to TFT."""
        if image is None:
            logger.warning("Skipping render for %s: image is None", screen_name)
            return
        logger.debug("[TFT] Rendering screen: %s", screen_name)
        try:
            self._render(image)
        except Exception as exc:
            logger.error("[TFT] Render failed: %s", exc)

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
        stock_level: int | None,
        unique_items: int,
        total_qty: int,
        subtotal: float,
    ) -> Image.Image:
        img, draw = self._blank("#1a1a2e")

        self._draw_camera_indicator(draw, camera_ready)
        if stock_level is not None and stock_level <= 0:
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill=DK_RED)
            draw.text((6, 3), "OUT OF STOCK", font=_load_font(12), fill=WHITE)
        elif stock_level is not None and stock_level <= LOW_STOCK_THRESHOLD:
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill="#594100")
            draw.text((6, 3), f"LOW STOCK: {stock_level}", font=_load_font(12), fill=YELLOW)
        else:
            draw.text((6, 3), "ADDED", font=_load_font(14), fill=GREEN)
        draw.line([(0, 22), (TFT_WIDTH, 22)], fill=GREY, width=1)

        short = (product_name[:18] + "…") if len(product_name) > 18 else product_name
        draw.text((6, 28), short, font=_load_font(13), fill=WHITE)
        draw.text((6, 46), f"₹{price:.2f}", font=_load_font(16), fill=YELLOW)
        draw.text((6, 66), f"Qty in cart: {qty}", font=_load_font(11, bold=False), fill=WHITE)

        if stock_level is None:
            stock_text = "Stock: -"
            stock_fill = LT_GREY
        elif stock_level <= 0:
            stock_text = "OUT OF STOCK"
            stock_fill = RED
        elif stock_level <= LOW_STOCK_THRESHOLD:
            stock_text = f"Only {stock_level} left"
            stock_fill = YELLOW
        else:
            stock_text = f"Stock: {stock_level}"
            stock_fill = LT_GREY

        draw.text((6, 82), stock_text, font=_load_font(10, bold=False), fill=stock_fill)

        weight_txt = "-" if weight is None else str(int(weight))
        draw.text((6, 94), f"Weight: {weight_txt}g", font=_load_font(9, bold=False), fill=WHITE)

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
            self.render_screen(img, "idle")
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
        stock_level: int | None = None,
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
                stock_level,
                unique_items,
                total_qty,
                subtotal,
            )
            self.render_screen(img, "product_found")
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
            self.render_screen(img, "qty_updated")
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
            self.render_screen(img, "not_found")
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
            self.render_screen(img, "item_removed")
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

    def compose_payment_connecting_screen(self, camera_ready: bool, total: float) -> Image.Image:
        img, draw = self._blank("#0b1320")
        draw.text((20, 26), "CONNECTING", font=_load_font(16), fill=WHITE)
        draw.text((20, 48), "TO PAYMENT...", font=_load_font(14), fill=CYAN)
        draw.text((20, 76), f"Amount: ₹{total:.2f}", font=_load_font(12), fill=YELLOW)
        draw.text((20, 96), "Please wait", font=_load_font(10, bold=False), fill=LT_GREY)
        self._draw_camera_indicator(draw, camera_ready)
        return img

    def compose_payment_unavailable_screen(self, camera_ready: bool, detail: str | None = None) -> Image.Image:
        img, draw = self._blank("#240a0a")
        draw.text((14, 24), "PAYMENT", font=_load_font(16), fill=RED)
        draw.text((14, 44), "UNAVAILABLE", font=_load_font(16), fill=RED)
        draw.text((14, 72), "Use button skip", font=_load_font(11, bold=False), fill=WHITE)
        draw.text((14, 86), "to test checkout", font=_load_font(11, bold=False), fill=WHITE)
        if detail:
            short = self._truncate(detail, _load_font(8, bold=False), TFT_WIDTH - 8)
            draw.text((4, TFT_HEIGHT - 12), short, font=_load_font(8, bold=False), fill=LT_GREY)
        self._draw_camera_indicator(draw, camera_ready)
        return img

    def compose_cart_empty_screen(self, camera_ready: bool) -> Image.Image:
        img, draw = self._blank("#1f1212")
        draw.text((24, 38), "CART IS", font=_load_font(18), fill=RED)
        draw.text((24, 62), "EMPTY", font=_load_font(20), fill=WHITE)
        draw.text((22, 92), "Scan items first", font=_load_font(10, bold=False), fill=LT_GREY)
        self._draw_camera_indicator(draw, camera_ready)
        return img

    def compose_payment_timeout_screen(self, camera_ready: bool) -> Image.Image:
        img, draw = self._blank("#220f0f")
        draw.text((26, 36), "QR EXPIRED", font=_load_font(17), fill=RED)
        draw.text((12, 70), "Press button to retry", font=_load_font(11, bold=False), fill=WHITE)
        self._draw_camera_indicator(draw, camera_ready)
        return img

    def compose_payment_screen(
        self,
        camera_ready: bool,
        total: float,
        qr_image: Image.Image | None,
        footer_text: str = "Btn=skip(test)",
        footer_fill: str = GREY,
        order_id: str | None = None,
    ) -> Image.Image:
        img, draw = self._blank("#101114")
        title_font = _load_font(14)
        amount_font = _load_font(16)
        brand_font = _load_font(9, bold=False)
        footer_font = _load_font(8, bold=False)

        title = "SCAN TO PAY"
        tw = self._text_width(title, title_font)
        draw.text(((TFT_WIDTH - tw) // 2, 5), title, font=title_font, fill=WHITE)

        if qr_image is not None:
            qr_size = min(100, max(64, TFT_HEIGHT - 46))
            qr_render = qr_image.convert("RGB").resize((qr_size, qr_size), Image.LANCZOS)
            qr_x = (TFT_WIDTH - qr_size) // 2
            qr_y = 22
            img.paste(qr_render, (qr_x, qr_y))
        else:
            draw.rectangle([(20, 22), (TFT_WIDTH - 20, 92)], outline=GREY, width=1)
            draw.text((34, 45), "QR IMAGE", font=_load_font(12), fill=LT_GREY)
            draw.text((34, 61), "UNAVAILABLE", font=_load_font(12), fill=LT_GREY)
            if order_id:
                short = self._truncate(order_id, _load_font(8, bold=False), TFT_WIDTH - 16)
                draw.text((8, 78), short, font=_load_font(8, bold=False), fill=CYAN)

        amount = f"₹{total:.2f}"
        aw = self._text_width(amount, amount_font)
        draw.rectangle([(0, TFT_HEIGHT - 27), (TFT_WIDTH, TFT_HEIGHT)], fill="#151515")
        draw.text(((TFT_WIDTH - aw) // 2, TFT_HEIGHT - 27), amount, font=amount_font, fill=YELLOW)

        brand = "Powered by Razorpay"
        bw = self._text_width(brand, brand_font)
        draw.text(((TFT_WIDTH - bw) // 2, TFT_HEIGHT - 12), brand, font=brand_font, fill=LT_GREY)

        if footer_text:
            fw = self._text_width(footer_text, footer_font)
            draw.text(((TFT_WIDTH - fw) // 2, TFT_HEIGHT - 22), footer_text, font=footer_font, fill=footer_fill)

        self._draw_camera_indicator(draw, camera_ready)
        return img

    def compose_payment_success_screen(self, camera_ready: bool, total: float, payment_id: str) -> Image.Image:
        img, draw = self._blank("#0d2b0d")
        draw.text((10, 24), "✓ PAYMENT", font=_load_font(18), fill="#5dff86")
        draw.text((10, 46), "RECEIVED", font=_load_font(18), fill="#5dff86")
        draw.text((30, 72), f"₹{total:.2f}", font=_load_font(18), fill=WHITE)
        draw.text((40, 94), "Thank you!", font=_load_font(14), fill=YELLOW)
        suffix = payment_id[-8:] if payment_id else ""
        if suffix:
            draw.text((42, 112), suffix, font=_load_font(9, bold=False), fill=LT_GREY)
        self._draw_camera_indicator(draw, camera_ready)
        return img

    def show_cart_empty(self, camera_ready: bool) -> None:
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT] Cart is empty")
            return
        try:
            self.render_screen(self.compose_cart_empty_screen(camera_ready), "cart_empty")
        except Exception as exc:
            logger.error("TFT show_cart_empty: %s", exc)

    def show_payment_connecting(self, camera_ready: bool, total: float) -> None:
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT] Connecting payment for ₹%.2f", total)
            return
        try:
            self.render_screen(self.compose_payment_connecting_screen(camera_ready, total), "payment_connecting")
        except Exception as exc:
            logger.error("TFT show_payment_connecting: %s", exc)

    def show_payment_unavailable(self, camera_ready: bool, detail: str | None = None) -> None:
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT] Payment unavailable")
            return
        try:
            self.render_screen(
                self.compose_payment_unavailable_screen(camera_ready, detail),
                "payment_unavailable",
            )
        except Exception as exc:
            logger.error("TFT show_payment_unavailable: %s", exc)

    def show_payment_timeout(self, camera_ready: bool) -> None:
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT] Payment timeout")
            return
        try:
            self.render_screen(self.compose_payment_timeout_screen(camera_ready), "payment_timeout")
        except Exception as exc:
            logger.error("TFT show_payment_timeout: %s", exc)

    def show_processing_message(self, camera_ready: bool, title: str, subtitle: str | None = None) -> None:
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT] %s", title)
            return
        try:
            img, draw = self._blank("#111111")
            draw.text((14, 40), title, font=_load_font(16), fill=WHITE)
            if subtitle:
                draw.text((14, 64), subtitle, font=_load_font(11, bold=False), fill=LT_GREY)
            self._draw_camera_indicator(draw, camera_ready)
            self.render_screen(img, "status_message")
        except Exception as exc:
            logger.error("TFT show_processing_message: %s", exc)

    def show_payment_qr(
        self,
        camera_ready: bool,
        total: float,
        qr_image: Image.Image | None,
        footer_text: str = "Btn=skip(test)",
        footer_fill: str = GREY,
        order_id: str | None = None,
    ):
        """STAGE 6: Show payment QR code and amount."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT QR] total=₹%.2f", total)
            return
        try:
            img = self.compose_payment_screen(
                camera_ready=camera_ready,
                total=total,
                qr_image=qr_image,
                footer_text=footer_text,
                footer_fill=footer_fill,
                order_id=order_id,
            )
            self.render_screen(img, "payment_qr")
        except Exception as exc:
            logger.error("TFT show_payment_qr: %s", exc)

    def show_payment_success(self, camera_ready: bool, total: float, receipt_id: str):
        """STAGE 7: Payment confirmed."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT ✓] Payment ₹%.2f receipt=%s", total, receipt_id)
            return
        try:
            self.render_screen(
                self.compose_payment_success_screen(camera_ready, total, receipt_id),
                "payment_success",
            )
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
    _PRINT_ROW_CMD = 0xA2
    _FEED_PAPER_CMD = 0xA1
    _PRINTER_FONT_CANDIDATES = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]

    def __init__(self, mac_address: str):
        self._printer = None
        self._mode = None
        self._mac_address = mac_address

        if not mac_address:
            logger.warning("Bluetooth printer MAC not configured; skipping printer init")
            return

        if _BLUETOOTH_PRINTER_AVAILABLE and BTWPrinter is not None:
            try:
                self._printer = BTWPrinter(mac_address)
                self._mode = "escpos"
                logger.info("Bluetooth printer initialized via escpos: %s", mac_address)
                return
            except Exception as exc:
                logger.warning("escpos Bluetooth init failed (%s), trying raw Bluetooth mode", exc)

        if hasattr(socket, "AF_BLUETOOTH") and Image is not None and ImageDraw is not None and ImageFont is not None:
            self._mode = "raw_bt"
            logger.info("Bluetooth printer initialized via raw RFCOMM image mode: %s", mac_address)
            return

        logger.warning("No compatible Bluetooth printer backend available; receipt printing disabled")

    @staticmethod
    def _crc8(data: list[int]) -> int:
        crc = 0
        for byte in data:
            crc ^= (byte & 0xFF)
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc & 0xFF

    @classmethod
    def _make_packet(cls, cmd: int, data: list[int]) -> bytes:
        payload = [int(v) & 0xFF for v in data]
        return bytes([0x51, 0x78, cmd & 0xFF, 0x00, len(payload), 0x00] + payload + [cls._crc8(payload), 0xFF])

    @classmethod
    def _load_printer_font(cls, size: int):
        for path in cls._PRINTER_FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def _build_receipt_image(self, session_id: str, cart: SessionCart, total: float, payment_ref: str):
        lines = [
            SHOP_NAME,
            "=" * 32,
            f"Receipt: {session_id}",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 32,
            "",
        ]

        for item in cart.items.values():
            lines.append(f"{item.name[:20]} x{item.quantity}")
            lines.append(f"  INR {item.price:.2f} = INR {item.line_total():.2f}")

        tax = cart.subtotal * 0.18
        lines.extend(
            [
                "",
                "-" * 32,
                f"Subtotal: INR {cart.subtotal:.2f}",
                f"GST @18%: INR {tax:.2f}",
                f"TOTAL: INR {total:.2f}",
                "=" * 32,
                f"Payment: {payment_ref}",
                "",
                "Thank you for shopping!",
                "",
            ]
        )

        width = max(128, BT_PRINTER_WIDTH)
        margin_x = 8
        top_margin = 8
        bottom_margin = 12
        line_gap = 6
        font = self._load_printer_font(24)

        line_height = (font.getbbox("Hg")[3] - font.getbbox("Hg")[1]) + line_gap
        height = top_margin + (len(lines) * line_height) + bottom_margin

        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)

        y = top_margin
        for line in lines:
            draw.text((margin_x, y), line, fill=0, font=font)
            y += line_height

        return image.point(lambda x: 0 if x < 128 else 255).convert("1")

    def _send_image_raw_bluetooth(self, image) -> None:
        image_l = image.convert("L")
        packets: list[bytes] = []

        for y in range(image_l.height):
            row_bytes: list[int] = []
            for x in range(0, BT_PRINTER_WIDTH, 8):
                value = 0
                for bit in range(8):
                    xx = x + bit
                    px = 255
                    if xx < image_l.width:
                        px = image_l.getpixel((xx, y))
                    if px < 128:
                        value |= (1 << (7 - bit))
                row_bytes.append(value)
            packets.append(self._make_packet(self._PRINT_ROW_CMD, row_bytes))

        sock = None
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.connect((self._mac_address, BT_PRINTER_CHANNEL))
            time.sleep(0.6)
            for packet in packets:
                sock.send(packet)
                if BT_PRINTER_ROW_DELAY_SECONDS > 0:
                    time.sleep(BT_PRINTER_ROW_DELAY_SECONDS)
            sock.send(self._make_packet(self._FEED_PAPER_CMD, [0x32, 0x00]))
            time.sleep(0.8)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _print_receipt_raw_bt(self, session_id: str, cart: SessionCart, total: float, payment_ref: str) -> None:
        image = self._build_receipt_image(session_id, cart, total, payment_ref)
        self._send_image_raw_bluetooth(image)

    def print_receipt(self, session_id: str, cart: SessionCart, total: float, payment_ref: str):
        """Print a receipt for the transaction."""
        if self._mode is None:
            logger.warning("No printer available; skipping receipt for %s", session_id)
            return

        try:
            logger.info("[PRINTER] Connecting to Bluetooth printer...")
            if self._mode == "escpos" and self._printer is not None:
                self._printer.text(f"{SHOP_NAME}\n")
                self._printer.text("=" * 32 + "\n")
                self._printer.text(f"Receipt: {session_id}\n")
                self._printer.text(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                self._printer.text("=" * 32 + "\n\n")

                for item in cart.items.values():
                    self._printer.text(f"{item.name[:20]:<20}\n")
                    self._printer.text(f"  {item.quantity}x INR {item.price:.2f} = INR {item.line_total():.2f}\n")

                self._printer.text("\n" + "-" * 32 + "\n")
                self._printer.text(f"{'Subtotal':<20} INR {cart.subtotal:.2f}\n")
                tax = cart.subtotal * 0.18
                self._printer.text(f"{'GST @18%':<20} INR {tax:.2f}\n")
                self._printer.text(f"{'TOTAL':<20} INR {total:.2f}\n")
                self._printer.text("=" * 32 + "\n")
                self._printer.text(f"Payment: {payment_ref}\n")
                self._printer.text("\nThank you for shopping!\n\n")
                self._printer.cut()
            elif self._mode == "raw_bt":
                self._print_receipt_raw_bt(session_id, cart, total, payment_ref)
            else:
                logger.warning("No compatible printer backend active; skipping receipt for %s", session_id)
                return

            logger.info("[PRINTER] Invoice printed successfully")
            logger.info("Receipt printed for %s", session_id)
        except Exception as exc:
            logger.error("[PRINTER] Print failed: %s", exc)
            logger.error("Receipt print failed: %s", exc)

    def close(self) -> None:
        if self._mode != "escpos" or not self._printer:
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
        logger.info("Terminal input thread started")
        logger.info("Terminal fallback: 'done'=checkout | 'skip'=skip payment")
        logger.info("Extra controls: 'clear'=reset cart | 'quit'=stop runtime")
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
                    if line:
                        self._queue.put(f"terminal:{line}")
                else:
                    time.sleep(0.1)  # Small sleep to prevent busy loop
            except (EOFError, KeyboardInterrupt):
                break
            except Exception:
                time.sleep(0.1)

    def stop(self):
        self._running = False


class ScannerButtonController:
    """GPIO checkout button controller (active-low)."""

    def __init__(self, command_queue: queue.Queue, on_press=None):
        self._queue = command_queue
        self._on_press = on_press
        self._started = False
        self._running = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._debounce_seconds = max(0.05, CHECKOUT_BUTTON_BOUNCETIME_MS / 1000.0)
        self._last_press_at = 0.0
        self._actual_pin = CHECKOUT_BUTTON_PIN

    def start(self) -> None:
        if not CHECKOUT_BUTTON_ENABLED:
            logger.info("Checkout button disabled by CHECKOUT_BUTTON_ENABLED")
            return
        if GPIO is None:
            logger.warning("RPi.GPIO unavailable; checkout button disabled")
            return
        try:
            current_mode = GPIO.getmode()
            if current_mode is None:
                # Keep global mode consistent with TFT/HX711 usage.
                GPIO.setmode(GPIO.BCM)
                current_mode = GPIO.BCM

            if current_mode == GPIO.BCM:
                mode_label = "BCM"
                if CHECKOUT_BUTTON_PIN_MODE == "board":
                    mapped = BOARD_TO_BCM_PIN.get(CHECKOUT_BUTTON_PIN)
                    if mapped is None:
                        raise ValueError(
                            f"Unsupported board pin {CHECKOUT_BUTTON_PIN} for BCM mode mapping"
                        )
                    self._actual_pin = mapped
                else:
                    self._actual_pin = CHECKOUT_BUTTON_PIN
            elif current_mode == GPIO.BOARD:
                mode_label = "BOARD"
                if CHECKOUT_BUTTON_PIN_MODE == "bcm":
                    mapped = BCM_TO_BOARD_PIN.get(CHECKOUT_BUTTON_PIN)
                    if mapped is None:
                        raise ValueError(
                            f"Unsupported BCM pin {CHECKOUT_BUTTON_PIN} for BOARD mode mapping"
                        )
                    self._actual_pin = mapped
                else:
                    self._actual_pin = CHECKOUT_BUTTON_PIN
            else:
                raise RuntimeError(f"Unsupported GPIO mode: {current_mode}")

            GPIO.setup(self._actual_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self._started = True
            self._running = True
            self._thread.start()
            initial_level = GPIO.input(self._actual_pin)
            logger.info(
                "Checkout button ready on %s pin %d (initial=%d)",
                mode_label,
                self._actual_pin,
                initial_level,
            )
            if mode_label == "BCM":
                board_pin = BCM_TO_BOARD_PIN.get(self._actual_pin)
                if board_pin is not None:
                    logger.info(
                        "Checkout button mapping: BCM %d == physical pin %d (other side to GND)",
                        self._actual_pin,
                        board_pin,
                    )
            elif mode_label == "BOARD":
                bcm_pin = BOARD_TO_BCM_PIN.get(self._actual_pin)
                if bcm_pin is not None:
                    logger.info(
                        "Checkout button mapping: physical pin %d == BCM %d (other side to GND)",
                        self._actual_pin,
                        bcm_pin,
                    )
            if CHECKOUT_BUTTON_PIN_MODE == "board" and CHECKOUT_BUTTON_PIN == 11:
                logger.info("Button wiring: physical pin 11 -> button -> physical pin 39 (GND)")
        except Exception as exc:
            logger.warning("Checkout button init failed: %s", exc)

    def _run(self) -> None:
        if GPIO is None:
            return
        try:
            prev = GPIO.input(self._actual_pin)
        except Exception:
            prev = 1

        while self._running:
            try:
                cur = GPIO.input(self._actual_pin)
            except Exception:
                time.sleep(0.05)
                continue

            # Active-low button: transition HIGH->LOW means pressed.
            if prev == 1 and cur == 0:
                now = time.monotonic()
                if (now - self._last_press_at) >= self._debounce_seconds:
                    self._last_press_at = now
                    try:
                        logger.info("Checkout button edge detected")
                        if self._on_press is not None:
                            self._on_press()
                        else:
                            self._queue.put_nowait("button")
                    except Exception:
                        pass
            prev = cur
            time.sleep(0.02)

    def stop(self) -> None:
        if not self._started or GPIO is None:
            return
        self._running = False
        try:
            self._thread.join(timeout=0.5)
        except Exception:
            pass
        try:
            GPIO.cleanup([self._actual_pin])
        except Exception:
            pass
        self._started = False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUNTIME
# ══════════════════════════════════════════════════════════════════════════════
def main() -> int:
    logger.info("Smart Trolley starting up...")
    logger.info("Shop: %s", SHOP_NAME)

    if not _PSYCOPG2_AVAILABLE:
        logger.error("Missing psycopg2-binary. Install requirements before running.")
        return 1
    if not DATABASE_URL:
        logger.error("DATABASE_URL missing. Set it in .env before running.")
        return 1

    logger.info("Connecting to Neon PostgreSQL...")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        ensure_transaction_payment_columns()
        logger.info("DB connection successful")
    except Exception as exc:
        logger.error("DB connection failed: %s", exc)
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
        logger.info("Initializing TFT display...")
        display = TFTDisplay()
        if getattr(display, "_device", None):
            logger.info("TFT ready")
        else:
            logger.error("TFT init failed: device unavailable")
        display.show_boot_splash()

        cart = SessionCart()

        logger.info("Initializing camera (picamera2)...")
        scanner = BarcodeScanner()
        if scanner.is_camera_ready():
            logger.info("Camera ready")
        else:
            logger.error("Camera not detected: using fallback scanner input")

        logger.info("HX711 load sensor initializing...")
        weights = WeightReader(HX711_DOUT_PIN, HX711_SCK_PIN)
        if getattr(weights, "_hx", None):
            logger.info("Scale ready")
        else:
            logger.warning("Scale not responding")

        printer = ReceiptPrinter(BLUETOOTH_PRINTER_MAC)
    except KeyboardInterrupt:
        logger.info("Startup interrupted by user")
        lock.release()
        return 130

    # Terminal control thread (only when camera uses stdin fallback)
    # When camera works, commands should be handled via special barcodes
    terminal_queue = queue.Queue()
    terminal_ctrl = TerminalController(terminal_queue)
    button_ctrl = ScannerButtonController(terminal_queue)
    camera_ready = scanner.is_camera_ready()

    terminal_ctrl.start()
    button_ctrl.start()

    logger.info("=" * 50)
    if not camera_ready:
        logger.info("Camera unavailable - using stdin fallback for barcode input")
    else:
        logger.info("Camera ready - scanning barcodes with camera")
    logger.info("To checkout: press button once (or terminal fallback 'done')")
    logger.info("To skip test payment: press button again on payment screen (or fallback 'skip')")
    logger.info("To clear cart: type/scan 'clear'")
    logger.info("Terminal fallback: 'done'=checkout | 'skip'=skip payment")
    logger.info("=" * 50)

    running = True
    last_barcode = ""
    last_seen_at = 0.0
    checkout_triggered = False
    checkout_busy = False
    payment_context: dict | None = None
    payment_poll_thread: threading.Thread | None = None
    blink_timer = time.time()
    tft_reinit_at = time.monotonic()

    global app_state
    app_state = STATE_IDLE

    def _handle_stop(_sig=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    def _log_cart_state() -> None:
        logger.debug(
            "[CART STATE] state=%s | %d items | Total qty: %d | Subtotal: ₹%.2f",
            app_state,
            cart.unique_item_count,
            cart.total_quantity,
            cart.subtotal,
        )

    def _set_idle_or_scanning_state() -> None:
        global app_state
        # Keep scanner-ready mode as the normal waiting state.
        app_state = STATE_SCANNING

    def _sleep_with_button_handling(seconds: float) -> None:
        end_at = time.monotonic() + max(0.0, seconds)
        while running and time.monotonic() < end_at:
            _process_pending_commands()
            time.sleep(0.05)

    def _handle_payment_success(payment_details: dict) -> None:
        nonlocal payment_context, checkout_busy, checkout_triggered
        global app_state
        if app_state != STATE_PAYMENT or not payment_context:
            return

        checkout_busy = True
        payment_id = str(payment_details.get("id") or f"PAY_{uuid.uuid4().hex[:10]}")
        final_total = float(payment_context["final_total"])
        session_id = str(payment_context["session_id"])
        order_id = str(payment_context.get("order_id") or "")
        qr_id = str(payment_context.get("qr_id") or "")

        try:
            app_state = STATE_SUCCESS
            tx_id = mark_transaction_paid(
                session_id=session_id,
                payment_id=payment_id,
                razorpay_order_id=order_id,
                razorpay_qr_id=qr_id,
                items=cart.to_list(),
                total=final_total,
            )
            logger.info("[PAYMENT] DB updated - status: paid | ref: %s | tx=%d", payment_id, tx_id)

            if payment_id.startswith("TEST_SKIP_"):
                logger.warning("[STOCK] Testing mode payment - applying stock decrement")
            try:
                decrement_stock(cart.to_list())
            except Exception as exc:
                logger.error("[STOCK] Failed to decrement stock: %s", exc)
                camera_now = scanner.is_camera_ready()
                display.show_processing_message(camera_now, "Stock sync error", "Check admin panel")
                _sleep_with_button_handling(1.0)

            camera_now = scanner.is_camera_ready()
            display.show_payment_success(camera_now, final_total, payment_id)
            _sleep_with_button_handling(2.0)

            display.show_processing_message(camera_now, "Printing bill...")
            logger.info("[PRINTER] Printing invoice...")
            printer.print_receipt(session_id, cart, final_total, payment_id)

            display.show_processing_message(camera_now, "Thank you!", "Come again")
            _sleep_with_button_handling(3.0)

            cart.clear()
            payment_context = None
            checkout_triggered = False
            _set_idle_or_scanning_state()
            camera_now = scanner.is_camera_ready()
            display.show_idle(camera_now, cart.unique_item_count, cart.total_quantity, cart.subtotal)
            logger.info("[SESSION] Reset complete - returning to idle")
            _log_cart_state()
        except Exception as exc:
            logger.exception("[PAYMENT] Success handling failed: %s", exc)
        finally:
            checkout_busy = False

    def _handle_payment_timeout() -> None:
        nonlocal payment_context, checkout_triggered
        if app_state != STATE_PAYMENT or not payment_context:
            return
        logger.warning("[PAYMENT] Payment QR expired without payment")
        camera_now = scanner.is_camera_ready()
        display.show_payment_timeout(camera_now)
        _sleep_with_button_handling(3.0)
        payment_context = None
        checkout_triggered = False
        _set_idle_or_scanning_state()
        if cart.unique_item_count > 0:
            display.show_cart_summary(camera_now, cart)
        else:
            display.show_idle(camera_now, cart.unique_item_count, cart.total_quantity, cart.subtotal)

    def _handle_payment_poll_error(error_text: str) -> None:
        if app_state != STATE_PAYMENT or not payment_context:
            return
        logger.error("[PAYMENT] Polling thread crashed: %s", error_text)
        camera_now = scanner.is_camera_ready()
        display.show_payment_qr(
            camera_now,
            float(payment_context["final_total"]),
            payment_context.get("qr_image"),
            footer_text="Payment check error - use skip",
            footer_fill=RED,
            order_id=payment_context.get("order_id"),
        )

    def _queue_payment_success(payment_details: dict) -> None:
        try:
            terminal_queue.put(("payment_success", payment_details))
        except Exception:
            pass

    def _queue_payment_timeout() -> None:
        try:
            terminal_queue.put(("payment_timeout", None))
        except Exception:
            pass

    def _queue_payment_error(exc: Exception) -> None:
        try:
            terminal_queue.put(("payment_poll_error", str(exc)))
        except Exception:
            pass

    def _start_checkout_flow() -> None:
        nonlocal checkout_triggered, checkout_busy, payment_context, payment_poll_thread
        global app_state
        if checkout_busy:
            return
        if app_state not in (STATE_IDLE, STATE_SCANNING):
            return
        if cart.unique_item_count == 0:
            checkout_triggered = False
            logger.warning("[CHECKOUT] Cart is empty - ignoring checkout trigger")
            camera_now = scanner.is_camera_ready()
            display.show_cart_empty(camera_now)
            _sleep_with_button_handling(2.0)
            _set_idle_or_scanning_state()
            display.show_idle(camera_now, cart.unique_item_count, cart.total_quantity, cart.subtotal)
            return

        checkout_busy = True
        checkout_triggered = False
        subtotal = cart.subtotal
        gst = subtotal * 0.18
        final_total = subtotal + gst
        session_id = f"SESSION_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        camera_now = scanner.is_camera_ready()

        try:
            app_state = STATE_PAYMENT
            logger.info("[PAYMENT] Connecting to Razorpay...")
            display.show_payment_connecting(camera_now, final_total)

            if not _PAYMENT_MODULE_AVAILABLE:
                raise RuntimeError(f"payment module unavailable: {_PAYMENT_IMPORT_ERROR}")

            order_id = create_razorpay_order(final_total, session_id)
            qr_id, qr_image_url = generate_payment_qr(order_id, final_total)

            try:
                qr_image = download_qr_image(qr_image_url)
            except Exception as exc:
                qr_image = None
                logger.error("[PAYMENT] QR image download failed: %s", exc)

            pending_tx_id = save_transaction(
                session_id=session_id,
                items=cart.to_list(),
                total=final_total,
                status="pending",
                payment_method="Razorpay UPI",
                upi_ref="",
                razorpay_order_id=order_id,
                razorpay_qr_id=qr_id,
            )
            logger.info("[PAYMENT] Pending transaction saved: tx=%d", pending_tx_id)

            payment_context = {
                "session_id": session_id,
                "final_total": final_total,
                "order_id": order_id,
                "qr_id": qr_id,
                "tx_id": pending_tx_id,
                "qr_image": qr_image,
            }

            footer_text = "Btn=skip(test)"
            footer_fill = LT_GREY
            if qr_image is None:
                footer_text = "QR image error - poll active"
                footer_fill = RED

            display.show_payment_qr(
                camera_now,
                final_total,
                qr_image,
                footer_text=footer_text,
                footer_fill=footer_fill,
                order_id=order_id,
            )

            payment_poll_thread = threading.Thread(
                target=poll_payment_status,
                args=(qr_id, _queue_payment_success, _queue_payment_timeout),
                kwargs={"timeout": 600, "on_error": _queue_payment_error},
                daemon=True,
            )
            payment_poll_thread.start()
            logger.info("[PAYMENT] Polling thread started for QR: %s", qr_id)
        except Exception as exc:
            error_text = str(exc)
            logger.error("[PAYMENT] Setup failed: %s", error_text)

            # If merchant UPI QR is disabled, keep payment state active so operator
            # can press button once more to run test skip flow.
            if "UPI transactions are not enabled" in error_text:
                test_qr_image = None
                if _QRCODE_AVAILABLE:
                    try:
                        upi_string = (
                            f"upi://pay?pa={UPI_ID}&pn={SHOP_NAME}&am={final_total:.2f}"
                            f"&cu=INR&tn={session_id}"
                        )
                        qr = qrcode.QRCode(version=1, box_size=10, border=2)
                        qr.add_data(upi_string)
                        qr.make(fit=True)
                        test_qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                        logger.warning("[PAYMENT] Test mode local UPI QR generated (non-verified)")
                    except Exception as qr_exc:
                        logger.error("[PAYMENT] Test mode QR generation failed: %s", qr_exc)

                payment_context = {
                    "session_id": session_id,
                    "final_total": final_total,
                    "order_id": "TEST_MODE",
                    "qr_id": "TEST_MODE",
                    "tx_id": None,
                    "qr_image": test_qr_image,
                }
                app_state = STATE_PAYMENT
                if test_qr_image is not None:
                    display.show_payment_qr(
                        camera_now,
                        final_total,
                        test_qr_image,
                        footer_text="TEST MODE QR | Btn=skip",
                        footer_fill=ORANGE,
                        order_id="TEST_MODE",
                    )
                else:
                    display.show_payment_unavailable(camera_now, "UPI unavailable | Press button to skip")
                logger.warning("[PAYMENT] UPI unavailable - test mode active (QR shown if available)")
                return

            display.show_payment_unavailable(camera_now, error_text)

            _sleep_with_button_handling(2.0)
            payment_context = None
            _set_idle_or_scanning_state()
            display.show_idle(camera_now, cart.unique_item_count, cart.total_quantity, cart.subtotal)
        finally:
            checkout_busy = False

    def trigger_checkout(source: str) -> None:
        nonlocal checkout_triggered
        if app_state not in (STATE_IDLE, STATE_SCANNING):
            logger.info("[CHECKOUT] Ignored in state: %s", app_state)
            return
        if source == "physical_button":
            logger.info("[CHECKOUT] Triggered via physical button")
        else:
            logger.info("[CHECKOUT] Triggered via terminal fallback")
        checkout_triggered = True

    def trigger_skip_payment(source: str) -> None:
        if app_state != STATE_PAYMENT or not payment_context:
            logger.info("[PAYMENT] Skip ignored in state: %s", app_state)
            return
        session_id = str(payment_context["session_id"])
        total = float(payment_context["final_total"])
        if source == "physical_button":
            logger.warning("[PAYMENT] TESTING MODE - Payment skipped via button")
        else:
            logger.warning("[PAYMENT] TESTING MODE - Payment skipped via terminal fallback")
        _handle_payment_success(
            {
                "id": f"TEST_SKIP_{session_id}",
                "amount": int(round(total * 100)),
                "status": "captured",
            }
        )

    def _process_pending_commands() -> None:
        nonlocal running
        while True:
            try:
                cmd = terminal_queue.get_nowait()
            except queue.Empty:
                break

            if isinstance(cmd, tuple) and len(cmd) == 2:
                event_name, payload = cmd
                if event_name == "payment_success":
                    _handle_payment_success(payload if isinstance(payload, dict) else {})
                elif event_name == "payment_timeout":
                    _handle_payment_timeout()
                elif event_name == "payment_poll_error":
                    _handle_payment_poll_error(str(payload))
                continue

            if cmd in ("button", "scanner"):
                if app_state in (STATE_IDLE, STATE_SCANNING):
                    trigger_checkout("physical_button")
                elif app_state == STATE_PAYMENT:
                    trigger_skip_payment("physical_button")
                else:
                    logger.debug("Button press ignored in state: %s", app_state)
            elif isinstance(cmd, str) and cmd.startswith("terminal:"):
                entered = cmd.split(":", 1)[1].strip().lower()
                if entered == "done":
                    trigger_checkout("terminal_fallback")
                elif entered == "skip":
                    trigger_skip_payment("terminal_fallback")
                elif entered == "clear":
                    logger.info("[TERMINAL] 'clear' received -> resetting cart")
                    cart.clear()
                    camera_now = scanner.is_camera_ready()
                    display.show_idle(camera_now, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    _set_idle_or_scanning_state()
                    logger.info("[SESSION] Cart cleared — returning to idle")
                    _log_cart_state()
                elif entered == "quit":
                    logger.info("[TERMINAL] 'quit' received -> stopping runtime")
                    running = False
                else:
                    logger.warning("[TERMINAL] Unknown command: '%s' — use 'done', 'skip', 'clear', or 'quit'", entered)
            elif cmd == "quit":
                running = False

    # Show initial idle screen
    camera_ready = scanner.is_camera_ready()
    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
    _set_idle_or_scanning_state()
    logger.info("Scan loop started — waiting for barcode...")

    # Main event loop
    try:
        while running:
            try:
                _process_pending_commands()

                if checkout_triggered and app_state in (STATE_IDLE, STATE_SCANNING) and not checkout_busy:
                    _start_checkout_flow()

                if app_state in (STATE_PAYMENT, STATE_SUCCESS):
                    time.sleep(0.05)
                    continue

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
                logger.info("Barcode detected: %s (type: %s)", barcode, scanner.last_barcode_type())

            # Handle special commands (when using stdin fallback)
                if barcode.lower() in ("quit", "done", "skip", "clear"):
                    if barcode.lower() == "quit":
                        logger.info("Quit command received")
                        running = False
                        break
                    elif barcode.lower() == "clear":
                        logger.info("[TERMINAL] 'clear' received -> resetting cart")
                        cart.clear()
                        display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                        _set_idle_or_scanning_state()
                        logger.info("[SESSION] Cart cleared — returning to idle")
                        _log_cart_state()
                    elif barcode.lower() == "done":
                        trigger_checkout("terminal_fallback")
                    elif barcode.lower() == "skip":
                        trigger_skip_payment("terminal_fallback")
                    continue

            # Handle special barcodes
                if barcode == BARCODE_REMOVE_LAST:
                    result = cart.decrement_last()
                    if result:
                        name, new_qty = result
                        if new_qty <= 0:
                            logger.info("[CART] Removed: '%s' (qty was 0)", name)
                        else:
                            logger.info("[CART] Decremented: '%s' → x%d", name, new_qty)
                        display.show_item_removed(camera_ready, name, new_qty,
                                                 cart.unique_item_count, cart.total_quantity, cart.subtotal)
                        _sleep_with_button_handling(2.0)
                    else:
                        logger.info("No item to decrement")
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    _set_idle_or_scanning_state()
                    _log_cart_state()
                    continue

                if barcode == BARCODE_CMD_CLEAR:
                    name = cart.remove_last()
                    if name:
                        logger.info("[CART] Removed: '%s' (qty was 0)", name)
                        display.show_item_removed(camera_ready, name, 0,
                                                 cart.unique_item_count, cart.total_quantity, cart.subtotal)
                        _sleep_with_button_handling(2.0)
                    else:
                        logger.info("No item to remove")
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    _set_idle_or_scanning_state()
                    _log_cart_state()
                    continue

            # Regular product lookup
                t0 = time.monotonic()
                product = get_product_by_barcode(barcode)
                lookup_ms = (time.monotonic() - t0) * 1000.0

                if not product:
                    logger.warning("Product not found in DB → Barcode: %s", barcode)
                    display.show_product_not_found(camera_ready, barcode,
                                                   cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    _sleep_with_button_handling(2.5)
                    display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                    continue

                logger.info(
                    "Product found -> Name: '%s' | Price: ₹%.2f | Stock: %s",
                    product.get("name"),
                    float(product.get("price", 0.0)),
                    product.get("stock", "N/A"),
                )

                stock_level = int(product.get("stock") or 0)
                if stock_level <= 0:
                    logger.warning("[STOCK] OUT OF STOCK: '%s'", product.get("name"))
                elif stock_level <= LOW_STOCK_THRESHOLD:
                    logger.warning(
                        "[STOCK] Low stock warning: '%s' - %d remaining",
                        product.get("name"),
                        stock_level,
                    )

            # Add to cart
                was_already_in_cart = barcode in cart.items
                name, qty = cart.add(product)
                try:
                    measured_weight = weights.read_grams()
                    if measured_weight is None:
                        logger.warning("[SCALE] Failed to read weight: unavailable")
                    else:
                        logger.debug("[SCALE] Weight reading: %sg", measured_weight)
                except Exception as exc:
                    logger.warning("[SCALE] Failed to read weight: %s", exc)
                    measured_weight = None

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
                    logger.info("[CART] Qty updated: '%s' → x%d", name, qty)
                    display.show_qty_updated(
                        camera_ready,
                        name,
                        qty,
                        cart.unique_item_count,
                        cart.total_quantity,
                        cart.subtotal,
                    )
                    _sleep_with_button_handling(2.0)
                else:
                    logger.info("[CART] Added: '%s' x1 @ ₹%.2f", name, float(product["price"]))
                    display.show_product_added(
                        camera_ready,
                        name,
                        float(product["price"]),
                        qty,
                        cart.unique_item_count,
                        cart.total_quantity,
                        cart.subtotal,
                        expected_weight,
                        stock_level,
                    )
                    _sleep_with_button_handling(2.5)

                display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                _set_idle_or_scanning_state()
                _log_cart_state()

            except KeyboardInterrupt:
                running = False
            except Exception as exc:
                logger.exception("Runtime error: %s", exc)
                try:
                    _sleep_with_button_handling(1.0)
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
            button_ctrl.stop()
        except Exception:
            pass
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
