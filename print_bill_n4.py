#!/usr/bin/env python3
import argparse
import os
import socket
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont


CRC8_TABLE = [
    0x00,0x07,0x0e,0x09,0x1c,0x1b,0x12,0x15,0x38,0x3f,0x36,0x31,0x24,0x23,0x2a,0x2d,
    0x70,0x77,0x7e,0x79,0x6c,0x6b,0x62,0x65,0x48,0x4f,0x46,0x41,0x54,0x53,0x5a,0x5d,
    0xe0,0xe7,0xee,0xe9,0xfc,0xfb,0xf2,0xf5,0xd8,0xdf,0xd6,0xd1,0xc4,0xc3,0xca,0xcd,
    0x90,0x97,0x9e,0x99,0x8c,0x8b,0x82,0x85,0xa8,0xaf,0xa6,0xa1,0xb4,0xb3,0xba,0xbd,
    0xc7,0xc0,0xc9,0xce,0xdb,0xdc,0xd5,0xd2,0xff,0xf8,0xf1,0xf6,0xe3,0xe4,0xed,0xea,
    0xb7,0xb0,0xb9,0xbe,0xab,0xac,0xa5,0xa2,0x8f,0x88,0x81,0x86,0x93,0x94,0x9d,0x9a,
    0x27,0x20,0x29,0x2e,0x3b,0x3c,0x35,0x32,0x1f,0x18,0x11,0x16,0x03,0x04,0x0d,0x0a,
    0x57,0x50,0x59,0x5e,0x4b,0x4c,0x45,0x42,0x6f,0x68,0x61,0x66,0x73,0x74,0x7d,0x7a,
    0x89,0x8e,0x87,0x80,0x95,0x92,0x9b,0x9c,0xb1,0xb6,0xbf,0xb8,0xad,0xaa,0xa3,0xa4,
    0xf9,0xfe,0xf7,0xf0,0xe5,0xe2,0xeb,0xec,0xc1,0xc6,0xcf,0xc8,0xdd,0xda,0xd3,0xd4,
    0x69,0x6e,0x67,0x60,0x75,0x72,0x7b,0x7c,0x51,0x56,0x5f,0x58,0x4d,0x4a,0x43,0x44,
    0x19,0x1e,0x17,0x10,0x05,0x02,0x0b,0x0c,0x21,0x26,0x2f,0x28,0x3d,0x3a,0x33,0x34,
    0x4e,0x49,0x40,0x47,0x52,0x55,0x5c,0x5b,0x76,0x71,0x78,0x7f,0x6a,0x6d,0x64,0x63,
    0x3e,0x39,0x30,0x37,0x22,0x25,0x2c,0x2b,0x06,0x01,0x08,0x0f,0x1a,0x1d,0x14,0x13,
    0xae,0xa9,0xa0,0xa7,0xb2,0xb5,0xbc,0xbb,0x96,0x91,0x98,0x9f,0x8a,0x8d,0x84,0x83,
    0xde,0xd9,0xd0,0xd7,0xc2,0xc5,0xcc,0xcb,0xe6,0xe1,0xe8,0xef,0xfa,0xfd,0xf4,0xf3,
]

PRINT_ROW_CMD = 0xA2
FEED_PAPER_CMD = 0xA1

# Locked to the readable N4 settings from diagnostics.
N4_THRESHOLD = 128
N4_BIT_ORDER = "lsb"
N4_INVERT = False

