"""End-to-end pipeline test with an in-memory stand-in for db_manager (no PostgreSQL needed)."""
import os

import pipeline

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "sample_data", "price_feed_sample.csv")


class FakeDB:
    def __init__(self):
        self.sellers, self.products, self.prices, self.rejected = {}, {}, [], []

    def get_or_create_sellers(self, names):
        for n in names:
            self.sellers.setdefault(n, len(self.sellers) + 1)
        return {n: self.sellers[n] for n in names}

    def get_or_create_products(self, rows):
        for url, name, *_ in rows:
            if url not in self.products and name:
                self.products[url] = len(self.products) + 1
        return {u: self.products[u] for u, *_ in rows if u in self.products}

    def get_last_prices(self, pids):
        return {(p, s): price for p, s, price, _ in self.prices if p in set(pids)}

    def bulk_insert_prices(self, rows):
        self.prices.extend(rows)
        return len(rows)

    def log_rejected(self, records, source):
        self.rejected.extend(records)
        return len(records)


def test_sample_feed(monkeypatch):
    db = FakeDB()
    for name in ["get_or_create_sellers", "get_or_create_products", "get_last_prices", "bulk_insert_prices", "log_rejected"]:
        monkeypatch.setattr(pipeline.db_manager, name, getattr(db, name))

    s = pipeline.ingest_csv(SAMPLE)
    assert s["received"] == 13
    assert s["inserted"] == 4          # Amazon, Best Buy, Target, Walmart (latest of the duplicate pair)
    assert s["rejected"] == 9
    assert s["reasons"] == {"missing_price": 1, "malformed_price": 1, "seller_domain_mismatch": 1,
                            "non_positive_price": 1, "unsupported_currency": 1, "invalid_url": 1,
                            "price_out_of_range": 1, "duplicate_in_batch": 1, "malformed_csv_row": 1}
    walmart = [r for r in db.prices if "walmart" in r[3]]
    assert [float(r[2]) for r in walmart] == [187.5]
    assert all(r["reason"] for r in db.rejected)

    # second run: an absurd price for a known product is caught by the anomaly check
    s2 = pipeline.ingest([{"product_url": "https://www.amazon.com/dp/B0BX2L8PBT", "seller": "Amazon",
                           "price": "$3.29", "product_name": "Sony WH-1000XM5"}], source="test")
    assert s2["inserted"] == 0 and s2["reasons"] == {"price_anomaly": 1}
