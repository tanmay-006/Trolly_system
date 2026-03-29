#!/usr/bin/env python3
"""Razorpay payment helpers for Smart Trolley runtime."""

from __future__ import annotations

import logging
import time
from io import BytesIO
from typing import Callable

import requests
from PIL import Image

from config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, SHOP_NAME

try:
    import razorpay
except Exception:
    razorpay = None

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    if razorpay is None:
        raise RuntimeError("razorpay package is not installed")
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay credentials missing in environment")

    _client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    return _client


def create_razorpay_order(total_amount_rupees: float, session_id: str) -> str:
    client = _get_client()
    order_data = {
        "amount": int(round(total_amount_rupees * 100)),
        "currency": "INR",
        "receipt": session_id,
        "notes": {
            "source": "SmartTrolley",
            "session_id": session_id,
        },
    }
    order = client.order.create(data=order_data)
    order_id = str(order["id"])
    logger.info("[PAYMENT] Order created: %s | Rs%.2f", order_id, total_amount_rupees)
    return order_id


def generate_payment_qr(order_id: str, total_amount_rupees: float) -> tuple[str, str]:
    client = _get_client()
    qr_data = {
        "type": "upi_qr",
        "name": SHOP_NAME,
        "usage": "single_use",
        "fixed_amount": True,
        "payment_amount": int(round(total_amount_rupees * 100)),
        "description": "Smart Trolley Payment",
        "close_by": int(time.time()) + 600,
        "notes": {
            "order_id": order_id,
        },
    }
    qr = client.qrcode.create(data=qr_data)
    qr_id = str(qr["id"])
    qr_image_url = str(qr["image_url"])
    logger.info("[PAYMENT] QR generated: %s | URL: %s", qr_id, qr_image_url)
    return qr_id, qr_image_url


def download_qr_image(qr_image_url: str) -> Image.Image:
    response = requests.get(qr_image_url, timeout=10)
    response.raise_for_status()
    qr_image = Image.open(BytesIO(response.content)).convert("RGB")
    qr_image = qr_image.resize((100, 100), Image.LANCZOS)
    logger.info("[PAYMENT] QR image downloaded (%dx%dpx)", qr_image.width, qr_image.height)
    return qr_image


def _extract_first_captured_payment(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None

    items = payload.get("items")
    if isinstance(items, list):
        for payment in items:
            if isinstance(payment, dict) and payment.get("status") == "captured":
                return payment
    return None


def poll_payment_status(
    qr_id: str,
    on_success: Callable[[dict], None],
    on_timeout: Callable[[], None],
    timeout: int = 600,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    client = _get_client()
    start = time.time()

    while time.time() - start < timeout:
        time.sleep(3)
        elapsed = int(time.time() - start)
        try:
            payments = client.qrcode.fetch_all_payments(qr_id)
            captured = _extract_first_captured_payment(payments)
            if captured is not None:
                amount = float(captured.get("amount", 0)) / 100.0
                logger.info("[PAYMENT] SUCCESS - ID: %s | Rs%.2f", captured.get("id", "unknown"), amount)
                on_success(captured)
                return
            logger.debug("[PAYMENT] Polling... %ss elapsed", elapsed)
        except Exception as exc:
            logger.error("[PAYMENT] Polling error: %s", exc)
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:
                    pass

    logger.warning("[PAYMENT] QR expired - no payment within 10 minutes")
    on_timeout()
