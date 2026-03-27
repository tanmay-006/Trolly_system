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
        try:
            serial = spi(
                port=0, device=0,
                gpio_DC=24, gpio_RST=25,
                bus_speed_hz=16_000_000,
            )
            self._device = st7735(
                serial,
                width=TFT_WIDTH, height=TFT_HEIGHT,
                rotate=2, bgr=True,
            )
            logger.info("TFT display initialized (%dx%d)", TFT_WIDTH, TFT_HEIGHT)
        except Exception as exc:
            logger.error("TFT init failed: %s", exc)
            self._device = None

    def _render(self, image: Image.Image) -> None:
        """Push PIL image to display (thread-safe)."""
        if not self._device:
            return
        with self._lock:
            self._device.display(image)

    def _blank(self, bg: str = BG) -> tuple:
        img = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), bg)
        draw = ImageDraw.Draw(img)
        return img, draw

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
    def show_idle(self, camera_ready: bool, unique_items: int, total_qty: int, subtotal: float):
        """STAGE 1: Idle / waiting for scan."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT IDLE] items=%d qty=%d total=₹%.2f", unique_items, total_qty, subtotal)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Main content
            draw.text((22, 24), "SMART", font=_load_font(22), fill=WHITE)
            draw.text((14, 50), "TROLLEY", font=_load_font(22), fill=YELLOW)
            draw.line([(0, 78), (TFT_WIDTH, 78)], fill=GREY, width=1)
            draw.text((14, 84), "Scan your items", font=_load_font(11, bold=False), fill=CYAN)

            # Cart footer
            if unique_items > 0:
                self._draw_cart_footer(draw, unique_items, total_qty, subtotal)
            else:
                draw.text((42, TFT_HEIGHT - 16), "• Ready •", font=_load_font(10), fill=GREEN)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_idle: %s", exc)

    def show_product_added(self, camera_ready: bool, name: str, price: float, qty: int,
                          unique_items: int, total_qty: int, subtotal: float):
        """STAGE 2: Product found and added."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT+] %s ₹%.2f×%d", name, price, qty)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Header
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill=DK_GREEN)
            draw.text((6, 3), "✓ ADDED", font=_load_font(14), fill=GREEN)

            # Content
            short = self._truncate(name, _load_font(13), TFT_WIDTH - 10)
            draw.text((6, 26), short, font=_load_font(13), fill=WHITE)
            draw.text((6, 44), f"₹{price:.2f}", font=_load_font(14), fill=YELLOW)
            draw.text((6, 62), f"Qty in cart: {qty}", font=_load_font(11, bold=False), fill=CYAN)

            # Cart footer
            self._draw_cart_footer(draw, unique_items, total_qty, subtotal)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_product_added: %s", exc)

    def show_product_not_found(self, camera_ready: bool, barcode: str,
                               unique_items: int, total_qty: int, subtotal: float):
        """STAGE 3: Product not found."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT MISS] barcode=%s", barcode)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Header
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill=DK_RED)
            draw.text((6, 3), "NOT FOUND", font=_load_font(14), fill=RED)

            # Content
            draw.text((6, 28), "Product not found", font=_load_font(12), fill=WHITE)
            short_code = self._truncate(barcode, _load_font(10, bold=False), TFT_WIDTH - 10)
            draw.text((6, 48), short_code, font=_load_font(10, bold=False), fill=LT_GREY)
            draw.text((6, 68), "Try scanning again", font=_load_font(10, bold=False), fill=ORANGE)

            # Cart footer
            if unique_items > 0:
                self._draw_cart_footer(draw, unique_items, total_qty, subtotal)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_product_not_found: %s", exc)

    def show_item_removed(self, camera_ready: bool, name: str, new_qty: int,
                         unique_items: int, total_qty: int, subtotal: float):
        """STAGE 4: Item quantity decreased or removed."""
        if not _DISPLAY_AVAILABLE or not self._device:
            logger.info("[TFT-] %s new_qty=%d", name, new_qty)
            return
        try:
            img, draw = self._blank()

            # Camera indicator
            self._draw_camera_indicator(draw, camera_ready)

            # Header
            draw.rectangle([(0, 0), (TFT_WIDTH, 20)], fill=DK_RED)
            draw.text((6, 3), "REMOVED" if new_qty == 0 else "QTY -1", font=_load_font(14), fill=RED)

            # Content
            short = self._truncate(name, _load_font(13), TFT_WIDTH - 10)
            draw.text((6, 28), short, font=_load_font(13), fill=WHITE)
            if new_qty > 0:
                draw.text((6, 48), f"New qty: {new_qty}", font=_load_font(12), fill=CYAN)
            else:
                draw.text((6, 48), "Removed from cart", font=_load_font(11), fill=ORANGE)

            # Cart footer
            if unique_items > 0:
                self._draw_cart_footer(draw, unique_items, total_qty, subtotal)

            self._render(img)
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

    # Initialize hardware
    try:
        display = TFTDisplay()
        cart = SessionCart()
        scanner = BarcodeScanner()
        weights = WeightReader(HX711_DOUT_PIN, HX711_SCK_PIN)
        printer = ReceiptPrinter(BLUETOOTH_PRINTER_MAC)
    except KeyboardInterrupt:
        logger.info("Startup interrupted by user")
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
    while running:
        try:
            # Camera indicator blink (every 1 second)
            if time.time() - blink_timer > 1.0:
                display.toggle_blink()
                camera_ready = scanner.is_camera_ready()
                display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                blink_timer = time.time()

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
                time.sleep(2.0)
                display.show_idle(camera_ready, cart.unique_item_count, cart.total_quantity, cart.subtotal)
                continue

            # Add to cart
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

            display.show_product_added(
                camera_ready,
                name,
                float(product["price"]),
                qty,
                cart.unique_item_count,
                cart.total_quantity,
                cart.subtotal,
            )

            time.sleep(2.0)
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

    # Cleanup
    scanner.close()
    if terminal_ctrl:
        terminal_ctrl.stop()
    camera_ready = scanner.is_camera_ready()
    display.show_idle(camera_ready, 0, 0, 0.0)
    logger.info("Pi runtime stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
