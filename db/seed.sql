-- Smart Trolley — Seed Data
-- Seeds the 10 demo products that were previously hardcoded in pos_app.py
-- Run after schema.sql:  psql $DATABASE_URL -f db/seed.sql

INSERT INTO products (barcode, name, price, weight_grams, category, stock) VALUES
    ('1001', 'Laptop',            45000, 1500, 'Electronics', 10),
    ('1002', 'Mouse',               800,  120, 'Electronics', 50),
    ('1003', 'Keyboard',           1500,  800, 'Electronics', 30),
    ('1004', 'Monitor',           12000, 3000, 'Electronics',  8),
    ('1005', 'Headphones',         2000,  400, 'Electronics', 20),
    ('1006', 'USB Cable',           200,   50, 'Accessories', 100),
    ('1007', 'Webcam',             2500,  200, 'Electronics', 15),
    ('1008', 'Phone Case',          500,   80, 'Accessories', 40),
    ('1009', 'Power Bank',         1500,  300, 'Electronics', 25),
    ('1010', 'Bluetooth Speaker',  3000,  600, 'Electronics', 12)
ON CONFLICT (barcode) DO NOTHING;
