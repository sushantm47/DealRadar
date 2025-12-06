import pymysql
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "dealradar")
}

def get_connection():
    return pymysql.connect(**DB_CONFIG)

def run_query(query, params=None):
    conn = get_connection()
    try:
        return pd.read_sql(query, conn, params=params)
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
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.callproc('InsertPrice', (pid, sid, price, url))
        conn.commit()
    finally:
        conn.close()

def get_or_create_product(pname, description, category, msrp, url):
    existing = run_query("SELECT pid FROM Product WHERE tracking_url=%s", (url,))
    if not existing.empty:
        return existing.iloc[0]['pid'], False
    
    execute_command(
        "INSERT INTO Product (pname, p_description, p_category, msrp, tracking_url) VALUES (%s, %s, %s, %s, %s)",
        (pname, description, category, msrp, url)
    )
    new_id = run_query("SELECT pid FROM Product WHERE tracking_url=%s", (url,)).iloc[0]['pid']
    return new_id, True

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
    except: return False
        
def delete_product(pid):
    execute_command("DELETE FROM Product WHERE pid=%s", (pid,))