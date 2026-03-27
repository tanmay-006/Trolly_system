#!/usr/bin/env python3
"""
tft_display.py — ST7735 TFT display driver for Smart Trolley POS
Renders all POS screens on a 160×128 SPI display.

Screens:
  _show_splash()            Boot / idle
  show_product_added()      Item added to cart  (green)
  show_product_removed()    Item removed        (red)
  show_cart_list()          Full cart overview  (up to 5 rows)
  show_payment_qr()         UPI QR code + total (yellow)
  show_payment_success()    Payment confirmed   (green)
  show_cart_cleared()       Reset to idle splash
  show_error()              Error banner        (red)

Gracefully does nothing when luma.lcd / Pillow are not installed
(dev machine / CI). All public methods are always safe to call.

SPI wiring:
  port=0, device=0, DC=GPIO24, RST=GPIO25, bus_speed=16 MHz
"""

import logging
import threading

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
BG        = "black"
WHITE     = "white"
GREEN     = "#00e676"
DK_GREEN  = "#1b5e20"
YELLOW    = "#ffd600"
CYAN      = "#00e5ff"
GREY      = "#555555"
LT_GREY   = "#888888"
RED       = "#ff5252"
DK_RED    = "#b71c1c"
ORANGE    = "#ff9100"

# ── Display geometry ──────────────────────────────────────────────────────────
WIDTH  = 160
HEIGHT = 128

# ── Font path ─────────────────────────────────────────────────────────────────
_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_PATH_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _load_font(size: int, bold: bool = True) -> "ImageFont.FreeTypeFont":
    """Return a font — falls back to default if font file missing."""
    try:
        path = _FONT_PATH if bold else _FONT_PATH_REG
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


