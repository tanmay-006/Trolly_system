---
title: Phase 2 Pi Runtime (Idle, Scan, Display)
---
# Plan: Implement Phase 2 Pi Runtime

This quick task delivers the Phase 2 runtime loop for Raspberry Pi scanning.

## Proposed Changes
- Add `main.py` as the Pi runtime entrypoint for idle -> scan -> cart-summary loop.
- Integrate barcode scanning using `picamera2` + `pyzbar` with stdin fallback for non-Pi/dev.
- Query products from Neon PostgreSQL by barcode using `psycopg2`.
- Add HX711 weight reads after each successful scan with graceful fallback.
- Extend TFT display helper with idle scan screen, product scan card, and not-found screen.
- Update README with `python main.py` Phase 2 run instructions.
