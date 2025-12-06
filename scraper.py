import cloudscraper
from bs4 import BeautifulSoup
import db_manager
import time
import re
import logging
import utils 

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
    try:
        if not price_str: return None
        # Fix for ranges like "$10.99 - $20.00" -> takes 10.99
        if "to" in price_str: price_str = price_str.split("to")[0]
        if "-" in price_str: price_str = price_str.split("-")[0]
        
        clean = re.sub(r'[^\d.]', '', price_str)
        return float(clean)
    except:
        return None

def clean_amazon_url(url):
    try:
        # Strips tracking params to avoid "Invalid Link" errors
        match = re.search(r'/(dp|gp/product)/([A-Z0-9]{10})', url)
        if match:
            asin = match.group(2)
            return f"https://www.amazon.com/dp/{asin}", asin
    except: pass
    return url, None

def scrape_direct_url(url, sid="NO-ID"):
    clean_url, asin = clean_amazon_url(url)
    logger.info(f"[{sid}] Requesting URL: {clean_url}")
    
    try:
        # 2. Fetch the page
        resp = scraper.get(clean_url, timeout=15)
        if resp.status_code != 200:
            logger.error(f"[{sid}] Amazon Status: {resp.status_code}")
            return None, None

        soup = BeautifulSoup(resp.content, "html.parser")
        
        if "captcha" in soup.get_text().lower():
            logger.warning(f"[{sid}] Amazon CAPTCHA detected.")
            return None, None

        # 3. Extract Title
        title_tag = soup.select_one("#productTitle") or soup.select_one("h1")
        raw_title = title_tag.text.strip() if title_tag else f"Amazon Item ({asin})"
        title = utils.safe_log(raw_title)

        # 4. EXTRACT PRICE (The Fix)
        # We prioritize the "Center Column" or "Core Price Div" to avoid sidebar accessories.
        price_val = None
        
        # Selector A: The "Apex" Price (Big deal price in middle of screen)
        price_tag = soup.select_one("#corePriceDisplay_desktop_feature_div .apexPriceToPay span.a-offscreen")
        
        # Selector B: The Standard Price block (Middle of screen)
        if not price_tag:
            price_tag = soup.select_one("#corePrice_feature_div span.a-offscreen")
            
        # Selector C: Fallback to your old logic if A and B fail
        if not price_tag:
            price_tag = soup.select_one("span.a-price span.a-offscreen") or soup.select_one("#priceblock_ourprice")
        
        if price_tag:
            price_val = clean_price(price_tag.text)
            logger.info(f"[{sid}] Found Price: ${price_val}")
        else:
            logger.warning(f"[{sid}] Title found but NO price.")

        return price_val, title

    except Exception as e:
        logger.error(f"[{sid}] Scrape Error: {e}")
        return None, None

# --- JOB FUNCTIONS (Required by app.py) ---

def run_scraper_job(sid="NO-ID"):
    logger.info(f"[{sid}] Starting Direct Link Job...")
    products = db_manager.run_query("SELECT * FROM Product WHERE tracking_url IS NOT NULL")
    if products.empty: return "No Amazon links."

    sellers = db_manager.run_query("SELECT sid FROM Sellers WHERE sname='Amazon'")
    if sellers.empty: return "Amazon ID missing."
    amazon_id = sellers.iloc[0]['sid']

    count = 0
    success = 0
    
    for _, product in products.iterrows():
        url = product['tracking_url']
        pid = product['pid']
        price, _ = scrape_direct_url(url, sid)
        
        if price:
            db_manager.call_insert_price_procedure(int(pid), int(amazon_id), float(price), url)
            success += 1
        count += 1
        time.sleep(2) # Polite delay

    return f"Scanned {count} links. Updated {success} prices."

def auto_discover_from_url(url, sid="NO-ID"):
    clean_url, asin = clean_amazon_url(url)
    price, title = scrape_direct_url(clean_url, sid)
    
    if title or asin:
        final_title = title if title else f"Amazon Item {asin}"
        return {
            "pname": final_title[:200],
            "category": "Amazon Import", 
            "msrp": price if price else 0.00,
            "tracking_url": clean_url
        }
    return None