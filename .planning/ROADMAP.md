# Roadmap: Smart Trolley Checkout System

**Milestone 1** | 5 phases | 29 requirements | All v1 requirements covered ✓

---

## Phase 1: Monorepo Scaffold & Database Schema

**Goal:** Set up the full project structure, Prisma schema, and push migrations to Neon PostgreSQL. Both surfaces have their skeleton; shared database is live.

**Requirements:** INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06

**UI hint:** no

**Success Criteria:**
1. `pi_runtime/`, `web/`, `prisma/` directories exist with correct file skeletons
2. `prisma/schema.prisma` defines both `products` and `transactions` models with correct types
3. `prisma migrate dev --name init` runs successfully and tables appear in Neon
4. `pi_runtime/requirements.txt` contains all Pi dependencies
5. `.env` file template documented in README with all required variables

---

## Phase 2: Pi Runtime — Idle, Scan, and Display

**Goal:** The Raspberry Pi can boot, show an idle screen on the TFT, scan barcodes with the camera, look up products in Neon, display product cards, read weight from the HX711, and maintain a live cart summary on screen.

**Requirements:** SCAN-01, SCAN-02, SCAN-03, SCAN-04, SCAN-05, SCAN-06, SCAN-07, SCAN-08

**UI hint:** no

**Success Criteria:**
1. TFT displays "Scan your items" idle screen on `python main.py` startup
2. Pointing camera at barcode triggers a product lookup within 2 seconds
3. Scanned product card (name, price) renders on TFT; "not found" message shown for unknown barcodes
4. HX711 reads a weight value (in grams) after each scan; value logged to console
5. Cart summary (count, subtotal) updates visibly on TFT after each scan

---

## Phase 3: Pi Runtime — Checkout & UPI Payment

**Goal:** A DONE trigger moves the system from scanning to payment mode: generates a UPI QR code, displays it full-screen on the TFT, and allows manual confirmation that writes the transaction to Neon.

**Requirements:** PAY-01, PAY-02, PAY-03, PAY-04, PAY-05, PAY-06

**UI hint:** no

**Success Criteria:**
1. DONE trigger (button press or special barcode) transitions from scan stage to payment stage
2. UPI deeplink QR code renders full-screen on TFT within 1 second of DONE trigger
3. TFT shows "Scan to Pay ₹<TOTAL>" text alongside QR
4. Manual confirm (button or keystroke) marks session as paid
5. `transactions` row exists in Neon with correct items, total, and `payment_status = 'paid'`

---

## Phase 4: Pi Runtime — Bluetooth Invoice Printing & Session Reset

**Goal:** After payment confirmation, the Pi prints a formatted ESC/POS receipt to the Bluetooth printer, then resets the session and returns to idle.

**Requirements:** PRNT-01, PRNT-02, PRNT-03

**UI hint:** no

**Success Criteria:**
1. Bluetooth printer (paired MAC from `.env`) receives the print job without error
2. Printed receipt contains: shop name, date/time, each item (name · qty · price · line total), subtotal separator, total, "Thank you" footer
3. After print, cart is cleared and TFT returns to idle "Scan your items" screen
4. Full end-to-end flow (scan → pay → print → idle) completes in a single run

---

## Phase 5: Next.js Admin Panel

**Goal:** A deployed Next.js 15 app lets the shop owner manage products and view transactions using Server Actions, Prisma, and shadcn/ui — all pointing at the same Neon database.

**Requirements:** ADMIN-01, ADMIN-02, ADMIN-03, ADMIN-04, ADMIN-05, ADMIN-06

**UI hint:** yes

**Success Criteria:**
1. `/admin/products` renders paginated product table with search and category filter working
2. "New Product" form saves a product via Server Action; product appears in Neon `products` table
3. "Edit" button opens pre-filled form; saving updates the record in Neon
4. "Delete" button opens confirmation dialog; confirming removes the record
5. `/admin/transactions` shows the transaction log with expandable JSONB items for each row

---

## Backlog (999.x)

*(empty)*

---
*Roadmap created: 2026-03-25*
*Last updated: 2026-03-25 after initialization*
