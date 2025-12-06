from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import db_manager
import scraper
import logging
import uuid
import utils 
import feedparser
import requests
import re
import os
import pandas as pd
from dotenv import load_dotenv

session_id = str(uuid.uuid4())[:8]
logging.basicConfig(filename='dealradar.log', level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

RSS_SOURCES = {
    "Amazon Official": "https://www.aboutamazon.com/feed/news",
    "Slickdeals": "https://feeds.feedburner.com/SlickdealsnetFP", 
    "CNET Deals": "https://www.cnet.com/rss/deals/",
    "Tom's Guide": "https://www.tomsguide.com/feeds/tag/amazon" 
}

def handle_db_error(e):
    if "1452" in str(e):
        session.clear()
        return redirect(url_for('login'))
    print(f"DB Error: {e}")
    raise e

@app.route('/')
def index():
    return redirect(url_for('dashboard')) if 'user_email' in session else redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        users = db_manager.run_query("SELECT * FROM Users WHERE email=%s", (request.form['email'],))
        if not users.empty:
            if str(users.iloc[0]['pswd']) == request.form['password']:
                session['user_id'] = int(users.iloc[0]['uid'])
                session['user_email'] = users.iloc[0]['email']
                session['user_name'] = users.iloc[0]['fname']
                session['is_admin'] = (request.form['email'] == 'admin@dealradar.com')
                return redirect(url_for('dashboard'))
        flash("User not found or password incorrect.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- API FOR GRAPHS ---
@app.route('/api/history/<int:pid>')
def get_price_history(pid):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    try:
        sql = "SELECT price, price_dt FROM Seller_Prices WHERE pid=%s ORDER BY price_dt ASC"
        df = db_manager.run_query(sql, (pid,))
        if df.empty: return jsonify({"labels": [], "prices": []})
        
        df['price_dt'] = pd.to_datetime(df['price_dt'])
        data = {
            "labels": df['price_dt'].dt.strftime('%m-%d').tolist(),
            "prices": df['price'].tolist()
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    if session.get('is_admin'): return redirect(url_for('admin_panel'))

    active_tab = request.args.get('active_tab', 'radar')
    uid = session['user_id']
    
    try:
        # Fetch Watchlist
        sql = """
        SELECT c.cid, p.pname, p.p_description, p.p_category, p.msrp, c.cutoff, p.pid, p.tracking_url,
            (SELECT price FROM Seller_Prices sp WHERE sp.pid = p.pid ORDER BY sp.price_dt DESC LIMIT 1) as current_price,
            (SELECT price FROM Seller_Prices sp WHERE sp.pid = p.pid ORDER BY sp.price_dt ASC LIMIT 1) as first_price,
            (SELECT MIN(price) FROM Seller_Prices sp WHERE sp.pid = p.pid) as min_price
        FROM Cart c JOIN Product p ON c.pid = p.pid WHERE c.uid = %s
        """
        df = db_manager.run_query(sql, (uid,))
        for col in ['current_price', 'first_price', 'min_price', 'msrp']: df[col] = df[col].fillna(0.0)
        all_items = df.to_dict('records')

        # Logic
        for item in all_items:
            if item['first_price'] <= 0: item['first_price'] = item['msrp'] if item['msrp'] > 0 else item['current_price']
            if item['min_price'] <= 0: item['min_price'] = item['current_price']
            
            if item['first_price'] > 0:
                diff = item['current_price'] - item['first_price']
                item['change_pct'] = round((diff / item['first_price']) * 100, 1)
            else: item['change_pct'] = 0.0
            
            if item['current_price'] > 0 and item['current_price'] <= item['cutoff']:
                item['is_deal'] = True
            else: item['is_deal'] = False

        # Sort by Deal Status
        all_items.sort(key=lambda x: (not x['is_deal'], x['pname']))
        active_deals = [x for x in all_items if x['is_deal']]

        # News
        news_data = db_manager.run_query("SELECT category, title, n_url, image_url, published_at FROM News ORDER BY published_at DESC LIMIT 100")
        raw_news = news_data.to_dict('records') if not news_data.empty else []
        
        user_keywords = [str(i['pname']).split()[0].lower() for i in all_items]
        processed_news = []
        for news in raw_news:
            t = str(news['title']).lower()
            tag = "AMAZON DEAL"
            if any(k in t for k in user_keywords if len(k)>3): tag = "WATCHLIST OFFER"
            elif any(x in t for x in ['prime', 'sale', 'deal']): tag = "UPCOMING SALE"
            news['tag'] = tag
            if not news['image_url']: news['image_url'] = "https://upload.wikimedia.org/wikipedia/commons/a/a9/Amazon_logo.svg"
            processed_news.append(news)
            
        priority_map = {"WATCHLIST OFFER": 1, "UPCOMING SALE": 2, "AMAZON DEAL": 3}
        processed_news.sort(key=lambda x: priority_map.get(x['tag'], 3))

        user_df = db_manager.run_query("SELECT fname, lname, email FROM Users WHERE uid=%s", (uid,))
        if user_df.empty: session.clear(); return redirect(url_for('login'))
        user_info = user_df.iloc[0].to_dict()

        return render_template('dashboard.html', 
                               watchlist=all_items, 
                               news_items=processed_news[:60], 
                               user_info=user_info, 
                               active_tab=active_tab,
                               deal_count=len(active_deals))

    except Exception as e: return handle_db_error(e)

@app.route('/trigger_news')
def trigger_news():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # Use requests for RSS (Simpler than Cloudscraper)
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36'}
    count = 0
    try:
        for cat, url in RSS_SOURCES.items():
            try:
                resp = requests.get(url, headers=HEADERS, timeout=10)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.content)
                    for entry in feed.entries[:10]:
                        if db_manager.run_query("SELECT nid FROM News WHERE n_url=%s", (entry.link,)).empty:
                            img_url = None
                            if 'media_content' in entry: img_url = entry.media_content[0]['url']
                            elif 'media_thumbnail' in entry: img_url = entry.media_thumbnail[0]['url']
                            else:
                                m = re.search(r'<img[^>]+src="([^">]+)"', entry.get('summary', ''))
                                if m: img_url = m.group(1)
                            clean_title = entry.title.encode('ascii', 'ignore').decode('ascii')
                            db_manager.execute_command(
                                "INSERT INTO News (category, title, n_url, image_url) VALUES (%s, %s, %s, %s)",
                                (cat, clean_title, entry.link, img_url)
                            )
                            count += 1
            except: pass
        flash(f"Refreshed! Found {count} new deals.", "success")
    except Exception as e: flash(f"Error: {e}", "danger")
    return redirect(url_for('dashboard', active_tab='news'))

@app.route('/user/create_product', methods=['POST'])
def user_create_product():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        details = scraper.auto_discover_from_url(request.form.get('pname_or_link'), session_id)
        if details:
            safe_pname = utils.safe_log(details['pname'])
            pid, _ = db_manager.get_or_create_product(safe_pname, "Amazon Import", details['category'], details['msrp'], details['tracking_url'])
            amz = db_manager.run_query("SELECT sid FROM Sellers WHERE sname='Amazon'")
            if not amz.empty:
                db_manager.call_insert_price_procedure(pid, int(amz.iloc[0]['sid']), float(details['msrp']), details['tracking_url'])
            db_manager.add_to_cart(session['user_id'], pid, 0.00)
            flash(f"Tracking started for {safe_pname[:15]}...", "success")
        else: flash("Invalid Amazon link or blocked.", "danger")
    except Exception as e: return handle_db_error(e)
    return redirect(url_for('dashboard', active_tab='radar'))

@app.route('/trigger_scrape')
def trigger_scrape():
    msg = scraper.run_scraper_job(session_id)
    flash(msg, "info")
    return redirect(url_for('dashboard', active_tab='radar'))

@app.route('/update_target', methods=['POST'])
def update_target():
    db_manager.update_cart_target(request.form.get('cid'), float(request.form.get('new_cutoff')))
    flash("Target updated.", "success")
    return redirect(url_for('dashboard', active_tab='radar'))

@app.route('/delete_cart/<int:cid>')
def delete_cart(cid):
    db_manager.delete_from_cart(cid)
    flash("Item removed.", "warning")
    return redirect(url_for('dashboard', active_tab='radar'))

@app.route('/user/update_profile', methods=['POST'])
def update_profile():
    db_manager.execute_command("UPDATE Users SET fname=%s, lname=%s, email=%s WHERE uid=%s", 
        (request.form.get('fname'), request.form.get('lname'), request.form.get('email'), session['user_id']))
    flash("Profile updated.", "success")
    return redirect(url_for('dashboard', active_tab='account'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        if db_manager.create_user(request.form['fname'], request.form['lname'], request.form['email'], request.form['password']):
            return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/admin')
def admin_panel(): return redirect(url_for('dashboard')) 
@app.route('/admin/add_product', methods=['POST'])
def add_product(): return redirect(url_for('dashboard'))
@app.route('/admin/delete_product/<int:pid>')
def delete_product_route(pid): return redirect(url_for('dashboard'))

if __name__ == '__main__':
    print("\n[INFO] App Running at: http://127.0.0.1:5000\n")
    app.run(debug=True)