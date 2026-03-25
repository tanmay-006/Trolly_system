# Requirements: Smart Trolley Checkout System

**Defined:** 2026-03-25
**Core Value:** A customer can scan items, pay via QR code, and get a printed receipt entirely on the trolley — no cashier needed.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure & Setup

- [ ] **INFRA-01**: Monorepo structure scaffolded (`pi_runtime/`, `web/`, `prisma/`, `.env`)
- [ ] **INFRA-02**: Prisma schema defines `products` table with all specified fields (id, barcode, name, price, weight_grams, category, image_url, stock, created_at, updated_at)
- [ ] **INFRA-03**: Prisma schema defines `transactions` table (id, session_id, items JSONB, total_amount, payment_status enum, payment_method, upi_ref, created_at)
- [ ] **INFRA-04**: Schema migrated to Neon PostgreSQL via `prisma migrate dev --name init`
- [ ] **INFRA-05**: Python runtime reads all config from `.env` via python-dotenv (DATABASE_URL, UPI_ID, SHOP_NAME, BLUETOOTH_PRINTER_MAC)
- [ ] **INFRA-06**: `pi_runtime/requirements.txt` lists all Pi dependencies (picamera2, pyzbar, Pillow, psycopg2-binary, python-dotenv, luma.lcd, hx711, python-escpos, qrcode)

### Pi Runtime — Idle & Scanning

- [ ] **SCAN-01**: System displays "Scan your items" idle screen on ST7735 TFT via luma.lcd at startup
- [ ] **SCAN-02**: Camera captures frames continuously via picamera2 and decodes barcodes via pyzbar
- [ ] **SCAN-03**: Detected barcode is looked up in Neon `products` table via psycopg2
- [ ] **SCAN-04**: Matched product is added to in-memory session cart
- [ ] **SCAN-05**: TFT displays product card (name, price, expected weight) after successful scan
- [ ] **SCAN-06**: TFT displays "Product not found" for 2 seconds then resumes scanning when barcode unknown
- [ ] **SCAN-07**: HX711 load sensor reads current tray weight after each scan
- [ ] **SCAN-08**: Running cart summary (item count, subtotal in ₹) shown at TFT bottom

### Pi Runtime — Checkout & Payment

- [ ] **PAY-01**: DONE trigger (physical button or special barcode) initiates checkout stage
- [ ] **PAY-02**: UPI deeplink QR code generated: `upi://pay?pa=<UPI_ID>&pn=SmartTrolley&am=<TOTAL>&cu=INR&tn=<SESSION_ID>`
- [ ] **PAY-03**: QR code rendered as PIL Image and displayed full-screen on TFT
- [ ] **PAY-04**: TFT shows "Scan to Pay ₹<TOTAL>" alongside QR code
- [ ] **PAY-05**: Manual confirm (button or endpoint poll) marks session as paid
- [ ] **PAY-06**: Transaction record written to Neon `transactions` table with payment_status = 'paid'

### Pi Runtime — Invoice Printing

- [ ] **PRNT-01**: ESC/POS receipt formatted with shop name header, date/time, item list (name, qty, unit price, line total), separator, total, "Thank you" footer
- [ ] **PRNT-02**: Receipt sent to Bluetooth printer via python-escpos using MAC from `.env`
- [ ] **PRNT-03**: Session cart cleared and system returns to idle after successful print

### Next.js Admin Panel

- [ ] **ADMIN-01**: `/admin/products` shows paginated product table (barcode, name, price, stock, category) with search and category filter
- [ ] **ADMIN-02**: Each product row has Edit and Delete action buttons
- [ ] **ADMIN-03**: `/admin/products/new` form adds a new product via Server Action (all schema fields)
- [ ] **ADMIN-04**: `/admin/products/[id]` provides pre-filled edit form with save via Server Action
- [ ] **ADMIN-05**: Delete product triggers confirmation dialog before Server Action deletion
- [ ] **ADMIN-06**: `/admin/transactions` shows read-only log (session_id, total_amount, payment_status, created_at) with expandable JSONB items

## v2 Requirements

### Admin Panel Enhancements

- **ADMIN-V2-01**: Authentication for admin panel (currently no auth for v1)
- **ADMIN-V2-02**: Product image upload (v1 uses image_url text field only)
- **ADMIN-V2-03**: Low stock alerts and inventory management dashboard

### Pi Runtime Enhancements

- **PRNT-V2-01**: QR code of transaction ID printed on receipt (optional small QR)
- **SCAN-V2-01**: Weight verification — compare HX711 reading against product expected_weight with tolerance check, alert on mismatch
- **PAY-V2-01**: Automatic payment confirmation via webhook instead of manual confirm

## Out of Scope

| Feature | Reason |
|---------|--------|
| Razorpay payment gateway | Using direct UPI deeplink QR; Razorpay adds cost and complexity for v1 |
| Legacy `pos_app.py` Flask app | Superseded by new Pi runtime + admin panel architecture |
| Video streaming from Pi camera | Camera frames used only for barcode decode, no streaming needed |
| Real-time WebSocket updates | Admin panel is management tool, not live dashboard |
| Multi-store / multi-user support | Single shop deployment for v1 |
| OAuth / SSO authentication | Internal tool; plain auth sufficient if added in v2 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| INFRA-03 | Phase 1 | Pending |
| INFRA-04 | Phase 1 | Pending |
| INFRA-05 | Phase 1 | Pending |
| INFRA-06 | Phase 1 | Pending |
| SCAN-01 | Phase 2 | Pending |
| SCAN-02 | Phase 2 | Pending |
| SCAN-03 | Phase 2 | Pending |
| SCAN-04 | Phase 2 | Pending |
| SCAN-05 | Phase 2 | Pending |
| SCAN-06 | Phase 2 | Pending |
| SCAN-07 | Phase 2 | Pending |
| SCAN-08 | Phase 2 | Pending |
| PAY-01 | Phase 3 | Pending |
| PAY-02 | Phase 3 | Pending |
| PAY-03 | Phase 3 | Pending |
| PAY-04 | Phase 3 | Pending |
| PAY-05 | Phase 3 | Pending |
| PAY-06 | Phase 3 | Pending |
| PRNT-01 | Phase 4 | Pending |
| PRNT-02 | Phase 4 | Pending |
| PRNT-03 | Phase 4 | Pending |
| ADMIN-01 | Phase 5 | Pending |
| ADMIN-02 | Phase 5 | Pending |
| ADMIN-03 | Phase 5 | Pending |
| ADMIN-04 | Phase 5 | Pending |
| ADMIN-05 | Phase 5 | Pending |
| ADMIN-06 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 29 total
- Mapped to phases: 29
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-25*
*Last updated: 2026-03-25 after initial definition*
