import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "dealradar")

print(f"[LOG] Connecting to MySQL ({DB_HOST})...")

try:
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD)
    cursor = conn.cursor()
    
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME};")
    cursor.execute(f"USE {DB_NAME};")

    print("[LOG] Resetting Tables...")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
    objects = [
        "TABLE IF EXISTS Alerts", "TABLE IF EXISTS User_News", "TABLE IF EXISTS Cart", 
        "TABLE IF EXISTS Seller_Prices", "TABLE IF EXISTS Sellers", "TABLE IF EXISTS Product", 
        "TABLE IF EXISTS News", "TABLE IF EXISTS Users", 
        "PROCEDURE IF EXISTS InsertPrice", "TRIGGER IF EXISTS AfterPriceInsert"
    ]
    for obj in objects: cursor.execute(f"DROP {obj};")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

    tables = [
        """CREATE TABLE Users (
            uid INT AUTO_INCREMENT PRIMARY KEY,
            fname VARCHAR(50), lname VARCHAR(50), 
            email VARCHAR(100) UNIQUE, pswd VARCHAR(255)
        );""",
        """CREATE TABLE Product (
            pid INT AUTO_INCREMENT PRIMARY KEY,
            pname VARCHAR(255), p_description TEXT, p_category VARCHAR(100),
            msrp DECIMAL(10, 2) DEFAULT 0.00, tracking_url TEXT
        );""",
        """CREATE TABLE Cart (
            cid INT AUTO_INCREMENT PRIMARY KEY, uid INT, pid INT, cutoff DECIMAL(10, 2),
            FOREIGN KEY (uid) REFERENCES Users(uid) ON DELETE CASCADE,
            FOREIGN KEY (pid) REFERENCES Product(pid) ON DELETE CASCADE
        );""",
        """CREATE TABLE Sellers (
            sid INT AUTO_INCREMENT PRIMARY KEY, sname VARCHAR(100), saddr VARCHAR(255), s_url VARCHAR(500)
        );""",
        """CREATE TABLE Seller_Prices (
            spid INT AUTO_INCREMENT PRIMARY KEY, pid INT, sid INT, price DECIMAL(10, 2),
            price_dt DATETIME DEFAULT CURRENT_TIMESTAMP, sp_url VARCHAR(500),
            FOREIGN KEY (pid) REFERENCES Product(pid) ON DELETE CASCADE,
            FOREIGN KEY (sid) REFERENCES Sellers(sid) ON DELETE CASCADE
        );""",
        """CREATE TABLE Alerts (
            aid INT AUTO_INCREMENT PRIMARY KEY, pid INT, spid INT, uid INT,
            createdat DATETIME DEFAULT CURRENT_TIMESTAMP, active_status BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (pid) REFERENCES Product(pid) ON DELETE CASCADE,
            FOREIGN KEY (spid) REFERENCES Seller_Prices(spid) ON DELETE CASCADE,
            FOREIGN KEY (uid) REFERENCES Users(uid) ON DELETE CASCADE
        );"""
    ]

    for sql in tables: cursor.execute(sql)

    cursor.execute("""
    CREATE PROCEDURE InsertPrice(IN p_pid INT, IN p_sid INT, IN p_price DECIMAL(10,2), IN p_url VARCHAR(500))
    BEGIN
        INSERT INTO Seller_Prices (pid, sid, price, sp_url) VALUES (p_pid, p_sid, p_price, p_url);
    END
    """)

    # --- THE FIX: SMART TRIGGER ---
    # Stops duplicate alerts if same price was alerted in last 24h
    cursor.execute("""
    CREATE TRIGGER AfterPriceInsert AFTER INSERT ON Seller_Prices
    FOR EACH ROW
    BEGIN
        INSERT INTO Alerts (pid, spid, uid, active_status)
        SELECT c.pid, NEW.spid, c.uid, TRUE 
        FROM Cart c 
        WHERE c.pid = NEW.pid 
        AND NEW.price <= c.cutoff
        AND NOT EXISTS (
            SELECT 1 FROM Alerts a 
            JOIN Seller_Prices sp ON a.spid = sp.spid
            WHERE a.uid = c.uid 
            AND a.pid = c.pid 
            AND sp.price = NEW.price
            AND a.createdat > NOW() - INTERVAL 1 DAY
        );
    END
    """)

    print("[LOG] Inserting Users...")
    cursor.execute("INSERT INTO Users (fname, lname, email, pswd) VALUES ('Admin', 'User', 'admin@dealradar.com', 'admin@123')")
    cursor.execute("INSERT INTO Users (fname, lname, email, pswd) VALUES ('John', 'Doe', 'john@example.com', '1234')")
    cursor.execute("INSERT INTO Sellers (sname, s_url) VALUES ('Amazon', 'https://amazon.com')")
    
    conn.commit()
    print("🎉 SETUP COMPLETE!")

except Exception as e:
    print(f"\n❌ ERROR: {e}")
finally:
    if 'conn' in locals() and conn.open: conn.close()