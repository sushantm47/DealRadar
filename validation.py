"""
Validation + normalization for incoming price records.

Every source (Amazon scraper, structured-data scraper, CSV feeds) produces
records shaped like:

    {"product_url": str, "seller": str, "price": str|float, "currency": str|None,
     "product_name": str|None, "pid": int|None, "error": str|None}

validate_prices() splits a batch into (valid, rejected). Rejected rows carry a
`reason` so they can be logged to the Rejected_Records table instead of being
silently dropped or written as bad data.

This module has no database dependency so it can be unit-tested directly.
"""
import math
import re
from decimal import Decimal
from urllib.parse import urlparse, urlunparse

import numpy as np
import pandas as pd

# Sellers the pipeline knows how to verify (seller name -> expected domain)
SELLER_DOMAINS = {
    "Amazon": "amazon.com",
    "Best Buy": "bestbuy.com",
    "Walmart": "walmart.com",
    "Target": "target.com",
    "Newegg": "newegg.com",
    "eBay": "ebay.com",
}

MAX_PRICE = 50_000.00          # anything above this is almost certainly a parse error
ANOMALY_LOW, ANOMALY_HIGH = 0.10, 10.0   # >90% drop or >10x jump vs. last price => suspicious
ACCEPTED_CURRENCIES = {"USD"}  # US-focused dashboard

RECORD_COLUMNS = ["product_url", "seller", "price", "currency", "product_name", "pid", "error"]

_PRICE_RE = re.compile(r"(-)?\s*\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_MULTI_DOT_RE = re.compile(r"\d\.\d+\.\d")


def parse_price(value):
    """Turn '$1,299.99', '19.99 - 29.99', 24.5, Decimal('3.10') into a float. None if unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal, np.number)):
        f = float(value)
        return f if math.isfinite(f) else None
    s = str(value).strip()
    if not s or _MULTI_DOT_RE.search(s):
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    num = float(m.group(2).replace(",", ""))
    return -num if m.group(1) else num


def canonical_url(url):
    """Amazon -> https://www.amazon.com/dp/<ASIN>; everything else -> tracking params stripped."""
    if not isinstance(url, str):
        return url
    url = url.strip()
    m = re.search(r"/(dp|gp/product)/([A-Z0-9]{10})", url)
    if m and "amazon." in url:
        return f"https://www.amazon.com/dp/{m.group(2)}"
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", "", ""))


def seller_for_url(url):
    """Return the known seller name for a URL's domain, or None."""
    host = (urlparse(url).hostname or "").lower() if isinstance(url, str) else ""
    for name, domain in SELLER_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def normalize_seller(name):
    if not isinstance(name, str):
        return name
    name = name.strip()
    for known in SELLER_DOMAINS:
        if name.lower() == known.lower():
            return known
    return name


def _blank(series):
    return series.map(lambda v: v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == "")


def validate_prices(records):
    """
    Split a batch of raw records into (valid_df, rejected_df).
    valid_df.price is a clean float; rejected_df has a `reason` column.
    """
    df = pd.DataFrame(records).copy() if not isinstance(records, pd.DataFrame) else records.copy()
    for col in RECORD_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df.astype({c: object for c in RECORD_COLUMNS})
    df["reason"] = pd.Series([None] * len(df), index=df.index, dtype=object)

    def fail(mask, reason):
        m = mask & df["reason"].isna()
        if isinstance(reason, pd.Series):
            df.loc[m, "reason"] = reason[m]
        else:
            df.loc[m, "reason"] = reason

    # 0. upstream fetch errors (HTTP error, CAPTCHA, unsupported site...)
    fail(~_blank(df["error"]), df["error"].astype(object))

    # 1. URL checks
    fail(_blank(df["product_url"]), "missing_url")
    bad_scheme = ~df["product_url"].map(lambda u: isinstance(u, str) and bool(re.match(r"^https?://[^/\s]+", u.strip())))
    fail(bad_scheme, "invalid_url")
    df["product_url"] = df["product_url"].map(canonical_url)

    # 2. seller checks
    fail(_blank(df["seller"]), "missing_seller")
    df["seller"] = df["seller"].map(normalize_seller)
    mismatch = df.apply(
        lambda r: r["seller"] in SELLER_DOMAINS
        and isinstance(r["product_url"], str)
        and seller_for_url(r["product_url"]) != r["seller"],
        axis=1,
    ) if len(df) else pd.Series(dtype=bool)
    fail(mismatch.astype(bool), "seller_domain_mismatch")

    # 3. price checks
    fail(_blank(df["price"]), "missing_price")
    df["price_value"] = df["price"].map(parse_price).astype(object)
    fail(df["price_value"].isna(), "malformed_price")
    pv = pd.to_numeric(df["price_value"], errors="coerce")
    fail(pv <= 0, "non_positive_price")
    fail(pv > MAX_PRICE, "price_out_of_range")

    # 4. currency (blank is allowed and assumed USD)
    cur = df["currency"].map(lambda c: str(c).strip().upper() if isinstance(c, str) and c.strip() else None)
    fail(cur.map(lambda c: not pd.isna(c) and c not in ACCEPTED_CURRENCIES).astype(bool), "unsupported_currency")
    df["currency"] = cur.fillna("USD") if len(df) else cur

    # 5. duplicates inside the same batch (keep the last observation)
    still_ok = df["reason"].isna()
    dup = df[still_ok].duplicated(subset=["product_url", "seller"], keep="last")
    fail(dup.reindex(df.index, fill_value=False), "duplicate_in_batch")

    ok = df["reason"].isna()
    valid = df[ok].copy()
    valid["price"] = valid["price_value"].astype(float)
    rejected = df[~ok].copy()
    return valid.drop(columns=["price_value", "reason"]), rejected.drop(columns=["price_value"])


def flag_anomalies(valid, last_prices, low=ANOMALY_LOW, high=ANOMALY_HIGH):
    """
    Reject prices that jump implausibly vs. the last stored price for the same (pid, sid).
    This catches scraper mistakes like picking up an accessory's price.
    `last_prices` is {(pid, sid): float}. Returns (valid, rejected).
    """
    if valid.empty or not last_prices:
        return valid, valid.iloc[0:0].assign(reason=pd.Series(dtype=object))
    last = valid.apply(lambda r: last_prices.get((int(r["pid"]), int(r["sid"]))), axis=1)
    ratio = valid["price"] / pd.to_numeric(last, errors="coerce")
    bad = ((ratio < low) | (ratio > high)).fillna(False)
    rejected = valid[bad].copy()
    rejected["reason"] = "price_anomaly"
    rejected["last_price"] = last[bad]
    return valid[~bad].copy(), rejected
