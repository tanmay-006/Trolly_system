#!/usr/bin/env python3
"""
tft_display.py — ST7735 TFT display driver for Smart Trolley POS
Renders product-added cards and cart totals on a 160×128 SPI display.

Gracefully does nothing when luma.lcd is not installed (dev machine / CI).

SPI wiring assumed:
  port=0, device=0, DC=GPIO24, RST=GPIO25, bus_speed=16 MHz
"""

import logging

logger = logging.getLogger(__name__)

# ── Try to import luma; fail gracefully on non-Pi machines ───────────────────
try:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735
    from PIL import Image, ImageDraw, ImageFont
    _LUMA_AVAILABLE = True
except ImportError:
    _LUMA_AVAILABLE = False
    logger.warning(
        "luma.lcd / Pillow not found — TFT display disabled (non-Pi environment)"
    )


# ── Colour palette ────────────────────────────────────────────────────────────
BG       = "black"
WHITE    = "white"
GREEN    = "#00e676"
YELLOW   = "#ffd600"
CYAN     = "#00e5ff"
GREY     = "#555555"
RED      = "#ff5252"

# ── Display geometry ──────────────────────────────────────────────────────────
WIDTH  = 160
HEIGHT = 128


def _load_font(size: int):
    """Return a bitmap or default font — never raises."""
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


