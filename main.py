#!/usr/bin/env python3
"""
Phase 2 Pi runtime: idle screen, barcode scan, Neon lookup, HX711 read,
and live cart summary on TFT.

Works with graceful fallbacks on non-Pi machines:
- If picamera2/pyzbar are unavailable, falls back to stdin barcode input.
- If hx711 is unavailable, weight reads are skipped.
- If luma.lcd is unavailable, TFT calls become no-ops via tft_display.
"""

from __future__ import annotations

import os
import time
import signal
import logging
import threading
from dataclasses import dataclass
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
    _DOTENV_AVAILABLE = True
except Exception:
    _DOTENV_AVAILABLE = False

    def load_dotenv(*_args, **_kwargs):
        return False

import tft_display

# Optional hardware/runtime imports
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


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pi_runtime")


load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

HX711_DOUT_PIN = int(os.getenv("HX711_DOUT_PIN", "5"))
HX711_SCK_PIN = int(os.getenv("HX711_SCK_PIN", "6"))
HX711_DISABLE = os.getenv("HX711_DISABLE", "0").strip().lower() in {"1", "true", "yes", "on"}
SCAN_DEBOUNCE_SECONDS = float(os.getenv("SCAN_DEBOUNCE_SECONDS", "1.2"))


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


@dataclass
class CartItem:
    barcode: str
    name: str
    price: float
    quantity: int = 1


class SessionCart:
    def __init__(self):
        self.items: dict[str, CartItem] = {}

    def add(self, product: dict) -> None:
        code = str(product["barcode"])
        if code in self.items:
            self.items[code].quantity += 1
            return
        self.items[code] = CartItem(
            barcode=code,
            name=str(product["name"]),
            price=float(product["price"]),
            quantity=1,
        )

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items.values())

    @property
    def subtotal(self) -> float:
        return sum(item.price * item.quantity for item in self.items.values())


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
            # Avoid noisy GPIO channel reuse warnings when another part of the app touched GPIO.
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
            logger.warning("HX711 %s timed out after %.2fs; disabling weight reads", method_name, timeout_seconds)
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


class BarcodeScanner:
    def __init__(self):
        self._camera = None
        self._mock_stdin = not (_PICAMERA_AVAILABLE and _PYZBAR_AVAILABLE)

        if self._mock_stdin:
            logger.warning(
                "Camera scanner unavailable (picamera2=%s pyzbar=%s). Using stdin fallback. "
                "On Raspberry Pi install with: sudo apt install -y python3-picamera2",
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
            logger.warning("Camera init failed: %s. Using stdin fallback.", exc)
            self._camera = None
            self._mock_stdin = True

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

        frame = self._camera.capture_array()
        decoded = zbar_decode(frame)
        if not decoded:
            return None

        try:
            return decoded[0].data.decode("utf-8").strip()
        except Exception:
            return None

    def close(self) -> None:
        if self._camera:
            try:
                self._camera.stop()
            except Exception:
                pass


def main() -> int:
    if not _PSYCOPG2_AVAILABLE:
        logger.error("Missing dependency: psycopg2-binary. Install requirements before running main.py")
        return 1
    if not _DOTENV_AVAILABLE:
        logger.warning("python-dotenv not installed; relying on existing environment variables")

    if not DATABASE_URL:
        logger.error("DATABASE_URL is missing. Set it in .env before running main.py")
        return 1

    try:
        display = tft_display.TFTDisplay()
        cart = SessionCart()
        scanner = BarcodeScanner()
        weights = WeightReader(HX711_DOUT_PIN, HX711_SCK_PIN)
    except KeyboardInterrupt:
        logger.info("Startup interrupted by user")
        return 130

    running = True
    last_barcode = ""
    last_seen_at = 0.0

    def _handle_stop(_sig=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    display.show_idle_scan(item_count=cart.item_count, subtotal=cart.subtotal)
    logger.info("Pi runtime ready. Waiting for barcode scans...")

    while running:
        try:
            barcode = scanner.read_barcode()
            if not barcode:
                if scanner._mock_stdin:
                    time.sleep(0.2)
                continue

            now = time.monotonic()
            if barcode == last_barcode and (now - last_seen_at) < SCAN_DEBOUNCE_SECONDS:
                continue
            last_barcode = barcode
            last_seen_at = now

            t0 = time.monotonic()
            product = get_product_by_barcode(barcode)
            lookup_ms = (time.monotonic() - t0) * 1000.0

            if not product:
                logger.info("Barcode=%s not found (lookup %.0f ms)", barcode, lookup_ms)
                display.show_product_not_found(barcode)
                time.sleep(2.0)
                display.show_idle_scan(item_count=cart.item_count, subtotal=cart.subtotal)
                continue

            cart.add(product)
            measured_weight = weights.read_grams()
            expected_weight = product.get("weight_grams")

            logger.info(
                "Scan OK barcode=%s name=%s lookup=%.0fms expected_g=%s measured_g=%s cart_items=%d subtotal=₹%.2f",
                barcode,
                product.get("name"),
                lookup_ms,
                expected_weight,
                measured_weight,
                cart.item_count,
                cart.subtotal,
            )

            display.show_scan_product_card(
                name=str(product["name"]),
                price=float(product["price"]),
                expected_weight_g=expected_weight,
                measured_weight_g=measured_weight,
                cart_count=cart.item_count,
                cart_subtotal=cart.subtotal,
            )

            # Keep the card briefly visible, then return to idle summary.
            time.sleep(1.2)
            display.show_idle_scan(item_count=cart.item_count, subtotal=cart.subtotal)

        except KeyboardInterrupt:
            running = False
        except Exception as exc:
            logger.exception("Runtime error: %s", exc)
            try:
                display.show_error("Scan loop error")
                time.sleep(1.0)
                display.show_idle_scan(item_count=cart.item_count, subtotal=cart.subtotal)
            except Exception:
                pass

    scanner.close()
    display.show_idle_scan(item_count=cart.item_count, subtotal=cart.subtotal)
    logger.info("Pi runtime stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
