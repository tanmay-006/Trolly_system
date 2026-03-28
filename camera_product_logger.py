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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("camera_product_logger")

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SCAN_DEBOUNCE_SECONDS = float(os.getenv("SCAN_DEBOUNCE_SECONDS", "1.2"))
LOCK_PATH = "/tmp/camera_product_logger.lock"


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
        try:
            self._camera.stop()
        except Exception:
            pass
        try:
            self._camera.close()
        except Exception:
            pass


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
        if holder:
            logger.error(
                "Another camera logger is running (pid=%d). Stop it first.",
                holder,
            )
        else:
            logger.error("Another camera logger is running. Stop it first.")
        return 1

    scanner = None
    running = True
    last_barcode = ""
    last_seen_at = 0.0

    def _handle_stop(_sig=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        scanner = CameraScanner()
        logger.info("Ready. Point camera at barcode...")

        while running:
            try:
                barcode = scanner.read_barcode()
            except Exception as exc:
                logger.error("Camera read error: %s", exc)
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
                continue

            logger.info(
                "Product details: barcode=%s name=%s price=%.2f category=%s weight_grams=%s",
                product.get("barcode"),
                product.get("name"),
                float(product.get("price", 0.0)),
                product.get("category"),
                product.get("weight_grams"),
            )

    finally:
        if scanner:
            scanner.close()
        lock.release()
        logger.info("Camera product logger stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
