# DealRadar: RAG-Enabled Price Monitoring Dashboard

DealRadar tracks US retail prices across multiple stores, validates every incoming price record before storing it in PostgreSQL, and includes an LLM product-insights assistant that answers questions **only** from the stored data, rejecting any answer that fails grounding checks.

**Stack:** Python 3.10+, PostgreSQL 12+, Pandas, Streamlit (dashboard), Flask (original web app), BeautifulSoup/CloudScraper, Anthropic Claude API.

---

## Architecture

```
 Sources                      Pipeline (pipeline.py)                    PostgreSQL
 ─────────                    ──────────────────────                    ──────────
 Amazon (HTML selectors) ─┐                                          ┌─ Seller_Prices ── trigger ─> Alerts
 Best Buy / Walmart /     ├─> raw records ─> validation.py ─> id ────┤
 Target / Newegg / eBay   │                 (rules below)   mapping  └─ Rejected_Records (reason + raw JSON)
 (schema.org JSON-LD)     │                        │           │
 CSV price feeds ─────────┘                        └─ anomaly check vs. last stored price

 RSS deal news (Slickdeals, CNET, ...) ──────────────────────────────> News

 Assistant (rag.py):  question ─> full-text retrieval (GIN tsvector indexes) ─> context docs
                      ─> Claude (JSON answer + citations) ─> grounding checks ─> answer or fallback
                      every question logged to Rag_Queries
```

## Project structure

| File | Purpose |
|---|---|
| `scraper.py` | Source adapters: Amazon selectors + a generic schema.org JSON-LD / meta-tag extractor for other stores |
| `validation.py` | Pure normalization and validation rules (no DB dependency; unit-tested) |
| `pipeline.py` | Orchestrates validate → map IDs → anomaly check → bulk insert → log rejects; CSV import; CLI |
| `db_manager.py` | PostgreSQL access (psycopg2), bulk helpers |
| `rag.py` | Retrieval, prompt, LLM call, grounding checks |
| `streamlit_app.py` | Dashboard: watchlist, price history, pipeline runs & rejects, assistant |
| `app.py` + `templates/` | Original Flask web app (now on PostgreSQL + the validated pipeline) |
| `schema.sql` / `setup_db.py` | Schema, indexes, procedure, trigger, seed data / one-command setup |
| `sample_data/price_feed_sample.csv` | Sample feed that includes intentionally malformed rows |
| `tests/` | Unit tests (run without a database) |

---

## Ingestion pipeline

### Sources
1. **Amazon:** existing CloudScraper + BeautifulSoup scraper. It prefers the centre-column price so accessory prices in the sidebar aren't picked up.
2. **Best Buy, Walmart, Target, Newegg, eBay:** a generic extractor that reads schema.org `Product`/`Offer` data from JSON-LD (including `@graph` and `AggregateOffer`), falling back to `product:price:amount` / `og:price` meta tags.
3. **CSV price feeds:** `product_url, seller, price[, currency, product_name]`, read in 50,000-row chunks so large files never need to fit in memory.

Scrapers return **raw** values. Parsing and judgement happen in one place (`validation.py`), so every source is held to the same rules.

### Validation rules
A record is rejected with the first reason that applies. Rejected records are written to `Rejected_Records` with the raw payload, never silently dropped.

| Reason | Rule |
|---|---|
| `http_4xx/5xx`, `captcha`, `fetch_failed`, `unsupported_source` | Upstream fetch problems reported by the scraper |
| `missing_url` / `invalid_url` | URL empty or not `http(s)://host/...` |
| `missing_seller` | No seller given |
| `seller_domain_mismatch` | Seller is a known store but the URL belongs to a different domain |
| `missing_price` / `malformed_price` | Price empty, or unparseable (`"N/A"`, `"12.99.99"`, `"Currently unavailable"`) |
| `non_positive_price` / `price_out_of_range` | Price ≤ 0 or > $50,000 |
| `unsupported_currency` | Currency present and not USD (blank is assumed USD) |
| `duplicate_in_batch` | Same product + seller appears again in the batch (last one wins) |
| `unknown_product` | URL not tracked and no product name to create it from |
| `price_anomaly` | New price is < 10% or > 10× the last stored price for that product/seller (usually a scraper mistake) |
| `malformed_csv_row` | CSV line with the wrong number of fields (e.g. an unquoted `1,099.00`) |

