#!/usr/bin/env python3
"""
Smart Trolley Admin Panel
Admin web interface for product and transaction management.
Customer-facing UI has been moved to the Pi runtime (main.py + TFT display).
"""

import os
import logging
from datetime import datetime
from contextlib import contextmanager

from flask import Flask, render_template, request, jsonify, redirect
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

# ── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "pos-admin-secret-key")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Redirect root to admin panel."""
    return redirect("/admin")


# ── Admin Panel ───────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_panel():
    """Admin panel — list all products and transactions."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Get products
            cur.execute(
                "SELECT * FROM products ORDER BY category, name"
            )
            products = [dict(r) for r in cur.fetchall()]

            # Get recent transactions
            cur.execute(
                """
                SELECT id, session_id, total_amount, payment_status,
                       payment_method, created_at
                FROM transactions
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
            transactions = [dict(r) for r in cur.fetchall()]

    return render_template("admin.html", products=products, transactions=transactions)


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


@app.route("/health")
def health_check():
    """Health check — also verifies DB connectivity."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM products")
                product_count = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) AS cnt FROM transactions")
                transaction_count = cur.fetchone()["cnt"]
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
        product_count = None
        transaction_count = None

    return jsonify({
        "status":             "healthy" if db_status == "ok" else "degraded",
        "timestamp":          datetime.now().isoformat(),
        "service":            "Smart Trolley Admin Panel",
        "database":           db_status,
        "product_count":      product_count,
        "transaction_count":  transaction_count,
    })


if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    print("Starting Smart Trolley Admin Panel...")
    print(f"Admin Panel:  http://0.0.0.0:5000/admin")
    print(f"Health check: http://0.0.0.0:5000/health")
    print("\nNote: Customer-facing UI is on the Pi runtime (main.py + TFT display)")
    app.run(host="0.0.0.0", port=5000, debug=True)

