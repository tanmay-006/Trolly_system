#!/usr/bin/env python3
"""
GPIO button test for Raspberry Pi.

Default wiring:
- One side of button to physical pin 11 (GPIO17)
- Other side of button to physical pin 39 (GND)

This script uses an internal pull-up resistor.
Idle state is HIGH (released), pressed state is LOW.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

try:
    import RPi.GPIO as GPIO
except Exception as exc:  # pragma: no cover - hardware import guard
    print(f"ERROR: Could not import RPi.GPIO: {exc}")
    print("Run this on a Raspberry Pi with RPi.GPIO installed.")
    raise SystemExit(1)


def _level_text(level: int) -> str:
    return "LOW (PRESSED)" if level == 0 else "HIGH (RELEASED)"


def _timestamp() -> str:
    return time.strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test a physical GPIO button")
    parser.add_argument(
        "--mode",
        choices=("board", "bcm"),
        default="board",
        help="Pin numbering mode. board=physical pin numbers, bcm=GPIO numbers.",
    )
    parser.add_argument(
        "--pin",
        type=int,
        default=11,
        help="Input pin number for selected mode (default: 11 for board mode).",
    )
    parser.add_argument(
        "--poll-ms",
        type=float,
        default=20.0,
        help="Polling interval in milliseconds.",
    )
    parser.add_argument(
        "--bouncetime-ms",
        type=int,
        default=200,
        help="Debounce time for edge events.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=2.0,
        help="How often to print a status line even if state does not change.",
    )
    args = parser.parse_args()

    pin_mode = GPIO.BOARD if args.mode == "board" else GPIO.BCM

    GPIO.setwarnings(False)
    GPIO.setmode(pin_mode)
    GPIO.setup(args.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    running = True

    def _stop(_sig=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    event_count = 0

    def _edge_callback(channel: int):
        nonlocal event_count
        event_count += 1
        level = GPIO.input(channel)
        print(
            f"[{_timestamp()}] EDGE #{event_count}: pin={channel} level={level} {_level_text(level)}",
            flush=True,
        )

    try:
        GPIO.add_event_detect(
            args.pin,
            GPIO.BOTH,
            callback=_edge_callback,
            bouncetime=max(1, args.bouncetime_ms),
        )
    except Exception as exc:
        print(f"[{_timestamp()}] Warning: add_event_detect failed: {exc}")
        print("Polling will still continue.")

    initial = GPIO.input(args.pin)
    print("=" * 64)
    print("GPIO Button Test")
    print(f"Mode: {args.mode.upper()}  Pin: {args.pin}")
    if args.mode == "board" and args.pin == 11:
        print("Expected wiring: physical pin 11 <-> button <-> physical pin 39 (GND)")
    elif args.mode == "bcm" and args.pin == 17:
        print("Expected wiring: BCM17 <-> button <-> GND")
    print("Pull-up: INTERNAL (PUD_UP)")
    print(f"Initial state: {initial} {_level_text(initial)}")
    print("Press/release button and watch for transitions.")
    print("Press Ctrl+C to exit.")
    print("=" * 64)

    prev = initial
    heartbeat_at = time.monotonic() + max(0.2, args.heartbeat_seconds)
    poll_sleep = max(0.005, args.poll_ms / 1000.0)

    while running:
        cur = GPIO.input(args.pin)
        if cur != prev:
            print(
                f"[{_timestamp()}] POLL transition: {prev} {_level_text(prev)} -> {cur} {_level_text(cur)}",
                flush=True,
            )
            prev = cur

        now = time.monotonic()
        if now >= heartbeat_at:
            print(
                f"[{_timestamp()}] HEARTBEAT: level={cur} {_level_text(cur)} events={event_count}",
                flush=True,
            )
            heartbeat_at = now + max(0.2, args.heartbeat_seconds)

        time.sleep(poll_sleep)

    try:
        GPIO.remove_event_detect(args.pin)
    except Exception:
        pass
    GPIO.cleanup([args.pin])
    print(f"[{_timestamp()}] Stopped. Total edge events: {event_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
