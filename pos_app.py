#!/usr/bin/env python3
"""
Smart Trolley Point of Sale System
Complete POS flow: Scan → Cart → Total → QR Code → Receipt
Products served from Neon PostgreSQL via psycopg2.
"""

import os
import json
import io
import base64
import uuid
import logging
from datetime import datetime
from contextlib import contextmanager

from flask import Flask, render_template, request, jsonify, redirect
import razorpay
import qrcode
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
UPI_ID       = os.getenv("UPI_ID", "yourshop@upi")
SHOP_NAME    = os.getenv("SHOP_NAME", "Smart Trolley Shop")
RZP_KEY_ID   = os.getenv("RAZORPAY_KEY_ID", "")
RZP_SECRET   = os.getenv("RAZORPAY_KEY_SECRET", "")

# ── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "pos-system-secret-key")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Razorpay ──────────────────────────────────────────────────────────────────
def get_razorpay_client():
    client = razorpay.Client(auth=(RZP_KEY_ID, RZP_SECRET))
    client.enable_retry(True)
    client.set_app_details({"title": "POS Payment System", "version": "1.0.0"})
    return client

razorpay_client = get_razorpay_client()

# ── Database ──────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    """Short-lived psycopg2 connection. Always closes after the with block."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_product_by_barcode(barcode: str) -> dict | None:
    """Look up a product from Neon by barcode. Returns dict or None."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM products WHERE barcode = %s LIMIT 1",
                (barcode,)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def save_transaction(session_id: str, items: list, total: float,
                     status: str = "paid", payment_method: str = "UPI/QR",
                     upi_ref: str = "") -> int:
    """Insert a completed transaction into Neon. Returns the new row id."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions
                    (session_id, items, total_amount, payment_status, payment_method, upi_ref)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s)
                RETURNING id
                """,
                (session_id, json.dumps(items), total, status, payment_method, upi_ref)
            )
            return cur.fetchone()["id"]


# ── In-memory cart (per Flask process / session) ──────────────────────────────
shopping_cart: list[dict] = []


def calculate_cart_total() -> float:
    return sum(item["total"] for item in shopping_cart)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def pos_dashboard():
    """Main POS dashboard — pass product list from DB for the sidebar."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT barcode, name, price, category FROM products ORDER BY category, name")
            products = {row["barcode"]: dict(row) for row in cur.fetchall()}
    return render_template("pos_dashboard.html", products=products)


@app.route("/api/scan_product", methods=["POST"])
def scan_product():
    """Scan product by barcode — looks up Neon instead of hardcoded dict."""
    try:
        data = request.get_json()
        barcode = str(data.get("product_id", "")).strip()

        product = get_product_by_barcode(barcode)
        if not product:
            return jsonify({
                "success": False,
                "error": "Product not found",
                "message": f"No product with barcode {barcode!r} in database"
            }), 404

        return jsonify({
            "success": True,
            "product": {
                "id":              product["barcode"],
                "name":            product["name"],
                "price":           float(product["price"]),
                "category":        product.get("category", ""),
                "expected_weight": product.get("weight_grams", 0),
                "quantity":        1,
                "total":           float(product["price"]),
            },
            "message": f'Product {product["name"]} scanned successfully'
        })

    except Exception as e:
        logger.error("Error scanning product: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/add_to_cart", methods=["POST"])
def add_to_cart():
    """Add product to shopping cart (in-memory)."""
    global shopping_cart
    try:
        data = request.get_json()
        barcode  = str(data.get("product_id", "")).strip()
        quantity = int(data.get("quantity", 1))

        product = get_product_by_barcode(barcode)
        if not product:
            return jsonify({"success": False, "error": "Product not found"}), 404

        for item in shopping_cart:
            if item["id"] == barcode:
                item["quantity"] += quantity
                item["total"] = item["price"] * item["quantity"]
                break
        else:
            shopping_cart.append({
                "id":       barcode,
                "name":     product["name"],
                "price":    float(product["price"]),
                "category": product.get("category", ""),
                "expected_weight": product.get("weight_grams", 0),
                "quantity": quantity,
                "total":    float(product["price"]) * quantity,
            })

        return jsonify({
            "success":    True,
            "cart":       shopping_cart,
            "cart_count": len(shopping_cart),
            "cart_total": calculate_cart_total()
        })

    except Exception as e:
        logger.error("Error adding to cart: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/get_cart")
def get_cart():
    return jsonify({
        "success":    True,
        "cart":       shopping_cart,
        "cart_count": len(shopping_cart),
        "cart_total": calculate_cart_total()
    })


@app.route("/api/remove_from_cart", methods=["POST"])
def remove_from_cart():
    global shopping_cart
    try:
        product_id    = request.get_json().get("product_id")
        shopping_cart = [i for i in shopping_cart if i["id"] != product_id]
        return jsonify({
            "success":    True,
            "cart":       shopping_cart,
            "cart_count": len(shopping_cart),
            "cart_total": calculate_cart_total()
        })
    except Exception as e:
        logger.error("Error removing from cart: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/clear_cart", methods=["POST"])
def clear_cart():
    global shopping_cart
    shopping_cart = []
    return jsonify({"success": True, "cart": [], "cart_count": 0, "cart_total": 0})


