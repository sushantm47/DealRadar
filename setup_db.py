"""Create the PostgreSQL database (if needed) and apply schema.sql. Safe to re-run: it resets all tables."""
import os

import psycopg2
from psycopg2 import sql

import db_manager

cfg = db_manager.DB_CONFIG
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

print(f"[LOG] Connecting to PostgreSQL ({cfg['host']}:{cfg['port']})...")
try:
    # 1. Create the database from the maintenance DB if it doesn't exist
    admin = psycopg2.connect(**{**cfg, "dbname": os.getenv("DB_ADMIN_DB", "postgres")})
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cfg["dbname"],))
        if not cur.fetchone():
            print(f"[LOG] Creating database {cfg['dbname']}...")
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cfg["dbname"])))
    admin.close()

    # 2. Apply schema (tables, indexes, procedure, trigger, seed data)
    print("[LOG] Applying schema.sql...")
    conn = db_manager.get_connection()
    with conn, conn.cursor() as cur, open(SCHEMA_FILE, encoding="utf-8") as f:
        cur.execute(f.read())
    conn.close()

    print(" SETUP COMPLETE!")
    print(" Login: test")
    print(" Pass: test@123")
except Exception as e:
    print(f"\n ERROR: {e}")
