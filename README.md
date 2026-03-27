# 🛒 Smart Trolley Checkout System

A self-contained POS system running on a **Raspberry Pi 4B**. Customers scan products with the Pi camera, see live cart totals on the TFT display, pay via UPI QR code, and get a Bluetooth-printed receipt — no cashier needed.

A Flask-based web interface provides a live POS dashboard and an admin panel to manage the product catalog, all backed by **Neon PostgreSQL**.

---

## Project Structure

```
Trolly_system/
├── pos_app.py               # Flask app — POS dashboard + admin panel
├── db_setup.py              # One-shot DB schema + seed runner (no psql needed)
├── db/
│   ├── schema.sql           # Products + transactions tables
│   └── seed.sql             # 10 demo products
├── templates/
│   ├── pos_dashboard.html   # POS cashier UI
│   └── admin.html           # Product management UI
├── requirements_pi.txt      # All Python dependencies
├── setup_raspberry_pi.sh    # Pi system setup script
├── .env.example             # Environment variable template
└── .planning/               # GSD project planning artifacts
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Neon PostgreSQL account | Free tier |
| UPI ID | For payment QR |

---

## 1 — Environment Setup

Copy the template and fill in your real values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Neon pooling URL (port 6543, has pgbouncer=true)
DATABASE_URL=postgresql://user:password@ep-xxx.region.aws.neon.tech/dbname?sslmode=require

# UPI payment details
UPI_ID=yourname@upi
SHOP_NAME=My Smart Trolley Shop

# Razorpay (optional — only for legacy order creation)
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxx
```

---

## 2 — Database Setup (Run Once)

No `psql` required — uses the Python script:

```bash
python db_setup.py
```

This creates the `products` and `transactions` tables in Neon and seeds 10 demo products.

---

## Running on Normal Linux (Dev Machine / x86_64)

> ⚠️ Pi-specific packages (`picamera2`, `luma.lcd`, `RPi.GPIO`) cannot install on x86. The Flask web app works fine; Pi hardware features require the Pi.

### 1. Create virtual environment

```bash
cd /path/to/Trolly_system
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
# Install only the non-Pi packages
pip install flask psycopg2-binary python-dotenv qrcode[pil] razorpay Pillow
```

> Or install everything and ignore Pi-only errors:
> ```bash
> pip install -r requirements_pi.txt --ignore-requires-python 2>/dev/null || true
> pip install flask psycopg2-binary python-dotenv qrcode[pil] razorpay Pillow
> ```

### 3. Set up the database

```bash
python db_setup.py
```

### 4. Run the app

```bash
python pos_app.py
```

Open in browser:
- **POS Dashboard:** http://localhost:5000
- **Admin Panel:** http://localhost:5000/admin
- **Health Check:** http://localhost:5000/health

---

## Running on Raspberry Pi 4B

> Target OS: **Raspberry Pi OS** (Debian Bookworm or Bullseye, 64-bit recommended)

### 1. Run the system setup script (once)

```bash
chmod +x setup_raspberry_pi.sh
./setup_raspberry_pi.sh
```

This installs system packages, enables SPI/I2C, adds GPIO permissions.

### 2. Create virtual environment

```bash
cd ~/Trolly_system
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install all Pi dependencies

```bash
pip install -r requirements_pi.txt
```

> `picamera2` is pre-installed on Raspberry Pi OS. If missing:
> ```bash
> sudo apt install -y python3-picamera2
> ```

### 4. Set up `.env`

```bash
cp .env.example .env
nano .env   # fill in DATABASE_URL, UPI_ID, SHOP_NAME, BLUETOOTH_PRINTER_MAC
```

### 5. Set up the database (once)

```bash
python db_setup.py
```

### 6. Run the app

```bash
source .venv/bin/activate
python pos_app.py
```

### 6a. Run Phase 2 Pi runtime (idle + scan + display)

```bash
source .venv/bin/activate
python main.py
```

Notes:
- Requires `DATABASE_URL` in `.env`.
- Uses camera barcode scanning when `picamera2` + `pyzbar` are available.
- Falls back to terminal input if camera libraries are not available.
- Reads HX711 after each scan when the sensor library and wiring are available.

### 7. Auto-start on boot (systemd)

Create the service file:

```bash
sudo nano /etc/systemd/system/smart-trolley.service
```

Paste:

```ini
[Unit]
Description=Smart Trolley POS System
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Trolly_system
EnvironmentFile=/home/pi/Trolly_system/.env
ExecStart=/home/pi/Trolly_system/.venv/bin/python pos_app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smart-trolley
sudo systemctl start smart-trolley
sudo systemctl status smart-trolley
```

---

## Admin Panel

Visit `http://<ip>:5000/admin` to manage products:

| Feature | How |
|---|---|
| Add product | Fill the form (barcode, name, price, weight, category, stock) → Save |
| Edit product | Click the ✏️ pen icon in the table row → form auto-fills → Save |
| Delete product | Click 🗑 trash icon → confirm dialog |
| Search | Type in the search box to filter the table live |

If a barcode already exists, saving the form **updates** the existing product.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | Neon pooling connection string |
| `UPI_ID` | ✅ | UPI ID for payment QR code |
| `SHOP_NAME` | ✅ | Shop name shown in QR + receipts |
| `RAZORPAY_KEY_ID` | Optional | Razorpay API key (legacy order creation) |
| `RAZORPAY_KEY_SECRET` | Optional | Razorpay API secret |
| `FLASK_SECRET` | Optional | Flask session secret (defaults to built-in) |

---

## Health Check

```bash
curl http://localhost:5000/health
```

Returns database connectivity status and product count:

```json
{
  "status": "healthy",
  "database": "ok",
  "product_count": 10
}
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Database | Neon PostgreSQL (psycopg2) |
| Frontend | Tailwind CSS, Vanilla JS |
| Payment | UPI deeplink QR code |
| Pi display | luma.lcd (ST7735 TFT) — Phase 2 |
| Pi camera | picamera2 + pyzbar — Phase 2 |
| Printing | python-escpos (Bluetooth) — Phase 4 |
