"""
DealRadar ingestion pipeline.

    sources (scraper.py / CSV feeds) -> validation.validate_prices -> id mapping
    -> validation.flag_anomalies -> bulk insert into Seller_Prices
                                 -> rejected rows logged to Rejected_Records

CLI:
    python pipeline.py --scrape                      # re-scrape all tracked products
    python pipeline.py --csv sample_data/price_feed_sample.csv
"""
import argparse
import logging
from collections import Counter

import pandas as pd

import db_manager
import validation

logger = logging.getLogger(__name__)
CSV_CHUNK_ROWS = 50_000


def _empty_summary():
    return {"received": 0, "inserted": 0, "rejected": 0, "reasons": {}}


def _merge(a, b):
    reasons = Counter(a["reasons"]) + Counter(b["reasons"])
    return {"received": a["received"] + b["received"], "inserted": a["inserted"] + b["inserted"],
            "rejected": a["rejected"] + b["rejected"], "reasons": dict(reasons)}


def ingest(records, source="manual"):
    """Validate and store a batch of raw price records. Returns a summary dict."""
    df = records.copy() if isinstance(records, pd.DataFrame) else pd.DataFrame(list(records))
    summary = _empty_summary()
    summary["received"] = len(df)
    if df.empty:
        return summary

    valid, rejected = validation.validate_prices(df)
    rejected_frames = [rejected]

    if not valid.empty:
        # map seller names -> sid
        sellers = db_manager.get_or_create_sellers(valid["seller"].unique())
        valid["sid"] = valid["seller"].map(sellers)

        # map product URLs -> pid (scraper rows already carry pid; feed rows may create products)
        need = valid["pid"].isna()
        if need.any():
            rows = [(u, n if isinstance(n, str) and n.strip() else None, f"{s} Import", f"Imported from {source}")
                    for u, n, s in valid.loc[need, ["product_url", "product_name", "seller"]].itertuples(index=False)]
            products = db_manager.get_or_create_products(rows)
            valid.loc[need, "pid"] = valid.loc[need, "product_url"].map(products)
        unknown = valid["pid"].isna()
        rejected_frames.append(valid[unknown].assign(reason="unknown_product"))
        valid = valid[~unknown].copy()
        valid["pid"] = valid["pid"].astype(int)

        # compare against history to catch implausible jumps
        last = db_manager.get_last_prices(valid["pid"].unique())
        valid, anomalies = validation.flag_anomalies(valid, last)
        rejected_frames.append(anomalies)

        rows = list(zip(valid["pid"].astype(int), valid["sid"].astype(int),
                        valid["price"].round(2), valid["product_url"]))
        summary["inserted"] = db_manager.bulk_insert_prices(rows)

    rejected = pd.concat([f for f in rejected_frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in rejected_frames) else pd.DataFrame()
    if not rejected.empty:
        cols = [c for c in validation.RECORD_COLUMNS + ["reason", "last_price"] if c in rejected.columns]
        db_manager.log_rejected(rejected[cols].astype(object).to_dict("records"), source)
        summary["rejected"] = len(rejected)
        summary["reasons"] = rejected["reason"].value_counts().to_dict()

    logger.info(f"[pipeline:{source}] {summary}")
    return summary


def ingest_csv(path_or_buffer, source=None):
    """
    Import a price feed. Required columns: product_url, seller, price.
    Optional: product_name, currency. Read in chunks so large feeds don't need to fit in memory.
    """
    source = source or f"csv:{getattr(path_or_buffer, 'name', path_or_buffer)}"
    total = _empty_summary()
    bad_lines = []  # rows with the wrong number of fields (e.g. an unquoted "1,099.00")

    def on_bad(line):
        bad_lines.append({"raw_line": line, "reason": "malformed_csv_row"})
        return None  # skip it, keep reading

    reader = pd.read_csv(path_or_buffer, dtype=str, chunksize=CSV_CHUNK_ROWS,
                         engine="python", on_bad_lines=on_bad)
    for chunk in reader:
        chunk.columns = [c.strip().lower() for c in chunk.columns]
        missing = {"product_url", "seller", "price"} - set(chunk.columns)
        if missing:
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")
        total = _merge(total, ingest(chunk, source=source))
    if bad_lines:
        db_manager.log_rejected(bad_lines, source)
        total = _merge(total, {"received": len(bad_lines), "inserted": 0, "rejected": len(bad_lines),
                               "reasons": {"malformed_csv_row": len(bad_lines)}})
    return total


def track_product(uid, url, sid="NO-ID"):
    """Start tracking a product URL for a user (used by both the Flask and Streamlit UIs)."""
    import scraper
    details = scraper.auto_discover_from_url(url, sid)
    if not details:
        return False, "Unsupported store, invalid link, or the page was blocked."
    pid, _ = db_manager.get_or_create_product(details["pname"], f"{details['seller']} Import",
                                              details["category"], details["msrp"], details["tracking_url"])
    raw = dict(details["raw"], pid=pid)
    summary = ingest([raw], source="track")
    db_manager.add_to_cart(uid, pid, 0.00)
    note = "" if summary["inserted"] else f" (price not stored: {', '.join(summary['reasons'])})"
    return True, f"Tracking started for {details['pname'][:30]}{note}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
    ap = argparse.ArgumentParser(description="DealRadar ingestion pipeline")
    ap.add_argument("--csv", help="path to a price feed CSV")
    ap.add_argument("--scrape", action="store_true", help="re-scrape all tracked products")
    args = ap.parse_args()
    if args.csv:
        print(ingest_csv(args.csv))
    if args.scrape:
        import scraper
        print(scraper.refresh_all("CLI"))
    if not (args.csv or args.scrape):
        ap.print_help()
