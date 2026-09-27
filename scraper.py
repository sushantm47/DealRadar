import json
import logging
import time

import cloudscraper
from bs4 import BeautifulSoup

import db_manager
import utils
import validation

logger = logging.getLogger(__name__)

# 1. Setup CloudScraper (Your working configuration)
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)
scraper.headers.update({
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.google.com/'
})


def clean_price(price_str):
    # Kept for backwards compatibility; parsing rules now live in validation.py
    return validation.parse_price(price_str)


def clean_amazon_url(url):
    clean = validation.canonical_url(url)
    asin = clean.rsplit("/", 1)[-1] if "amazon.com/dp/" in clean else None
    return clean, asin


# ---------------------------------------------------------------------------
# Fetch + per-source extractors. Extractors return RAW values; validation.py
# decides whether they are usable, so malformed data gets logged, not guessed at.
# ---------------------------------------------------------------------------

def _fetch(url, sid):
    resp = scraper.get(url, timeout=15)
    if resp.status_code != 200:
        logger.error(f"[{sid}] HTTP {resp.status_code} for {url}")
        return None, f"http_{resp.status_code}"
    soup = BeautifulSoup(resp.content, "html.parser")
    if "captcha" in soup.get_text().lower()[:5000]:
        logger.warning(f"[{sid}] CAPTCHA detected at {url}")
        return None, "captcha"
    return soup, None


def extract_amazon(soup, asin=None):
    title_tag = soup.select_one("#productTitle") or soup.select_one("h1")
    title = title_tag.text.strip() if title_tag else (f"Amazon Item ({asin})" if asin else None)
    # Prioritise the centre-column price to avoid sidebar accessories
    price_tag = (soup.select_one("#corePriceDisplay_desktop_feature_div .apexPriceToPay span.a-offscreen")
                 or soup.select_one("#corePrice_feature_div span.a-offscreen")
                 or soup.select_one("span.a-price span.a-offscreen")
                 or soup.select_one("#priceblock_ourprice"))
    return {"product_name": title, "price": price_tag.text.strip() if price_tag else None, "currency": "USD"}


def _iter_jsonld(soup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                for key in ("@graph", "mainEntity", "itemListElement", "item"):
                    if key in node:
                        stack.append(node[key])


def extract_structured(soup):
    """Generic extractor for retailers that publish schema.org Product data (JSON-LD or meta tags)."""
    for node in _iter_jsonld(soup):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" not in types:
            continue
        offers = node.get("offers") or []
        offers = offers if isinstance(offers, list) else [offers]
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            spec = offer.get("priceSpecification")
            spec = spec[0] if isinstance(spec, list) and spec else spec
            price = offer.get("price", offer.get("lowPrice"))
            if price is None and isinstance(spec, dict):
                price = spec.get("price")
            if price is not None:
                return {"product_name": node.get("name"), "price": price,
                        "currency": offer.get("priceCurrency") or (spec or {}).get("priceCurrency")}
        return {"product_name": node.get("name"), "price": None, "currency": None}

    meta_price = soup.select_one('meta[property="product:price:amount"], meta[property="og:price:amount"], meta[itemprop="price"]')
    meta_cur = soup.select_one('meta[property="product:price:currency"], meta[property="og:price:currency"], meta[itemprop="priceCurrency"]')
    title = soup.select_one('meta[property="og:title"]')
    return {"product_name": title.get("content") if title else (soup.title.string.strip() if soup.title and soup.title.string else None),
            "price": meta_price.get("content") if meta_price else None,
            "currency": meta_cur.get("content") if meta_cur else None}


def scrape_product(url, sid="NO-ID"):
    """Return one raw price record for any supported retailer URL (never raises)."""
    seller = validation.seller_for_url(url)
    clean_url = validation.canonical_url(url)
    record = {"product_url": clean_url, "seller": seller, "price": None, "currency": None, "product_name": None, "error": None}
    if not seller:
        record["error"] = "unsupported_source"
        return record
    logger.info(f"[{sid}] Requesting {seller}: {clean_url}")
    try:
        soup, err = _fetch(clean_url, sid)
        if err:
            record["error"] = err
            return record
        _, asin = clean_amazon_url(clean_url)
        data = extract_amazon(soup, asin) if seller == "Amazon" else extract_structured(soup)
        record.update({k: v for k, v in data.items() if v is not None})
        if record["product_name"]:
            record["product_name"] = utils.safe_log(str(record["product_name"]))[:200]
        logger.info(f"[{sid}] {seller} raw price: {record['price']}")
    except Exception as e:
        logger.error(f"[{sid}] Scrape Error: {e}")
        record["error"] = "fetch_failed"
    return record


def scrape_direct_url(url, sid="NO-ID"):
    # Old interface: (price, title)
    rec = scrape_product(url, sid)
    return validation.parse_price(rec["price"]), rec["product_name"]


# --- JOB FUNCTIONS (Required by app.py / streamlit_app.py) ---

def refresh_all(sid="NO-ID"):
    """Re-scrape every tracked product across all sources and push the batch through the pipeline."""
    import pipeline  # local import avoids a circular import
    logger.info(f"[{sid}] Starting multi-source refresh...")
    products = db_manager.run_query("SELECT pid, tracking_url FROM Product WHERE tracking_url IS NOT NULL")
    if products.empty:
        return {"received": 0, "inserted": 0, "rejected": 0, "reasons": {}}
    records = []
    for _, product in products.iterrows():
        rec = scrape_product(product['tracking_url'], sid)
        rec["pid"] = int(product['pid'])
        records.append(rec)
        time.sleep(2)  # Polite delay
    return pipeline.ingest(records, source="scraper")


def run_scraper_job(sid="NO-ID"):
    s = refresh_all(sid)
    if not s["received"]:
        return "No tracked links."
    return f"Scanned {s['received']} links. Inserted {s['inserted']} prices, rejected {s['rejected']} ({pipeline_reasons(s)})."


def pipeline_reasons(summary):
    return ", ".join(f"{k}: {v}" for k, v in summary["reasons"].items()) or "none"


def auto_discover_from_url(url, sid="NO-ID"):
    rec = scrape_product(url, sid)
    if rec["error"] == "unsupported_source":
        return None
    if rec["product_name"] or rec["price"] is not None:
        return {
            "pname": rec["product_name"] or f"{rec['seller']} Item",
            "category": f"{rec['seller']} Import",
            "msrp": validation.parse_price(rec["price"]) or 0.00,
            "tracking_url": rec["product_url"],
            "seller": rec["seller"],
            "raw": rec,
        }
    return None
