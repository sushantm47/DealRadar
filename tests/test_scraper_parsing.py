from bs4 import BeautifulSoup

import scraper

JSONLD = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},
 {"@type":"Product","name":"Sony WH-1000XM5","offers":{"@type":"Offer","price":"349.99","priceCurrency":"USD"}}]}
</script></head></html>"""

AGG = """<script type="application/ld+json">[{"@type":["Product"],"name":"AirPods Pro",
 "offers":{"@type":"AggregateOffer","lowPrice":189,"priceCurrency":"USD"}}]</script>"""

META = """<html><head><meta property="og:title" content="Air Fryer">
<meta property="product:price:amount" content="79.00"><meta property="product:price:currency" content="USD"></head></html>"""

AMAZON = """<span id="productTitle"> Kindle </span>
<div id="corePrice_feature_div"><span class="a-offscreen">$139.99</span></div>
<div id="sidebar"><span class="a-price"><span class="a-offscreen">$9.99</span></span></div>"""


def test_jsonld_graph():
    assert scraper.extract_structured(BeautifulSoup(JSONLD, "html.parser")) == \
        {"product_name": "Sony WH-1000XM5", "price": "349.99", "currency": "USD"}


def test_jsonld_aggregate_offer():
    assert scraper.extract_structured(BeautifulSoup(AGG, "html.parser"))["price"] == 189


def test_meta_tag_fallback():
    r = scraper.extract_structured(BeautifulSoup(META, "html.parser"))
    assert r == {"product_name": "Air Fryer", "price": "79.00", "currency": "USD"}


def test_amazon_prefers_center_column_price():
    r = scraper.extract_amazon(BeautifulSoup(AMAZON, "html.parser"))
    assert r["product_name"] == "Kindle" and r["price"] == "$139.99"


def test_unsupported_store_is_flagged_without_fetching():
    assert scraper.scrape_product("https://www.example.com/item/1")["error"] == "unsupported_source"
