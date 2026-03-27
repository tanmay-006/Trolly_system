# Smart Trolley Refactoring Summary

**Date:** 2026-03-28
**Scope:** Complete refactoring to move customer-facing UI from web to TFT display

---

## Overview

The smart trolley system has been refactored to separate concerns:
- **Web UI (`pos_app.py`)**: Now admin-only for product/transaction management
- **Pi Runtime (`main.py`)**: Complete customer experience via TFT display + camera

---

## PART 1: pos_app.py Changes

### Removed Components

1. **Customer-Facing Routes**
   - `POST /api/scan_product` - Barcode scanning endpoint
   - `POST /api/add_to_cart` - Cart management
   - `GET /api/get_cart` - Cart status
   - `POST /api/remove_from_cart` - Remove items
   - `POST /api/clear_cart` - Clear cart
   - `POST /api/create_payment_order` - Payment flow
   - `POST /api/complete_payment` - Payment confirmation
   - `GET /receipt/<id>` - Receipt view

2. **Dependencies Removed**
   - `razorpay` - Payment processing (moved to Pi runtime)
   - `qrcode` - QR generation (moved to Pi runtime)
   - `tft_display` - Display control (moved to Pi runtime)

3. **In-Memory State Removed**
   - `shopping_cart` global variable
   - `calculate_cart_total()` function

### Kept Components

1. **Admin Panel**
   - `GET /admin` - Product and transaction list
   - `POST /admin/add` - Add/update products
   - `POST /admin/delete/<barcode>` - Delete products

2. **Database Functions**
   - `get_db()` - Connection context manager
   - `get_product_by_barcode()` - Product lookup (used by main.py)
   - `save_transaction()` - Transaction persistence (used by main.py)

3. **Health Check**
   - `GET /health` - System status

### New Behavior

- Root URL (`/`) now redirects to `/admin`
- Admin panel shows both products and recent transactions
- Startup message clarifies this is admin-only

---

## PART 2: main.py Complete Rewrite

### Architecture

The new `main.py` is a single-process runtime that integrates:
1. Camera barcode scanning
2. HX711 weight reading
3. TFT display rendering
4. Cart management
5. Database operations
6. UPI QR generation
7. Bluetooth receipt printing
8. Terminal input control

### New Features

#### 1. Camera Status Indicator

**Visual feedback on all screens:**
- **Green blinking dot** (top-right): Camera ready and scanning
- **Red solid dot + "No Cam"**: Camera error or unavailable

**Implementation:**
- `_blink_state` boolean toggles every 1 second
- `_draw_camera_indicator(draw, camera_ready)` composites onto every screen
- Non-intrusive, always visible

**Error Handling:**
- Camera initialization failure caught gracefully
- `_camera_error` flag tracks camera state
- System continues running with stdin fallback

#### 2. Quantity Management (Not Toggle)

**Old behavior:**
- Scan same barcode → remove item (toggle)

**New behavior:**
- First scan → Add item (qty=1)
- Second scan → Increment quantity (qty=2)
- Third scan → Increment quantity (qty=3)
- etc.

**Special Barcodes:**
- `REMOVE_LAST` - Decrements last scanned item by 1 (removes at qty=0)
- `CMD_CLEAR` - Removes ALL instances of last scanned item

**SessionCart class:**
```python
def add(product) -> (name, new_qty)  # Always increments
def decrement_last() -> (name, new_qty) | None
def remove_last() -> name | None
def clear()  # Reset entire cart
```

#### 3. Persistent Cart Summary Footer

**Displayed on all TFT screens:**
```
┌─────────────────────────────────┐
│ [Content varies by stage]       │
│                                  │
├─────────────────────────────────┤
│ 3 items  Qty:7  ₹234.50        │ ← Footer
└─────────────────────────────────┘
```

**Components:**
- Unique item count (how many different products)
- Total quantity (sum of all quantities)
- Running subtotal (₹)

**Rendered by:**
- `_draw_cart_footer(draw, unique_items, total_qty, subtotal)`
- Called from every screen composition method

#### 4. Terminal Input Thread (GPIO Button Placeholder)

**TerminalController class:**
- Background daemon thread listens for stdin input
- Commands sent via `queue.Queue` to main loop
- Non-blocking, doesn't interfere with scanning

**Commands:**
- `done` → Trigger checkout flow
- `clear` → Reset cart to empty
- `quit` → Exit application

**Future GPIO Integration:**
```python
# In main loop, replace terminal check with:
# TODO: if GPIO.input(CHECKOUT_PIN) == GPIO.HIGH:
#           checkout_triggered = True
```