class TFTDisplay:
    """
    High-level display abstraction for the Smart Trolley.
    All public methods are safe to call even when luma is unavailable.
    """

    def __init__(self):
        self._device = None
        self._lock   = threading.Lock()   # protect concurrent render calls
        if not _LUMA_AVAILABLE:
            return
        try:
            serial = spi(
                port=0, device=0,
                gpio_DC=24, gpio_RST=25,
                bus_speed_hz=16_000_000,
            )
            self._device = st7735(
                serial,
                width=WIDTH, height=HEIGHT,
                rotate=2, bgr=True,
            )
            logger.info("TFT display initialised (%dx%d)", WIDTH, HEIGHT)
            self._show_splash()
        except Exception as exc:
            logger.error("TFT init failed: %s", exc)
            self._device = None

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _render(self, image: "Image.Image") -> None:
        """Push a PIL image to the display (thread-safe)."""
        if not self._device:
            return
        with self._lock:
            self._device.display(image)

    def _blank(self, bg: str = BG) -> "tuple[Image.Image, ImageDraw.ImageDraw]":
        img  = Image.new("RGB", (WIDTH, HEIGHT), bg)
        draw = ImageDraw.Draw(img)
        return img, draw

    def _text_width(self, text: str, font) -> int:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]

    def _truncate(self, text: str, font, max_width: int) -> str:
        """Trim text to fit within max_width pixels."""
        while len(text) > 0:
            if self._text_width(text, font) <= max_width:
                return text
            text = text[: len(text) - 1]
        return ""

    def _header(self, draw: "ImageDraw.ImageDraw", label: str,
                 bg: str = DK_GREEN, fg: str = GREEN, size: int = 13) -> None:
        """Draw a coloured header bar at the top."""
        draw.rectangle([(0, 0), (WIDTH, 22)], fill=bg)
        draw.text((6, 4), label, font=_load_font(size), fill=fg)

    def _footer_total(self, draw: "ImageDraw.ImageDraw",
                      total: float, y: int = 108) -> None:
        """Draw 'Total  ₹X.XX' footer bar at the bottom."""
        draw.rectangle([(0, y), (WIDTH, HEIGHT)], fill="#111111")
        draw.text((6, y + 2),   "Total",     font=_load_font(10), fill=GREY)
        draw.text((60, y + 2),  f"\u20b9{total:.2f}", font=_load_font(12), fill=YELLOW)

    def _schedule(self, delay: float, fn, *args, **kwargs) -> None:
        """Call fn(*args, **kwargs) after delay seconds (daemon thread)."""
        t = threading.Timer(delay, fn, args=args, kwargs=kwargs)
        t.daemon = True
        t.start()

    # ─────────────────────────────────────────────────────────────────────────
    # Boot / idle
    # ─────────────────────────────────────────────────────────────────────────

    def _show_splash(self) -> None:
        """Idle splash — called at boot and after cart clear/payment."""
        if not _LUMA_AVAILABLE:
            return
        img, draw = self._blank()
        draw.text((22, 20),  "SMART",           font=_load_font(22), fill=WHITE)
        draw.text((22, 48),  "TROLLEY",          font=_load_font(22), fill=YELLOW)
        draw.line([(0, 78), (WIDTH, 78)],        fill=GREY, width=1)
        draw.text((14, 84), "Scan item to begin", font=_load_font(11, bold=False), fill=CYAN)
        draw.text((30, 102), "\u2022 Ready \u2022",         font=_load_font(11), fill=GREEN)
        self._render(img)

    def show_idle_scan(self, item_count: int = 0, subtotal: float = 0.0) -> None:
        """Idle screen for scan mode with optional cart summary footer."""
        if not _LUMA_AVAILABLE:
            logger.info("[TFT IDLE] items=%d subtotal=₹%.2f", item_count, subtotal)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            draw.text((22, 20), "SMART", font=_load_font(22), fill=WHITE)
            draw.text((22, 48), "TROLLEY", font=_load_font(22), fill=YELLOW)
            draw.line([(0, 78), (WIDTH, 78)], fill=GREY, width=1)
            draw.text((14, 84), "Scan your items", font=_load_font(11, bold=False), fill=CYAN)
            if item_count > 0:
                draw.text((6, 104), f"Items: {item_count}", font=_load_font(10, bold=False), fill=LT_GREY)
                draw.text((78, 102), f"₹{subtotal:.2f}", font=_load_font(12), fill=YELLOW)
            else:
                draw.text((30, 102), "\u2022 Ready \u2022", font=_load_font(11), fill=GREEN)
            self._render(img)
        except Exception as exc:
            logger.error("TFT show_idle_scan: %s", exc)

    def show_scan_product_card(
        self,
        name: str,
        price: float,
        expected_weight_g: float | int | None,
        measured_weight_g: float | int | None,
        cart_count: int,
        cart_subtotal: float,
    ) -> None:
        """Render a scan result card with weight and cart summary."""
        if not _LUMA_AVAILABLE:
            logger.info(
                "[TFT SCAN] %s ₹%.2f exp=%sg got=%sg items=%d subtotal=₹%.2f",
                name,
                price,
                expected_weight_g,
                measured_weight_g,
                cart_count,
                cart_subtotal,
            )
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            self._header(draw, "SCAN OK", "#003b2f", GREEN, 13)

            short = self._truncate(name, _load_font(13), WIDTH - 10)
            draw.text((6, 28), short, font=_load_font(13), fill=WHITE)
            draw.text((6, 45), f"₹{price:.2f}", font=_load_font(14), fill=YELLOW)

            exp = "-" if expected_weight_g is None else str(int(expected_weight_g))
            got = "-" if measured_weight_g is None else str(int(measured_weight_g))
            draw.text((6, 64), f"Exp: {exp} g", font=_load_font(10, bold=False), fill=LT_GREY)
            draw.text((82, 64), f"Now: {got} g", font=_load_font(10, bold=False), fill=CYAN)

            draw.line([(0, 88), (WIDTH, 88)], fill=GREY, width=1)
            draw.text((6, 94), f"Items: {cart_count}", font=_load_font(10, bold=False), fill=LT_GREY)
            draw.text((78, 92), f"₹{cart_subtotal:.2f}", font=_load_font(13), fill=YELLOW)
            self._render(img)
        except Exception as exc:
            logger.error("TFT show_scan_product_card: %s", exc)

    def show_product_not_found(self, barcode: str) -> None:
        """Render a temporary not-found card for unknown barcodes."""
        if not _LUMA_AVAILABLE:
            logger.info("[TFT MISS] barcode=%s", barcode)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            self._header(draw, "NOT FOUND", DK_RED, RED, 14)
            draw.text((6, 36), "Product not found", font=_load_font(12), fill=WHITE)
            short_code = self._truncate(barcode, _load_font(11, bold=False), WIDTH - 10)
            draw.text((6, 56), short_code, font=_load_font(11, bold=False), fill=LT_GREY)
            draw.text((6, 94), "Try scanning again", font=_load_font(10, bold=False), fill=ORANGE)
            self._render(img)
        except Exception as exc:
            logger.error("TFT show_product_not_found: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Product Added
    # ─────────────────────────────────────────────────────────────────────────

    def show_product_added(
        self,
        name: str,
        price: float,
        qty: int,
        cart_total: float,
        cart_count: int = 0,
        then_show_cart: bool = True,
    ) -> None:
        """
        Green confirmation card when a product is added.
        Optionally transitions to cart list after 2 s.

        Layout:
          ┌──────────────────────┐
          │ ✓ ADDED              │  green header
          │ <Name>               │
          │ ₹price  ×  qty       │
          │ ─────────────────── │
          │ Cart Total           │
          │ ₹total    N items    │
          └──────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info("[TFT+] %s ₹%.2f×%d | total ₹%.2f", name, price, qty, cart_total)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            self._header(draw, "\u2713 ADDED", DK_GREEN, GREEN, 15)

            short = self._truncate(name, _load_font(13), WIDTH - 10)
            draw.text((6, 28), short, font=_load_font(13), fill=WHITE)
            draw.text((6, 46), f"\u20b9{price:.2f}  \u00d7  {qty}", font=_load_font(13), fill=CYAN)

            draw.line([(0, 68), (WIDTH, 68)], fill=GREY, width=1)
            draw.text((6, 73), "Cart Total",           font=_load_font(10, bold=False), fill=GREY)
            draw.text((6, 87), f"\u20b9{cart_total:.2f}", font=_load_font(16), fill=YELLOW)

            if cart_count:
                lbl = f"{cart_count} item{'s' if cart_count != 1 else ''}"
                draw.text((96, 91), lbl, font=_load_font(10, bold=False), fill=LT_GREY)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_product_added: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Product Removed
    # ─────────────────────────────────────────────────────────────────────────

    def show_product_removed(
        self,
        name: str,
        cart_total: float,
        cart_count: int = 0,
    ) -> None:
        """
        Red card when a product is removed.

        Layout:
          ┌──────────────────────┐
          │ ✕ REMOVED            │  red header
          │ <Name>               │
          │ ─────────────────── │
          │ Cart Total           │
          │ ₹total    N items    │
          └──────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info("[TFT-] %s removed | total ₹%.2f", name, cart_total)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            self._header(draw, "\u2715 REMOVED", DK_RED, RED, 15)

            short = self._truncate(name, _load_font(13), WIDTH - 10)
            draw.text((6, 28), short, font=_load_font(13), fill=LT_GREY)

            # Strike-through line over the name
            bbox = _load_font(13).getbbox(short)
            mid_y = 28 + (bbox[3] - bbox[1]) // 2
            draw.line([(6, mid_y), (6 + (bbox[2] - bbox[0]), mid_y)], fill=RED, width=2)

            draw.line([(0, 56), (WIDTH, 56)], fill=GREY, width=1)
            draw.text((6, 62), "Cart Total",            font=_load_font(10, bold=False), fill=GREY)

            if cart_count == 0:
                draw.text((6, 76), "Cart empty",            font=_load_font(14), fill=ORANGE)
            else:
                draw.text((6, 76), f"\u20b9{cart_total:.2f}", font=_load_font(16), fill=YELLOW)
                lbl = f"{cart_count} item{'s' if cart_count != 1 else ''}"
                draw.text((96, 80), lbl, font=_load_font(10, bold=False), fill=LT_GREY)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_product_removed: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Cart List
    # ─────────────────────────────────────────────────────────────────────────

    def show_cart_list(
        self,
        items: list,
        cart_total: float,
    ) -> None:
        """
        Compact list of all cart items on the TFT.
        Shows up to 5 rows; if more, the last row says '+ N more…'

        Layout (160×128):
          ┌──────────────────────┐
          │ CART  (N items)      │  cyan header
          │ Name         qty ₹p  │  row 1
          │ Name         qty ₹p  │  row 2
          │  …up to 5 rows…      │
          │ ─────────────────── │
          │ Total         ₹X.XX  │  footer
          └──────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info("[TFT CART] %d items | total ₹%.2f", len(items), cart_total)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()

            # Header
            count = len(items)
            draw.rectangle([(0, 0), (WIDTH, 20)], fill="#004d5e")
            draw.text((6, 3), f"CART  ({count} item{'s' if count != 1 else ''})",
                      font=_load_font(12), fill=CYAN)

            # Rows
            f_row   = _load_font(10, bold=False)
            f_row_b = _load_font(10)
            row_h   = 18
            y_start = 24
            max_rows = 4
            footer_y = HEIGHT - 22

            visible   = items[:max_rows]
            overflow  = len(items) - max_rows

            for i, item in enumerate(visible):
                y = y_start + i * row_h
                # Name (truncated)
                name_short = self._truncate(item["name"], f_row, 90)
                draw.text((4, y), name_short, font=f_row, fill=WHITE)
                # Qty × price on the right
                right_label = f"{item['quantity']}\u00d7\u20b9{item['price']:.0f}"
                rw = self._text_width(right_label, f_row_b)
                draw.text((WIDTH - rw - 4, y), right_label, font=f_row_b, fill=CYAN)

            if overflow > 0:
                y = y_start + max_rows * row_h
                draw.text((4, y), f"+ {overflow} more\u2026", font=f_row, fill=ORANGE)

            # Divider + footer
            draw.line([(0, footer_y - 2), (WIDTH, footer_y - 2)], fill=GREY, width=1)
            draw.rectangle([(0, footer_y), (WIDTH, HEIGHT)], fill="#111111")
            draw.text((4,  footer_y + 3), "Total", font=_load_font(11), fill=GREY)
            draw.text((60, footer_y + 3), f"\u20b9{cart_total:.2f}",
                      font=_load_font(13), fill=YELLOW)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_cart_list: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Payment QR Code
    # ─────────────────────────────────────────────────────────────────────────

    def show_payment_qr(
        self,
        total: float,
        qr_pil_image: "Image.Image",
    ) -> None:
        """
        Render the UPI QR code on the TFT for payment.

        Layout (160×128):
          ┌──────────────────────┐
          │ SCAN TO PAY          │  yellow header
          │   ┌──────────┐       │
          │   │  QR CODE │       │  90×90 centred
          │   └──────────┘       │
          │   ₹TOTAL             │  white, below QR
          └──────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info("[TFT QR] total ₹%.2f — QR display skipped (no luma)", total)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()

            # Header
            draw.rectangle([(0, 0), (WIDTH, 22)], fill="#4a3a00")
            draw.text((6, 4), "SCAN TO PAY", font=_load_font(14), fill=YELLOW)

            # Scale QR to fit nicely — 90×90 px, centred horizontally
            qr_size = 90
            qr_resized = qr_pil_image.convert("RGB").resize(
                (qr_size, qr_size), Image.NEAREST
            )
            qr_x = (WIDTH - qr_size) // 2
            img.paste(qr_resized, (qr_x, 24))

            # Amount below QR
            amount_str = f"\u20b9{total:.2f}"
            aw = self._text_width(amount_str, _load_font(16))
            draw.text(((WIDTH - aw) // 2, 118), amount_str,
                      font=_load_font(16), fill=WHITE)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_payment_qr: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Payment Success
    # ─────────────────────────────────────────────────────────────────────────

    def show_payment_success(
        self,
        total: float,
        receipt_id: str = "",
    ) -> None:
        """
        Full-screen payment confirmed card (green). Auto-returns to idle
        splash — call show_cart_cleared() after the desired display time.

        Layout:
          ┌──────────────────────┐
          │  PAYMENT SUCCESS     │  green header
          │  ✔            PAID   │  big tick + label
          │               ₹X.XX  │
          │  ────────────────── │
          │  Thank you!          │  yellow
          │  receipt-id …        │  grey small
          └──────────────────────┘
        """
        if not _LUMA_AVAILABLE:
            logger.info("[TFT ✔] Payment ₹%.2f  receipt=%s", total, receipt_id)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            self._header(draw, "PAYMENT SUCCESS", DK_GREEN, GREEN, 13)

            draw.text((10, 26), "\u2714", font=_load_font(40), fill=GREEN)

            draw.text((72, 30), "PAID",          font=_load_font(13), fill=WHITE)
            draw.text((72, 48), f"\u20b9{total:.2f}", font=_load_font(15), fill=WHITE)

            draw.line([(0, 78), (WIDTH, 78)], fill=GREY, width=1)
            draw.text((14, 84), "Thank you!", font=_load_font(16), fill=YELLOW)

            if receipt_id:
                short = self._truncate(receipt_id, _load_font(10, bold=False), WIDTH - 10)
                draw.text((6, 110), short, font=_load_font(10, bold=False), fill=GREY)

            self._render(img)
        except Exception as exc:
            logger.error("TFT show_payment_success: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Cart Cleared / Idle
    # ─────────────────────────────────────────────────────────────────────────

    def show_cart_cleared(self) -> None:
        """Return to idle splash (after payment or manual clear)."""
        if not _LUMA_AVAILABLE:
            logger.info("[TFT] Idle splash")
            return
        self._show_splash()

    # ─────────────────────────────────────────────────────────────────────────
    # Error
    # ─────────────────────────────────────────────────────────────────────────

    def show_error(self, message: str) -> None:
        """Show a brief error banner."""
        if not _LUMA_AVAILABLE:
            logger.info("[TFT ERR] %s", message)
            return
        if not self._device:
            return
        try:
            img, draw = self._blank()
            self._header(draw, "! ERROR", DK_RED, RED, 15)
            short = self._truncate(message, _load_font(12), WIDTH - 10)
            draw.text((6, 32), short, font=_load_font(12), fill=WHITE)
            self._render(img)
        except Exception as exc:
            logger.error("TFT show_error: %s", exc)
