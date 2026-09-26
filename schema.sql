-- DealRadar schema - PostgreSQL 12+
-- Run via:  python setup_db.py   (or: psql -d dealradar -f schema.sql)

-- 1. RESET
DROP TABLE IF EXISTS Rag_Queries, Rejected_Records, Alerts, Cart, Seller_Prices,
                     Sellers, News, Product, Users CASCADE;
DROP PROCEDURE IF EXISTS InsertPrice(INT, INT, NUMERIC, TEXT);
DROP FUNCTION IF EXISTS after_price_insert() CASCADE;

-- 2. CORE TABLES
CREATE TABLE Users (
    uid        SERIAL PRIMARY KEY,
    fname      VARCHAR(50),
    lname      VARCHAR(50),
    email      VARCHAR(100) UNIQUE,
    pswd       VARCHAR(255),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Product (
    pid           SERIAL PRIMARY KEY,
    pname         VARCHAR(255),
    p_description TEXT,
    p_category    VARCHAR(100),
    msrp          NUMERIC(10, 2) DEFAULT 0.00,
    tracking_url  TEXT UNIQUE,
    -- RAG retrieval: full-text index over the product's descriptive fields
    search_tsv    TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(pname, '') || ' ' || coalesce(p_category, '') || ' ' || coalesce(p_description, ''))
    ) STORED
);
CREATE INDEX idx_product_search ON Product USING GIN (search_tsv);

CREATE TABLE Cart (
    cid    SERIAL PRIMARY KEY,
    uid    INT REFERENCES Users(uid) ON DELETE CASCADE,
    pid    INT REFERENCES Product(pid) ON DELETE CASCADE,
    cutoff NUMERIC(10, 2)
);

CREATE TABLE Sellers (
    sid    SERIAL PRIMARY KEY,
    sname  VARCHAR(100) UNIQUE NOT NULL,
    saddr  VARCHAR(255),
    s_url  VARCHAR(500)
);

CREATE TABLE Seller_Prices (
    spid     SERIAL PRIMARY KEY,
    pid      INT REFERENCES Product(pid) ON DELETE CASCADE,
    sid      INT REFERENCES Sellers(sid) ON DELETE CASCADE,
    price    NUMERIC(10, 2) NOT NULL CHECK (price > 0),   -- last line of defence behind validation.py
    price_dt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sp_url   TEXT
);
CREATE INDEX idx_prices_pid_dt ON Seller_Prices (pid, price_dt DESC);

CREATE TABLE Alerts (
    aid           SERIAL PRIMARY KEY,
    pid           INT REFERENCES Product(pid) ON DELETE CASCADE,
    spid          INT REFERENCES Seller_Prices(spid) ON DELETE CASCADE,
    uid           INT REFERENCES Users(uid) ON DELETE CASCADE,
    createdat     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active_status BOOLEAN DEFAULT TRUE
);

CREATE TABLE News (
    nid          SERIAL PRIMARY KEY,
    category     VARCHAR(100),
    title        TEXT,
    n_url        VARCHAR(500) UNIQUE,
    image_url    TEXT,
    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    search_tsv   TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(category, ''))
    ) STORED
);
CREATE INDEX idx_news_search ON News USING GIN (search_tsv);

-- 3. PIPELINE + RAG BOOKKEEPING
-- Every record the validation layer rejects lands here with a reason (never silently dropped)
CREATE TABLE Rejected_Records (
    rrid        SERIAL PRIMARY KEY,
    source      VARCHAR(100),
    reason      VARCHAR(100),
    raw_record  JSONB,
    rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_rejected_reason ON Rejected_Records (reason);

-- Every assistant question, the answer shown, what it cited, and whether it passed grounding checks
CREATE TABLE Rag_Queries (
    qid           SERIAL PRIMARY KEY,
    question      TEXT,
    answer        TEXT,
    cited_ids     TEXT[],
    grounded      BOOLEAN,
    reject_reason TEXT,
    model         VARCHAR(100),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. STORED PROCEDURE & TRIGGER
CREATE PROCEDURE InsertPrice(p_pid INT, p_sid INT, p_price NUMERIC, p_url TEXT)
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO Seller_Prices (pid, sid, price, sp_url) VALUES (p_pid, p_sid, p_price, p_url);
END;
$$;

CREATE FUNCTION after_price_insert() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO Alerts (pid, spid, uid, active_status)
    SELECT c.pid, NEW.spid, c.uid, TRUE
    FROM Cart c
    WHERE c.pid = NEW.pid
      AND NEW.price <= c.cutoff
      AND NOT EXISTS (
          SELECT 1 FROM Alerts a JOIN Seller_Prices sp ON a.spid = sp.spid
          WHERE a.uid = c.uid AND a.pid = c.pid AND sp.price = NEW.price
            AND a.createdat > NOW() - INTERVAL '1 day'
      );
    RETURN NEW;
END;
$$;

CREATE TRIGGER AfterPriceInsert
AFTER INSERT ON Seller_Prices
FOR EACH ROW EXECUTE FUNCTION after_price_insert();

-- 5. DEFAULT DATA
INSERT INTO Sellers (sname, s_url) VALUES
    ('Amazon',   'https://www.amazon.com'),
    ('Best Buy', 'https://www.bestbuy.com'),
    ('Walmart',  'https://www.walmart.com'),
    ('Target',   'https://www.target.com'),
    ('Newegg',   'https://www.newegg.com'),
    ('eBay',     'https://www.ebay.com');

INSERT INTO Users (fname, lname, email, pswd) VALUES ('Test', 'User', 'test', 'test@123');
INSERT INTO Users (fname, lname, email, pswd) VALUES ('Admin', 'User', 'admin@dealradar.com', 'admin');
