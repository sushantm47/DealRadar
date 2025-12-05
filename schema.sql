CREATE DATABASE IF NOT EXISTS dealradar;
USE dealradar;

-- 1. HARD RESET (Clean Slate)
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS Alerts;
DROP TABLE IF EXISTS User_News;
DROP TABLE IF EXISTS Cart;
DROP TABLE IF EXISTS Seller_Prices;
DROP TABLE IF EXISTS Sellers;
DROP TABLE IF EXISTS Product;
DROP TABLE IF EXISTS News;
DROP TABLE IF EXISTS Users;
DROP PROCEDURE IF EXISTS InsertPrice;
DROP TRIGGER IF EXISTS AfterPriceInsert;
SET FOREIGN_KEY_CHECKS = 1;

-- 2. CREATE TABLES
CREATE TABLE Users (
    uid INT AUTO_INCREMENT PRIMARY KEY,
    fname VARCHAR(50),
    lname VARCHAR(50),
    email VARCHAR(100) UNIQUE,
    pswd VARCHAR(255)
);

CREATE TABLE Product (
    pid INT AUTO_INCREMENT PRIMARY KEY,
    pname VARCHAR(255),
    p_description TEXT,
    p_category VARCHAR(100)
);

CREATE TABLE Cart (
    cid INT AUTO_INCREMENT PRIMARY KEY,
    uid INT,
    pid INT,
    cutoff DECIMAL(10, 2),
    FOREIGN KEY (uid) REFERENCES Users(uid) ON DELETE CASCADE,
    FOREIGN KEY (pid) REFERENCES Product(pid) ON DELETE CASCADE
);

CREATE TABLE Sellers (
    sid INT AUTO_INCREMENT PRIMARY KEY,
    sname VARCHAR(100),
    saddr VARCHAR(255),
    s_url VARCHAR(500)
);

CREATE TABLE Seller_Prices (
    spid INT AUTO_INCREMENT PRIMARY KEY,
    pid INT,
    sid INT,
    price DECIMAL(10, 2),
    price_dt DATETIME DEFAULT CURRENT_TIMESTAMP,
    sp_url VARCHAR(500),
    FOREIGN KEY (pid) REFERENCES Product(pid) ON DELETE CASCADE,
    FOREIGN KEY (sid) REFERENCES Sellers(sid) ON DELETE CASCADE
);

CREATE TABLE Alerts (
    aid INT AUTO_INCREMENT PRIMARY KEY,
    pid INT,
    spid INT,
    uid INT,
    createdat DATETIME DEFAULT CURRENT_TIMESTAMP,
    active_status BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (pid) REFERENCES Product(pid) ON DELETE CASCADE,
    FOREIGN KEY (spid) REFERENCES Seller_Prices(spid) ON DELETE CASCADE,
    FOREIGN KEY (uid) REFERENCES Users(uid) ON DELETE CASCADE
);

CREATE TABLE News (
    nid INT AUTO_INCREMENT PRIMARY KEY,
    category VARCHAR(100),
    n_content TEXT,
    ndate DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE User_News (
    uid INT,
    nid INT,
    PRIMARY KEY (uid, nid),
    FOREIGN KEY (uid) REFERENCES Users(uid) ON DELETE CASCADE,
    FOREIGN KEY (nid) REFERENCES News(nid) ON DELETE CASCADE
);

-- 3. STORED PROCEDURE & TRIGGER
DELIMITER //

-- Procedure to Insert Price safely
CREATE PROCEDURE InsertPrice(
    IN p_pid INT,
    IN p_sid INT,
    IN p_price DECIMAL(10,2),
    IN p_url VARCHAR(500)
)
BEGIN
    INSERT INTO Seller_Prices (pid, sid, price, sp_url)
    VALUES (p_pid, p_sid, p_price, p_url);
END //

-- Trigger to Auto-Generate Alerts
CREATE TRIGGER AfterPriceInsert
AFTER INSERT ON Seller_Prices
FOR EACH ROW
BEGIN
    INSERT INTO Alerts (pid, spid, uid, active_status)
    SELECT c.pid, NEW.spid, c.uid, TRUE
    FROM Cart c
    WHERE c.pid = NEW.pid
    AND NEW.price < c.cutoff;
END //

DELIMITER ;