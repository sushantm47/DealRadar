import json
import os

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extensions import AsIs, register_adapter
from psycopg2.extras import Json, execute_values
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "dbname": os.getenv("DB_NAME", "dealradar"),
}

# Let psycopg2 accept numpy scalars that come out of pandas (e.g. df.iloc[0]['pid'])
register_adapter(np.int64, AsIs)
register_adapter(np.int32, AsIs)
register_adapter(np.float64, AsIs)
register_adapter(np.bool_, lambda b: AsIs(bool(b)))


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def run_query(query, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return pd.DataFrame.from_records(cursor.fetchall(), columns=cols, coerce_float=True)
    finally:
        conn.close()


def execute_command(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def call_insert_price_procedure(pid, sid, price, url):
    execute_command("CALL InsertPrice(%s::int, %s::int, %s::numeric, %s::text)", (pid, sid, price, url))


def get_or_create_product(pname, description, category, msrp, url):
    existing = run_query("SELECT pid FROM Product WHERE tracking_url=%s", (url,))
    if not existing.empty:
        return int(existing.iloc[0]['pid']), False
    new_id = _insert_returning(
        "INSERT INTO Product (pname, p_description, p_category, msrp, tracking_url) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING pid",
        (pname, description, category, msrp, url),
    )
    return int(new_id), True


def _insert_returning(sql, params):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            value = cursor.fetchone()[0]
        conn.commit()
        return value
    finally:
        conn.close()


def add_to_cart(uid, pid, cutoff):
    exists = run_query("SELECT cid FROM Cart WHERE uid=%s AND pid=%s", (uid, pid))
    if exists.empty:
        execute_command("INSERT INTO Cart (uid, pid, cutoff) VALUES (%s, %s, %s)", (uid, pid, cutoff))


def update_cart_target(cid, new_cutoff):
    execute_command("UPDATE Cart SET cutoff=%s WHERE cid=%s", (new_cutoff, cid))


def delete_from_cart(cid):
    execute_command("DELETE FROM Cart WHERE cid=%s", (cid,))


def create_user(fname, lname, email, password):
    try:
        execute_command("INSERT INTO Users (fname, lname, email, pswd) VALUES (%s, %s, %s, %s)", (fname, lname, email, password))
        return True
    except Exception:
        return False


def delete_product(pid):
    execute_command("DELETE FROM Product WHERE pid=%s", (pid,))


# ---------------------------------------------------------------------------
# Bulk helpers used by pipeline.py (one round-trip per batch, not per row)
# ---------------------------------------------------------------------------

def get_or_create_sellers(names):
    """{seller_name: sid}; unknown sellers are inserted."""
    names = sorted({n for n in names if n})
    if not names:
        return {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, "INSERT INTO Sellers (sname) VALUES %s ON CONFLICT (sname) DO NOTHING",
                           [(n,) for n in names])
            cur.execute("SELECT sname, sid FROM Sellers WHERE sname = ANY(%s)", (names,))
            mapping = dict(cur.fetchall())
        conn.commit()
        return mapping
    finally:
        conn.close()


def get_or_create_products(rows):
    """
    rows: iterable of (tracking_url, product_name, category, description).
    Returns {tracking_url: pid}. Rows whose URL is unknown AND have no name are not created.
    """
    rows = list(rows)
    urls = sorted({r[0] for r in rows})
    if not urls:
        return {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tracking_url, pid FROM Product WHERE tracking_url = ANY(%s)", (urls,))
            mapping = dict(cur.fetchall())
            new_rows, seen = [], set()
            for url, name, category, desc in rows:
                if url not in mapping and url not in seen and name:
                    new_rows.append((str(name)[:255], desc, category, url))
                    seen.add(url)
            if new_rows:
                created = execute_values(
                    cur,
                    "INSERT INTO Product (pname, p_description, p_category, tracking_url) VALUES %s "
                    "ON CONFLICT (tracking_url) DO NOTHING RETURNING tracking_url, pid",
                    new_rows, fetch=True)
                mapping.update(dict(created))
        conn.commit()
        return mapping
    finally:
        conn.close()


def get_last_prices(pids):
    """{(pid, sid): latest price} for anomaly detection."""
    pids = sorted({int(p) for p in pids})
    if not pids:
        return {}
    df = run_query(
        "SELECT DISTINCT ON (pid, sid) pid, sid, price FROM Seller_Prices "
        "WHERE pid = ANY(%s) ORDER BY pid, sid, price_dt DESC, spid DESC", (pids,))
    return {(int(r.pid), int(r.sid)): float(r.price) for r in df.itertuples()}


def bulk_insert_prices(rows):
    """rows: [(pid, sid, price, url), ...]. The AfterPriceInsert trigger still fires per row."""
    if not rows:
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, "INSERT INTO Seller_Prices (pid, sid, price, sp_url) VALUES %s", rows, page_size=1000)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _json_safe(record):
    clean = {}
    for k, v in record.items():
        if not isinstance(v, (list, dict, tuple)) and pd.isna(v):
            v = None
        clean[k] = v
    return Json(clean, dumps=lambda o: json.dumps(o, default=str))


def log_rejected(records, source):
    """records: list of dicts, each with a 'reason' key."""
    if not records:
        return 0
    rows = [(source, r.get("reason"), _json_safe({k: v for k, v in r.items() if k != "reason"})) for r in records]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, "INSERT INTO Rejected_Records (source, reason, raw_record) VALUES %s", rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def log_rag_query(question, answer, cited_ids, grounded, reject_reason, model):
    try:
        execute_command(
            "INSERT INTO Rag_Queries (question, answer, cited_ids, grounded, reject_reason, model) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (question, answer, list(cited_ids or []), grounded, reject_reason, model))
    except Exception:
        pass  # auditing must never break the assistant
