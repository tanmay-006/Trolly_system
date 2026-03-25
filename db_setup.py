#!/usr/bin/env python3
"""
db_setup.py — Apply schema.sql and seed.sql to Neon using psycopg2.
Replaces `psql` when it's not available on the dev machine.

Usage:
    source .venv/bin/activate
    python db_setup.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL or "ep-xxx" in DATABASE_URL:
    print("ERROR: DATABASE_URL is not set or still has the placeholder value.")
    print("  Edit .env and set a real Neon connection string.")
    sys.exit(1)

SCHEMA_FILE = Path(__file__).parent / "db" / "schema.sql"
SEED_FILE   = Path(__file__).parent / "db" / "seed.sql"

def run_sql_file(conn, path: Path, label: str):
    sql = path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print(f"  ✓ {label} applied")

def main():
    print(f"Connecting to Neon...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    try:
        print("Applying schema...")
        run_sql_file(conn, SCHEMA_FILE, "schema.sql")

        print("Seeding demo products...")
        run_sql_file(conn, SEED_FILE, "seed.sql")

        # Verify
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            count = cur.fetchone()[0]
        print(f"\n✅ Done! {count} products in database.")
        print("   Run: python pos_app.py")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
