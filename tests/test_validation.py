import pandas as pd

import validation as v


def test_parse_price_formats():
    assert v.parse_price("$1,299.99") == 1299.99
    assert v.parse_price("$19.99 - $29.99") == 19.99
    assert v.parse_price(24.5) == 24.5
    assert v.parse_price("-10.00") == -10.0
    for bad in [None, "", "Currently unavailable", "12.99.99", float("nan"), True]:
        assert v.parse_price(bad) is None


def test_canonical_url():
    assert v.canonical_url("https://www.amazon.com/Sony/dp/B0BX2L8PBT/ref=x?th=1") == "https://www.amazon.com/dp/B0BX2L8PBT"
    assert v.canonical_url("https://www.BestBuy.com/site/x.p?skuId=1#r") == "https://www.bestbuy.com/site/x.p"


def test_validate_prices_reasons():
    rows = [
        {"product_url": "https://www.amazon.com/dp/B0BX2L8PBT", "seller": "Amazon", "price": "$329.99"},
        {"product_url": "https://www.amazon.com/dp/B0CHX1W1XY", "seller": "Amazon", "price": None},
        {"product_url": "https://www.amazon.com/dp/B0D1XD1ZV3", "seller": "Amazon", "price": "N/A"},
        {"product_url": "https://www.bestbuy.com/site/tv.p", "seller": "Amazon", "price": "10"},
        {"product_url": "https://www.target.com/p/x", "seller": "target", "price": "-1"},
        {"product_url": "https://www.ebay.com/itm/1", "seller": "eBay", "price": "5", "currency": "GBP"},
        {"product_url": "not-a-url", "seller": "Walmart", "price": "5"},
        {"product_url": "https://www.newegg.com/p/1", "seller": "Newegg", "price": "99999"},
        {"product_url": "https://www.amazon.com/dp/B0BX2L8PBT", "seller": "Amazon", "price": None, "error": "captcha"},
    ]
    valid, rejected = v.validate_prices(rows)
    assert len(valid) == 1 and valid.iloc[0]["price"] == 329.99 and valid.iloc[0]["currency"] == "USD"
    assert list(rejected["reason"]) == [
        "missing_price", "malformed_price", "seller_domain_mismatch", "non_positive_price",
        "unsupported_currency", "invalid_url", "price_out_of_range", "captcha"]


def test_duplicates_keep_last():
    rows = [{"product_url": "https://www.walmart.com/ip/1", "seller": "Walmart", "price": p} for p in ("10", "11")]
    valid, rejected = v.validate_prices(rows)
    assert list(valid["price"]) == [11.0] and list(rejected["reason"]) == ["duplicate_in_batch"]


def test_flag_anomalies():
    df = pd.DataFrame({"pid": [1, 2, 3], "sid": [1, 1, 1], "price": [5.0, 95.0, 2000.0]})
    ok, bad = v.flag_anomalies(df, {(1, 1): 100.0, (2, 1): 100.0, (3, 1): 100.0})
    assert list(ok["pid"]) == [2] and sorted(bad["pid"]) == [1, 3]