PRINTER_MAC = os.getenv("PRINTER_MAC", "D9:2C:4C:B4:DA:FF")
PRINTER_PORT = int(os.getenv("PRINTER_PORT", "1"))
WIDTH = max(8, (int(os.getenv("BT_NATIVE_WIDTH", "384")) // 8) * 8)
ROW_DELAY = float(os.getenv("BT_NATIVE_ROW_DELAY_SECONDS", "0.005"))
POST_SEND_DELAY = float(os.getenv("BT_POST_SEND_DELAY_SECONDS", "1.0"))


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC8_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFF


def make_packet(cmd: int, data: list[int] | bytes) -> bytes:
    payload = bytes(data)
    return bytes([0x51, 0x78, cmd, 0x00, len(payload), 0x00]) + payload + bytes([crc8(payload), 0xFF])


def connect_printer() -> socket.socket:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(10)
    sock.connect((PRINTER_MAC, PRINTER_PORT))
    time.sleep(0.8)
    return sock


def _load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _center(text: str, width: int) -> str:
    t = text[:width]
    left = max(0, (width - len(t)) // 2)
    right = max(0, width - len(t) - left)
    return (" " * left) + t + (" " * right)


def _build_invoice_lines(session: dict) -> list[str]:
    line_w = 32
    items = session.get("items", [])
    total = float(session.get("total", 0.0))
    sid = str(session.get("session_id", "-"))
    pid = str(session.get("payment_id", "-"))
    ts = session.get("timestamp")
    if isinstance(ts, datetime):
        ts_text = ts.strftime("%d/%m/%Y  %H:%M:%S")
    else:
        ts_text = str(ts)

    out = []
    out.append(_center("SMART TROLLEY", line_w))
    out.append(_center("N4 IMAGE MODE", line_w))
    out.append("=" * line_w)
    out.append(f"Date/Time : {ts_text}"[:line_w])
    out.append(f"Bill No   : {sid[-8:]}"[:line_w])
    out.append(f"Pay Ref   : {pid[-12:]}"[:line_w])
    out.append("-" * line_w)
    out.append(f"{'Item':<16}{'Qty':>4}  {'Amt':>8}"[:line_w])
    out.append("-" * line_w)

    for item in items:
        name = str(item.get("name", ""))[:16]
        qty = int(item.get("qty", 0))
        unit = float(item.get("price", 0.0))
        amt = float(item.get("line_total", 0.0))
        out.append(f"{name:<16}{qty:>4}  {amt:>7.2f}"[:line_w])
        out.append(f"  @ Rs{unit:.2f} each"[:line_w])

    count = sum(int(i.get("qty", 0)) for i in items)
    out.append("=" * line_w)
    out.append(f"Items     : {count}"[:line_w])
    out.append("-" * line_w)
    out.append(f"TOTAL: Rs{total:.2f}".rjust(line_w)[:line_w])
    out.append("=" * line_w)
    out.append(_center("Thank you for shopping!", line_w))
    out.append("")
    out.append("")
    return out


def build_invoice_image_n4(session: dict) -> Image.Image:
    lines = _build_invoice_lines(session)
    line_h = 28
    top = 10
    img_h = max(180, top * 2 + len(lines) * line_h)

    img = Image.new("L", (WIDTH, img_h), 255)
    draw = ImageDraw.Draw(img)
    font = _load_font(24)

    y = top
    for line in lines:
        draw.text((6, y), line, fill=0, font=font)
        y += line_h

    if N4_INVERT:
        img = img.point(lambda x: 255 - x)

    return img


def image_to_packets_n4(img: Image.Image) -> list[bytes]:
    img_l = img.convert("L")
    packets: list[bytes] = []

    for y in range(img_l.height):
        row_bytes: list[int] = []
        for x in range(0, WIDTH, 8):
            byte = 0
            for bit in range(8):
                px = img_l.getpixel((x + bit, y))
                if px < N4_THRESHOLD:
                    if N4_BIT_ORDER == "lsb":
                        byte |= 1 << bit
                    else:
                        byte |= 1 << (7 - bit)
            row_bytes.append(byte)
        packets.append(make_packet(PRINT_ROW_CMD, row_bytes))

    for _ in range(10):
        packets.append(make_packet(FEED_PAPER_CMD, [25, 0]))
    return packets


def print_bill_n4(session: dict, save_preview: bool = True) -> None:
    image = build_invoice_image_n4(session)
    if save_preview:
        image.save("print_preview_bill_n4.png")

    packets = image_to_packets_n4(image)
    sock = connect_printer()
    try:
        for packet in packets:
            sock.sendall(packet)
            if ROW_DELAY > 0:
                time.sleep(ROW_DELAY)
        time.sleep(POST_SEND_DELAY)
    finally:
        sock.close()


def sample_session() -> dict:
    items = [
        {"name": "Amul Milk 500ml", "qty": 1, "price": 30.0, "line_total": 30.0},
        {"name": "Parle-G Biscuit", "qty": 2, "price": 10.0, "line_total": 20.0},
        {"name": "Tata Salt 1kg", "qty": 1, "price": 22.0, "line_total": 22.0},
    ]
    return {
        "session_id": "N4-SESSION-001",
        "items": items,
        "total": sum(i["line_total"] for i in items),
        "payment_id": "PAY-N4-1234567890",
        "timestamp": datetime.now(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print invoice using locked N4 format")
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="build and save preview image without sending to printer",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session = sample_session()
    image = build_invoice_image_n4(session)
    image.save("print_preview_bill_n4.png")
    print("Preview saved: print_preview_bill_n4.png")

    if args.preview_only:
        print("Preview-only mode. Not printing.")
        return

    print(f"Connecting to printer {PRINTER_MAC}:{PRINTER_PORT} using N4 mode...")
    print_bill_n4(session, save_preview=False)
    print("N4 bill sent to printer.")


if __name__ == "__main__":
    main()