Normalization: prices like `"$1,299.99"` or `"$19.99 - $29.99"` become floats; Amazon URLs are canonicalized to `https://www.amazon.com/dp/<ASIN>`; other URLs drop tracking query strings; seller names are case-normalized. The database enforces `CHECK (price > 0)` as a final safeguard.

Valid rows are bulk-inserted with `execute_values` (one round-trip per 1,000 rows). The existing `AfterPriceInsert` trigger still creates alerts when a price drops below a user's target.

---

## Product-insights assistant (RAG)

1. **Retrieve.** The question is turned into an OR full-text query against `Product.search_tsv` (a generated, GIN-indexed `tsvector` over name, category and description). If nothing matches and the user is signed in, the user's watchlist is used instead. For each product, SQL aggregates build a context document with per-seller latest, lowest, highest and average prices, number of observations, change since first observation, cheapest seller, the gap to the user's target, and recent observations. Related headlines come from `News.search_tsv`. Each document gets an ID (`P12`, `N7`).
2. **Generate.** Claude receives only those documents. It is instructed to state only numbers that appear in the context and to return JSON: `{"answer", "citations", "insufficient_context"}`.
3. **Grounding checks** (`rag.check_grounding`). The answer is **rejected** and replaced with a fallback message if any of these fail:
   - the output isn't valid JSON of that shape;
   - the model reports insufficient context;
   - there are no citations, or it cites a document that wasn't retrieved;
   - any price, percentage, decimal, or number > 10 in the answer isn't found in the *cited* documents (or the user's question). Whole-number rounding of a context value is allowed; bare small counts (≤ 10) are ignored.

   If retrieval returns nothing, the LLM is never called. LLM/API errors also produce the fallback instead of a crash. Every question, answer, citation list and verdict is logged to `Rag_Queries`.

---

## Setup

### Prerequisites
- Python 3.10+
- PostgreSQL 12+ (<https://www.postgresql.org/download/>)
- An Anthropic API key (only needed for the assistant)

### 1. Clone and install
```bash
git clone https://github.com/sushantm47/DealRadar.git
cd DealRadar
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env     # then edit DB_PASSWORD, FLASK_SECRET_KEY, ANTHROPIC_API_KEY
```

### 3. Create the database
```bash
python setup_db.py
```
This creates the `dealradar` database if needed and applies `schema.sql`. It **resets all tables**. Demo login: `test` / `test@123`.

### 4. Load sample data (optional)
```bash
python pipeline.py --csv sample_data/price_feed_sample.csv
```
Expected: 13 records received, 4 stored, 9 rejected, one for each kind of problem planted in the file. Inspect them with `SELECT reason, raw_record FROM Rejected_Records;`.

---

## Running

| What | Command |
|---|---|
| Streamlit dashboard | `streamlit run streamlit_app.py` → <http://localhost:8501> |
| Flask web app | `python app.py` → <http://127.0.0.1:5000> |
| Re-scrape all tracked products | `python pipeline.py --scrape` |
| Import a price feed | `python pipeline.py --csv path/to/feed.csv` |
| Tests (no DB needed) | `pytest -q` |

The dashboard tabs:
- **Watchlist:** add a product by URL, see current, lowest and first prices, and set target prices.
- **Price history:** a per-seller line chart.
- **Data pipeline:** refresh prices, upload a CSV feed, and see rejected records by reason.
- **Ask DealRadar:** chat with the assistant. Each answer shows its sources, or which grounding check blocked it.

---

## Known limitations
- Retailers change their markup and block bots. A store that serves a CAPTCHA or strips JSON-LD shows up as `captcha` or `missing_price` in `Rejected_Records` rather than as bad data.
- Scraping is sequential with a 2-second delay per product. Bulk volume comes from CSV feeds.
- Passwords are stored in plain text (demo only). Don't reuse real credentials.
- Retrieval uses PostgreSQL full-text search rather than vector embeddings. That's a good fit for product names and structured price facts; pgvector could be added for fuzzier semantic matching.
