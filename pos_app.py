#!/usr/bin/env python3
"""
Smart Trolley Admin Panel
Admin web interface for product and transaction management.
Customer-facing UI has been moved to the Pi runtime (main.py + TFT display).
"""

import os
import json
import math
import logging
from datetime import datetime
from contextlib import contextmanager

from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "5"))

# ── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "pos-admin-secret-key")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_INDEXES_READY = False

# ── Database ──────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    """Short-lived psycopg2 connection. Always closes after the with block."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
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


def ensure_admin_indexes() -> None:
    global _INDEXES_READY
    if _INDEXES_READY:
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_session ON transactions(session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_products_stock ON products(stock)")
    _INDEXES_READY = True


def _normalize_items(raw_items) -> list[dict]:
    if isinstance(raw_items, list):
        parsed = raw_items
    elif isinstance(raw_items, str):
        try:
            parsed = json.loads(raw_items)
        except Exception:
            parsed = []
    else:
        parsed = []

    normalized: list[dict] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        qty = int(row.get("qty") or row.get("quantity") or 0)
        unit_price = float(row.get("unit_price") or row.get("price") or 0.0)
        line_total = float(row.get("line_total") or row.get("total") or (qty * unit_price))
        normalized.append(
            {
                "barcode": str(row.get("barcode") or row.get("id") or ""),
                "name": str(row.get("name") or "Item"),
                "qty": qty,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )
    return normalized


def _status_bucket(stock: int) -> str:
    if stock <= 0:
        return "out"
    if stock <= LOW_STOCK_THRESHOLD:
        return "low"
    return "in"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Redirect root to admin panel."""
    return redirect(url_for("admin_products"))


# ── Admin Panel ───────────────────────────────────────────────────────────────

@app.route("/admin")
@app.route("/admin/products")
def admin_products():
    """Products admin page with stock-aware badges and summary stats."""
    ensure_admin_indexes()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products ORDER BY category, name")
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    COALESCE(SUM(total_amount), 0) AS today_sales,
                    COUNT(*) AS today_orders
                FROM transactions
                WHERE payment_status = 'paid'
                  AND created_at::date = CURRENT_DATE
                """
            )
            sales_row = dict(cur.fetchone() or {})

            cur.execute("SELECT COUNT(*) AS cnt FROM products")
            total_products = int((cur.fetchone() or {}).get("cnt", 0))

            cur.execute("SELECT COUNT(*) AS cnt FROM products WHERE stock <= %s", (LOW_STOCK_THRESHOLD,))
            low_stock_items = int((cur.fetchone() or {}).get("cnt", 0))

            cur.execute("SELECT COUNT(*) AS cnt FROM products WHERE stock = 0")
            out_of_stock_items = int((cur.fetchone() or {}).get("cnt", 0))

    for product in products:
        stock = int(product.get("stock") or 0)
        product["stock_status"] = _status_bucket(stock)
        product["stock_value"] = stock

    stats = {
        "today_sales": float(sales_row.get("today_sales") or 0.0),
        "today_orders": int(sales_row.get("today_orders") or 0),
        "total_products": total_products,
        "low_stock_items": low_stock_items,
        "out_of_stock_items": out_of_stock_items,
    }

    return render_template(
        "admin.html",
        products=products,
        stats=stats,
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        active_page="products",
    )


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
        return redirect(url_for("admin_products"))

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
        return redirect(url_for("admin_products"))
    except Exception as e:
        logger.error("Error deleting product: %s", e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/admin/orders")
def admin_orders():
    """Paginated orders history showing all transactions."""
    ensure_admin_indexes()

    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM transactions")
            total_orders = int((cur.fetchone() or {}).get("cnt", 0))

            cur.execute(
                """
                SELECT session_id, total_amount, payment_status, payment_method,
                       upi_ref, created_at, items
                FROM transactions
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]

    total_pages = max(1, math.ceil(total_orders / per_page))
    if page > total_pages and total_orders > 0:
        return redirect(url_for("admin_orders", page=total_pages))

    orders = []
    for row in rows:
        session_id = str(row.get("session_id") or "")
        items = _normalize_items(row.get("items"))
        upi_ref = str(row.get("upi_ref") or "")
        orders.append(
            {
                **row,
                "bill_no": session_id[-8:] if session_id else "-",
                "items_count": len(items),
                "payment_ref_short": upi_ref[-12:] if upi_ref else "-",
            }
        )

    return render_template(
        "orders.html",
        orders=orders,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_orders=total_orders,
        active_page="orders",
    )


@app.route("/admin/orders/<session_id>")
def admin_order_detail(session_id: str):
    """Detailed view for a single order session."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, total_amount, payment_status, payment_method,
                       upi_ref, created_at, items
                FROM transactions
                WHERE session_id = %s
                LIMIT 1
                """,
                (session_id,),
            )
            row = cur.fetchone()

    if not row:
        abort(404)

    order = dict(row)
    items = _normalize_items(order.get("items"))
    subtotal = sum(float(item.get("line_total") or 0.0) for item in items)
    item_count = sum(int(item.get("qty") or 0) for item in items)
    payment_ref = str(order.get("upi_ref") or "")

    return render_template(
        "order_detail.html",
        order=order,
        items=items,
        subtotal=subtotal,
        item_count=item_count,
        payment_ref_short=payment_ref[-12:] if payment_ref else "-",
        active_page="orders",
    )


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

