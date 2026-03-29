-- Smart Trolley Checkout System — Database Schema
-- Run once against Neon:  psql $DATABASE_URL -f db/schema.sql

CREATE TABLE IF NOT EXISTS products (
    id            SERIAL PRIMARY KEY,
    barcode       TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    price         NUMERIC(10, 2) NOT NULL,
    weight_grams  INT,
    category      TEXT,
    image_url     TEXT,
    stock         INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id             SERIAL PRIMARY KEY,
    session_id     TEXT NOT NULL,
    items          JSONB NOT NULL,
    total_amount   NUMERIC(10, 2) NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'pending',   -- pending | paid | failed
    payment_method TEXT,
    upi_ref        TEXT,
    razorpay_order_id TEXT,
    razorpay_qr_id TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS razorpay_order_id TEXT;

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS razorpay_qr_id TEXT;

-- Auto-update updated_at on products rows
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS products_updated_at ON products;
CREATE TRIGGER products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
