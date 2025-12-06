import pymysql
import feedparser
import os
from dotenv import load_dotenv

load_dotenv()

# Database Config
DB_CFG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "dealradar")
}

# 1. THE SOURCE: Real RSS Feeds mapped to our Categories
RSS_SOURCES = {
    "Electronics": "https://www.theverge.com/rss/index.xml",
    "Home": "https://www.apartmenttherapy.com/main.xml",
    "Fashion": "https://www.gq.com/feed/style/rss"
}

def update_news_feed():
    """Run this function once every few hours to fill the DB with fresh news."""
    conn = pymysql.connect(**DB_CFG)
    cursor = conn.cursor()
    
    print("[LOG] Fetching latest news...")
    
    for category, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            # Take top 3 stories from each feed
            for entry in feed.entries[:3]:
                title = entry.title
                link = entry.link
                
                # Insert if not exists (Avoid duplicates based on URL)
                sql = """
                INSERT INTO News (category, title, n_url) 
                SELECT %s, %s, %s 
                WHERE NOT EXISTS (SELECT 1 FROM News WHERE n_url = %s)
                """
                cursor.execute(sql, (category, title, link, link))
                
        except Exception as e:
            print(f"Error fetching {category}: {e}")
            
    conn.commit()
    conn.close()
    print("[LOG] News database updated.")

def get_relevant_news_for_user(user_id):
    """
    MAGIC FUNCTION: Finds news based on what products the user is watching.
    No manual setup required by the user!
    """
    conn = pymysql.connect(**DB_CFG)
    cursor = conn.cursor()
    
    # LOGIC: 
    # 1. Look at Cart to find Product Categories
    # 2. Find News that matches those Categories
    sql = """
    SELECT DISTINCT n.category, n.title, n.n_url, n.published_at
    FROM News n
    JOIN Product p ON n.category = p.p_category
    JOIN Cart c ON p.pid = c.pid
    WHERE c.uid = %s
    ORDER BY n.published_at DESC
    LIMIT 10;
    """
    
    cursor.execute(sql, (user_id,))
    results = cursor.fetchall()
    conn.close()
    
    return results

# --- TEST RUN ---
if __name__ == "__main__":
    # 1. Scrape real news
    update_news_feed()
    
    # 2. Simulate User 1 (Who is watching "Sony Headphones" -> Electronics)
    print("\n--- User 1's Personalized News Feed ---")
    news = get_relevant_news_for_user(1)
    
    if not news:
        print("No relevant news found. (Try adding products to your cart!)")
    else:
        for item in news:
            print(f"[{item[0]}] {item[1]}\n   -> {item[2]}")