class TFTDisplay:
    """
    High-level display abstraction for the Smart Trolley.

    All public methods are safe to call even when luma is unavailable —
    they simply log and return immediately.
    """

    def __init__(self):
        self._device = None
        if not _LUMA_AVAILABLE:
            return
        try:
            serial = spi(
                port=0, device=0,
                gpio_DC=24, gpio_RST=25,
                bus_speed_hz=16_000_000
            )
            self._device = st7735(
                serial,
                width=WIDTH, height=HEIGHT,
                rotate=2, bgr=True
            )
            logger.info("TFT display initialised (%dx%d)", WIDTH, HEIGHT)
            self._show_splash()
        except Exception as exc:
            logger.error("TFT init failed: %s", exc)
            self._device = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _render(self, image: "Image.Image"):
        """Push a PIL image to the display."""
        if self._device:
            self._device.display(image)

    def _blank(self) -> tuple["Image.Image", "ImageDraw.ImageDraw"]:
        """Return a fresh black canvas + draw handle."""
        img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(img)
        return img, draw

    def _truncate(self, text: str, font, max_width: int) -> str:
        """Truncate text so it fits within max_width pixels."""
        while len(text) > 0:
            bbox = font.getbbox(text)
            text_width = bbox[2] - bbox[0]
            if text_width <= max_width:
                return text
            text = text[: len(text) - 1]
        return ""

    # ── Public screens ────────────────────────────────────────────────────────

    def _show_splash(self):
        """Boot splash shown once at startup."""
        if not _LUMA_AVAILABLE:
            return
        img, draw = self._blank()
        f_big  = _load_font(20)
        f_med  = _load_font(13)
        draw.text((20, 25), "SMART",   font=f_big, fill=WHITE)
        draw.text((20, 52), "TROLLEY", font=f_big, fill=YELLOW)
        draw.text((18, 82), "Ready to scan...", font=f_med, fill=CYAN)
        draw.line([(0, 110), (160, 110)], fill=GREY, width=1)
        draw.text((10, 113), "Scan item to begin", font=_load_font(11), fill=GREY)
        self._render(img)

    def show_product_added(
        self,
        name: str,
        price: float,
        qty: int,
        cart_total: float,
        cart_count: int = 0,
    ):
        """
        Show product-added confirmation screen.

        Layout (160×128):
          ┌────────────────────┐
          │ ✓ ADDED            │  ← green header
          │ <Product Name>     │  ← truncated to fit
          │ ₹20.00 × 2         │  ← price × qty
          │ ────────────────── │  ← divider
          │ Cart: ₹40.00       │  ← running total (yellow)
          │ (3 items)          │  ← item count (grey)
          └────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info(
                "[TFT] Product added: %s ₹%.2f×%d | cart total ₹%.2f",
                name, price, qty, cart_total
            )
            return

        if not self._device:
            return

        try:
            img, draw = self._blank()

            f_header = _load_font(15)
            f_name   = _load_font(13)
            f_price  = _load_font(13)
            f_total  = _load_font(15)
            f_small  = _load_font(11)

            # ── Header ────────────────────────────────────────────────────────
            draw.rectangle([(0, 0), (WIDTH, 22)], fill="#1b5e20")
            draw.text((6, 4), "\u2713 ADDED", font=f_header, fill=GREEN)

            # ── Product name (truncated) ──────────────────────────────────────
            short_name = self._truncate(name, f_name, WIDTH - 10)
            draw.text((6, 28), short_name, font=f_name, fill=WHITE)

            # ── Price × qty ───────────────────────────────────────────────────
            price_line = f"\u20b9{price:.2f}  \u00d7  {qty}"
            draw.text((6, 48), price_line, font=f_price, fill=CYAN)

            # ── Divider ───────────────────────────────────────────────────────
            draw.line([(0, 70), (WIDTH, 70)], fill=GREY, width=1)

            # ── Cart total ────────────────────────────────────────────────────
            draw.text((6, 76), "Cart Total", font=f_small, fill=GREY)
            draw.text((6, 91), f"\u20b9{cart_total:.2f}", font=f_total, fill=YELLOW)

            # ── Item count ────────────────────────────────────────────────────
            if cart_count > 0:
                count_label = f"{cart_count} item{'s' if cart_count != 1 else ''}"
                draw.text((6, 112), count_label, font=f_small, fill=GREY)

            self._render(img)

        except Exception as exc:
            logger.error("TFT show_product_added failed: %s", exc)

    def show_payment_success(self, total: float, receipt_id: str = ""):
        """
        Show payment confirmed screen.

        Layout (160×128):
          ┌────────────────────┐
          │  PAYMENT SUCCESS   │  ← green header
          │                    │
          │   ✓ PAID           │  ← large green tick
          │   ₹<total>         │  ← amount in white
          │ ─────────────────  │
          │ Thank you!         │  ← yellow
          │ <receipt_id>       │  ← grey, small
          └────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info("[TFT] Payment success — ₹%.2f  receipt=%s", total, receipt_id)
            return

        if not self._device:
            return

        try:
            img, draw = self._blank()

            # ── Header ────────────────────────────────────────────────────────
            draw.rectangle([(0, 0), (WIDTH, 22)], fill="#1b5e20")
            draw.text((6, 4), "PAYMENT SUCCESS", font=_load_font(13), fill=GREEN)

            # ── Big tick ──────────────────────────────────────────────────────
            draw.text((18, 28), "\u2714", font=_load_font(36), fill=GREEN)

            # ── Amount ────────────────────────────────────────────────────────
            draw.text((72, 35), "PAID", font=_load_font(13), fill=WHITE)
            draw.text((72, 53), f"\u20b9{total:.2f}", font=_load_font(15), fill=WHITE)

            # ── Divider ───────────────────────────────────────────────────────
            draw.line([(0, 80), (WIDTH, 80)], fill=GREY, width=1)

            # ── Thank you ─────────────────────────────────────────────────────
            draw.text((18, 87), "Thank you!", font=_load_font(15), fill=YELLOW)

            # ── Receipt ID (small, truncated) ─────────────────────────────────
            if receipt_id:
                short_id = self._truncate(receipt_id, _load_font(10), WIDTH - 10)
                draw.text((6, 112), short_id, font=_load_font(10), fill=GREY)

            self._render(img)

        except Exception as exc:
            logger.error("TFT show_payment_success failed: %s", exc)

    def show_cart_cleared(self):
        """Show idle/cleared screen after cart is cleared or payment done."""
        if not _LUMA_AVAILABLE:
            logger.info("[TFT] Cart cleared — showing idle screen")
            return
        self._show_splash()


    def show_error(self, message: str):
        """Show a brief error message on the display."""
        if not _LUMA_AVAILABLE:
            logger.info("[TFT] Error: %s", message)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            draw.rectangle([(0, 0), (WIDTH, 22)], fill="#b71c1c")
            draw.text((6, 4), "! ERROR", font=_load_font(15), fill=RED)
            short = self._truncate(message, _load_font(12), WIDTH - 10)
            draw.text((6, 35), short, font=_load_font(12), fill=WHITE)
            self._render(img)
        except Exception as exc:
            logger.error("TFT show_error failed: %s", exc)
