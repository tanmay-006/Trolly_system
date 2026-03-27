---
title: Completed Phase 2 Pi Runtime (Idle, Scan, Display)
---
# Summary: Phase 2 Pi Runtime Implemented

Phase 2 runtime capabilities are now implemented in this repository.

## Changes Made
- Added [main.py](../../../main.py):
  - Startup idle screen rendering on TFT via `display.show_idle_scan(...)`.
  - Continuous barcode scan loop using camera libraries when available.
  - Product lookup from Neon (`products` table) by barcode.
  - In-memory cart updates with running item count and subtotal.
  - HX711 weight read after successful scans, logged to console.
  - Unknown barcode path displays a temporary "Product not found" card for 2 seconds, then returns to idle.
  - Dependency/hardware fallbacks for non-Pi environments.

- Updated [tft_display.py](../../../tft_display.py):
  - Added `show_idle_scan(item_count, subtotal)`.
  - Added `show_scan_product_card(...)` with expected/current weight display.
  - Added `show_product_not_found(barcode)`.

- Updated [README.md](../../../README.md):
  - Added Phase 2 run instructions (`python main.py`) and behavior notes.

## Validation Notes
- Syntax check succeeded for edited Python files using `python -m py_compile`.
- Runtime start command shows a clear dependency message in this environment because `psycopg2-binary` is not installed here.
- On Raspberry Pi with required dependencies installed, the scan loop is ready for hardware validation.