@app.route("/api/create_payment_order", methods=["POST"])
def create_payment_order():
    """Create Razorpay order and generate UPI QR code."""
    try:
        if not shopping_cart:
            return jsonify({"success": False, "error": "Cart is empty"}), 400

        cart_total  = calculate_cart_total()
        gst_amount  = cart_total * 0.18
        final_total = cart_total + gst_amount

        order = razorpay_client.order.create({
            "amount":          int(final_total * 100),
            "currency":        "INR",
            "receipt":         f"pos_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "payment_capture": 1,
        })

        # UPI QR (uses env UPI_ID instead of hardcoded test UPI)
        qr_data = (
            f"upi://pay?pa={UPI_ID}&pn={SHOP_NAME}"
            f"&am={final_total:.2f}&cu=INR&tn={order['id']}"
        )
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()

        return jsonify({
            "success":     True,
            "order":       order,
            "qr_code":     f"data:image/png;base64,{qr_b64}",
            "cart_total":  cart_total,
            "gst_amount":  gst_amount,
            "final_total": final_total,
            "cart_items":  shopping_cart,
            "message":     "Payment QR code generated successfully"
        })

    except Exception as e:
        logger.error("Error creating payment order: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/complete_payment", methods=["POST"])
def complete_payment():
    """Mark payment as done and persist transaction to Neon."""
    global shopping_cart
    try:
        if not shopping_cart:
            return jsonify({"success": False, "error": "Cart is empty"}), 400

        data       = request.get_json()
        order_id   = data.get("order_id", "")
        payment_id = data.get("payment_id", f"pay_demo_{uuid.uuid4().hex[:8]}")
        cart_total = calculate_cart_total()
        final_total = cart_total * 1.18
        session_id  = f"SESSION_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # Persist to Neon
        tx_id = save_transaction(
            session_id=session_id,
            items=shopping_cart.copy(),
            total=final_total,
            status="paid",
            payment_method="UPI/QR",
            upi_ref=payment_id,
        )
        logger.info("Transaction saved to DB: id=%s session=%s", tx_id, session_id)

        receipt = {
            "receipt_id":     session_id,
            "db_transaction": tx_id,
            "order_id":       order_id,
            "payment_id":     payment_id,
            "date":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items":          shopping_cart.copy(),
            "subtotal":       cart_total,
            "tax":            cart_total * 0.18,
            "total":          final_total,
            "payment_method": "UPI/QR Code",
            "status":         "PAID",
        }

        shopping_cart = []
        return jsonify({"success": True, "receipt": receipt,
                        "message": "Payment completed successfully"})

    except Exception as e:
        logger.error("Error completing payment: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/receipt/<receipt_id>")
def view_receipt(receipt_id):
    return render_template("receipt.html", receipt_id=receipt_id)


# ── Admin Panel ───────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_panel():
    """Admin panel — list all products."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM products ORDER BY category, name"
            )
            products = [dict(r) for r in cur.fetchall()]
    return render_template("admin.html", products=products)


@app.route("/admin/add", methods=["POST"])
def admin_add_product():
    """Add a new product via the admin form."""
    try:
        barcode      = request.form.get("barcode", "").strip()
        name         = request.form.get("name", "").strip()
        price        = float(request.form.get("price", 0))
        weight_grams = request.form.get("weight_grams", None)
        category     = request.form.get("category", "General").strip()
        stock        = int(request.form.get("stock", 0))

        if not barcode or not name or price <= 0:
            return jsonify({"success": False, "error": "barcode, name and price are required"}), 400

        weight_grams = int(weight_grams) if weight_grams else None

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO products (barcode, name, price, weight_grams, category, stock)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (barcode) DO UPDATE
                        SET name = EXCLUDED.name,
                            price = EXCLUDED.price,
                            weight_grams = EXCLUDED.weight_grams,
                            category = EXCLUDED.category,
                            stock = EXCLUDED.stock,
                            updated_at = NOW()
                    """,
                    (barcode, name, price, weight_grams, category, stock)
                )
        return redirect("/admin")

    except Exception as e:
        logger.error("Error adding product: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/admin/delete/<barcode>", methods=["POST"])
def admin_delete_product(barcode: str):
    """Delete a product by barcode."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE barcode = %s", (barcode,))
        return redirect("/admin")
    except Exception as e:
        logger.error("Error deleting product: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/admin/products")
def api_admin_products():
    """JSON list of all products — used by the POS dashboard to refresh the grid."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT barcode, name, price, category, stock FROM products ORDER BY category, name")
            products = {r["barcode"]: dict(r) for r in cur.fetchall()}
    return jsonify({"success": True, "products": products})


@app.route("/health")
def health_check():
    """Health check — also verifies DB connectivity."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM products")
                product_count = cur.fetchone()["cnt"]
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
        product_count = None

    return jsonify({
        "status":         "healthy" if db_status == "ok" else "degraded",
        "timestamp":      datetime.now().isoformat(),
        "service":        "Smart Trolley POS",
        "database":       db_status,
        "product_count":  product_count,
    })


if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    print("Starting Smart Trolley POS...")
    print(f"Dashboard:    http://0.0.0.0:5000")
    print(f"Health check: http://0.0.0.0:5000/health")
    app.run(host="0.0.0.0", port=5000, debug=True)