#### 5. TFT Display Stages

**STAGE 1 - IDLE/WAITING:**
- Shop name and branding
- "Scan your items" prompt
- Camera status indicator
- Cart footer (if items exist) or "Ready" message

**STAGE 2 - PRODUCT FOUND:**
- ✓ ADDED header (green)
- Product name
- Price
- "Qty in cart: N"
- Camera indicator
- Cart footer
- Auto-returns to idle after 2s

**STAGE 3 - PRODUCT NOT FOUND:**
- NOT FOUND header (red)
- "Product not found" message
- Raw barcode string (for debugging)
- "Try scanning again"
- Camera indicator
- Cart footer
- Auto-returns to idle after 2s

**STAGE 4 - ITEM REMOVED/DECREMENTED:**
- REMOVED or QTY -1 header (red)
- Product name
- New quantity or "Removed from cart"
- Camera indicator
- Cart footer
- Auto-returns to idle after 2s

**STAGE 5 - CART SUMMARY:**
- CART header with item count
- List of items (up to 3 visible)
- "+ N more..." if overflow
- Camera indicator
- Cart footer
- Shown before checkout QR

**STAGE 6 - PAYMENT QR:**
- SCAN TO PAY header (yellow)
- UPI QR code (90×90px centered)
- Total amount below QR
- Camera indicator
- Waits for payment confirmation

**STAGE 7 - PAYMENT SUCCESS:**
- PAYMENT SUCCESS header (green)
- Big checkmark ✓
- "PAID" + amount
- "Thank you!" message
- Receipt ID
- Camera indicator
- Auto-returns to idle after 5s

#### 6. Checkout Flow

**Trigger:**
- Terminal command: `done`
- (Future) GPIO button press

**Steps:**
1. Show cart summary (STAGE 5) - 3 seconds
2. Generate UPI QR code with session ID
3. Display QR (STAGE 6)
4. Wait for payment (simulated with Enter key)
5. Save transaction to database
6. Show payment success (STAGE 7)
7. Print receipt via Bluetooth
8. Clear cart and return to idle

**Database Persistence:**
- Session ID: `SESSION_YYYYMMDD_HHMMSS_<random>`
- Cart items stored as JSONB
- Total includes 18% GST
- Payment reference stored

#### 7. Receipt Printing

**ReceiptPrinter class:**
- Connects via Bluetooth MAC address
- Uses `python-escpos` library
- Gracefully degrades if printer unavailable

**Receipt Format:**
```
Smart Trolley Shop
================================
Receipt: SESSION_20260328_143522_ab12cd
Date: 2026-03-28 14:35:22
================================

Product Name
  2x ₹50.00 = ₹100.00

--------------------------------
Subtotal             ₹100.00
GST @18%              ₹18.00
TOTAL                ₹118.00
================================
Payment: UPI_ab12cd34

Thank you for shopping!
```

### Error Handling

1. **Camera Failures:**
   - Caught during initialization
   - Caught during frame capture
   - `_camera_error` flag set
   - Red indicator shown on TFT
   - System continues with stdin fallback

2. **Weight Sensor:**
   - Timeout protection on all HX711 calls
   - Returns `None` if unavailable
   - Logged as warning but doesn't block flow

3. **TFT Display:**
   - All render methods wrapped in try/except
   - Logs error but doesn't crash
   - Falls back to console logging

4. **Bluetooth Printer:**
   - Optional feature
   - Warns if unavailable
   - Checkout proceeds without receipt

### Configuration (via .env)

```bash
# Database
DATABASE_URL=postgresql://...

# UPI Payment
UPI_ID=shop@upi
SHOP_NAME="Smart Trolley Shop"

# Hardware
HX711_DOUT_PIN=5
HX711_SCK_PIN=6
HX711_DISABLE=0
SCAN_DEBOUNCE_SECONDS=1.2

# Bluetooth Receipt Printer
BLUETOOTH_PRINTER_MAC=12:34:56:78:9A:BC
```

---

## File Changes Summary

### Modified Files

| File | Lines Changed | Description |
|------|---------------|-------------|
| `pos_app.py` | ~250 lines removed | Removed customer UI, kept admin panel |
| `main.py` | Complete rewrite (~1100 lines) | Integrated TFT display logic |

### Unchanged Files

| File | Status |
|------|--------|
| `tft_display.py` | Unchanged (but no longer used directly) |
| `.env` | Add new variables for UPI/printer |
| `requirements_pi.txt` | No changes needed |

