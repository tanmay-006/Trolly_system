# Smart Trolley Checkout System

## What This Is

A self-contained smart trolley checkout system running on a Raspberry Pi 4B. Customers walk around a store, scan products with the Pi's camera, see live cart totals on a TFT display, pay via UPI QR code, and receive a Bluetooth-printed receipt — all without a cashier. A separate Next.js admin panel lets the shop owner manage the product catalog via a cloud-hosted Neon PostgreSQL database.

## Core Value

A customer can scan items, pay via QR code, and get a printed receipt entirely on the trolley — no cashier needed.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ Flask POS prototype with scan, cart, and Razorpay QR — existing code (pos_app.py)

### Active

<!-- Current scope. Building toward these. -->

**Pi Hardware Runtime**
- [ ] System displays idle "Scan your items" screen on TFT (ST7735 via luma.lcd)
- [ ] Camera captures frames continuously and decodes barcodes via pyzbar
- [ ] Successful barcode scan queries Neon PostgreSQL products table via psycopg2
- [ ] Product card (name, price, weight) rendered on TFT after scan
- [ ] HX711 load sensor reads weight after each scan
- [ ] Running cart summary (item count, subtotal) shown at TFT bottom
- [ ] DONE trigger (physical button or special barcode) initiates checkout
- [ ] UPI QR code generated and rendered on TFT display (PIL Image → ST7735)
- [ ] Manual confirm or poll endpoint marks session as paid
- [ ] Transaction written to Neon `transactions` table on payment
- [ ] ESC/POS invoice printed to Bluetooth printer via python-escpos
- [ ] Session resets and returns to idle after print

**Next.js Admin Panel**
- [ ] Admin can view paginated product list with search and category filter
- [ ] Admin can add a new product (barcode, name, price, weight, category, image, stock)
- [ ] Admin can edit an existing product
- [ ] Admin can delete a product with confirmation dialog
- [ ] Admin can view read-only transaction log with expandable JSONB items

**Infrastructure**
- [ ] Prisma schema defines `products` and `transactions` tables
- [ ] Schema migrated to Neon PostgreSQL via `prisma migrate dev`
- [ ] Monorepo structure: `pi_runtime/`, `web/`, `prisma/`, `.env`
- [ ] Python runtime reads config/secrets from `.env` via python-dotenv
- [ ] Next.js app reads secrets from `.env.local`

### Out of Scope

- Authentication for admin panel — simple internal tool, can be added later
- Real-time payment webhook — manual confirm button used for v1
- Video streaming from camera — frames only used for barcode decode
- Legacy `pos_app.py` Flask app — superseded by new architecture

## Context

- Existing prototype: `pos_app.py` (Flask, Razorpay, in-memory cart) — partially demonstrates the payment flow but is x86 only and not hardware-integrated
- Target hardware: Raspberry Pi 4B (ARM64, Linux)
- Device OS: Raspberry Pi OS (Debian-based, uses `apt`, not `dnf`)
- Development machine: Fedora x86_64 — Pi-specific packages (RPi.GPIO, picamera2) cannot be installed here; dev/test non-hardware code only
- Database: Neon PostgreSQL (cloud-hosted, connection pooling via pgbouncer URL)
- Payment: UPI deeplink QR, no Razorpay dependency in pi_runtime (Razorpay only used for order creation in old prototype)

## Constraints

- **Hardware**: Pi 4B ARM64 — runtime must be tested on Pi; some packages (picamera2, luma.lcd) are Pi-only
- **Display**: ST7735 TFT, 128×160 or 160×128 px — very limited screen real estate, UI must be simple
- **Database**: Neon free tier — connection pooling URL must be used; avoid long-lived connections
- **Stack (web)**: Next.js 15 App Router, TypeScript, Prisma, Tailwind, shadcn/ui — no REST API needed (Server Actions)
- **Stack (pi)**: picamera2, pyzbar, luma.lcd (st7735 driver), hx711, python-escpos, psycopg2-binary, python-dotenv, qrcode, Pillow

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| psycopg2 (not SQLAlchemy) for Pi runtime | Minimal deps, direct control, no ORM overhead on embedded system | — Pending |
| Prisma ORM for Next.js web app | Type-safe, co-located with UI via Server Actions, migration tooling | — Pending |
| luma.lcd for ST7735 | Active library, Python 3 support, Pi-compatible | — Pending |
| Server Actions over REST API | Internal tool, type-safe mutations, less boilerplate | — Pending |
| Monorepo (single repo, two surfaces) | Shared .env, easier to manage for a solo shop owner | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-25 after initialization*