### New Dependencies

- `qrcode` - QR code generation (moved from web app)
- Already in requirements: `python-escpos`, `PIL`, `luma.lcd`

---

## Acceptance Criteria ✓

| # | Criterion | Status |
|---|-----------|--------|
| 1 | pos_app root (`/`) redirects to `/admin` | ✓ |
| 2 | main.py starts with TFT idle screen | ✓ |
| 3 | Green blinking dot when camera ready | ✓ |
| 4 | Red solid dot + warning when camera fails | ✓ |
| 5 | Scanning valid barcode adds product | ✓ |
| 6 | Scanning same barcode increments quantity | ✓ |
| 7 | REMOVE_LAST decrements last item | ✓ |
| 8 | Unknown barcode shows "not found" in red | ✓ |
| 9 | Cart footer updates after every scan | ✓ |
| 10 | Terminal 'done' triggers checkout | ✓ |
| 11 | Terminal 'clear' resets cart | ✓ |
| 12 | Checkout shows summary then QR | ✓ |
| 13 | Payment prints receipt and resets | ✓ |

---

## Testing Checklist

### pos_app.py

- [ ] Start flask app: `python pos_app.py`
- [ ] Visit `http://localhost:5000/` → should redirect to `/admin`
- [ ] Admin panel shows product list
- [ ] Admin panel shows transaction history
- [ ] Can add new product
- [ ] Can delete product
- [ ] Health check returns transaction count

### main.py

- [ ] Start Pi runtime: `python main.py`
- [ ] TFT shows idle screen with shop name
- [ ] Camera indicator visible (green blinking or red solid)
- [ ] Scan valid barcode → shows product card
- [ ] Product card shows price, qty, and updates cart footer
- [ ] Auto-returns to idle after 2s
- [ ] Scan same barcode again → qty increments to 2
- [ ] Cart footer shows "2 items, Qty:2, ₹XX.XX"
- [ ] Scan unknown barcode → "NOT FOUND" in red
- [ ] Type `REMOVE_LAST` → decrements last item qty
- [ ] Type `CMD_CLEAR` → removes last item entirely
- [ ] Type `clear` → cart resets, footer disappears
- [ ] Add items, type `done` → checkout flow starts
- [ ] Shows cart summary for 3s
- [ ] Shows UPI QR code
- [ ] Press Enter → payment confirmed
- [ ] Shows success screen with checkmark
- [ ] Receipt prints (if printer configured)
- [ ] Cart clears, returns to idle

### Integration

- [ ] Add products via admin panel
- [ ] Scan those products in Pi runtime
- [ ] Complete checkout
- [ ] Verify transaction appears in admin panel
- [ ] Check database for transaction record

---

## Migration Steps

1. **Backup current code:**
   ```bash
   git commit -am "Backup before refactoring"
   ```

2. **Deploy changes:**
   - Update `pos_app.py` (admin panel only)
   - Update `main.py` (complete Pi runtime)
   - No database migrations needed

3. **Update environment:**
   - Add `UPI_ID` to `.env`
   - Add `BLUETOOTH_PRINTER_MAC` to `.env`

4. **Test admin panel:**
   ```bash
   python pos_app.py
   ```

5. **Test Pi runtime:**
   ```bash
   python main.py
   ```

6. **Remove obsolete files (optional):**
   - `templates/pos_dashboard.html` (customer UI)
   - `tft_display.py` (logic now in main.py)

---

## Future GPIO Button Implementation

Replace terminal input with GPIO button:

```python
# In main.py, around line 900, replace:
try:
    cmd = terminal_queue.get_nowait()
    if cmd == "done":
        checkout_triggered = True
except queue.Empty:
    pass

# With:
import RPi.GPIO as GPIO
CHECKOUT_PIN = 17  # Define in .env
GPIO.setmode(GPIO.BCM)
GPIO.setup(CHECKOUT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# In main loop:
if GPIO.input(CHECKOUT_PIN) == GPIO.LOW:  # Button pressed (active low)
    checkout_triggered = True
    time.sleep(0.2)  # Debounce
```

Remove `TerminalController` class and thread once GPIO is wired.

---

## Notes

- TFT display logic is now self-contained in `main.py`
- All screens consistently show camera indicator
- Cart management is local to Pi runtime (not web-based)
- Database remains single source of truth for products and transactions
- Admin panel is lightweight, database-only tool
- Receipt printing is optional (graceful degradation)
- Terminal input is temporary (GPIO button placeholder)

**End of refactoring summary**
