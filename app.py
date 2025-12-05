from flask import Flask, render_template, request, redirect, url_for, session, flash
import db_manager
import scraper
import pandas as pd
import os
import logging
import uuid
import utils 
from dotenv import load_dotenv
from pymysql.err import IntegrityError # NEW IMPORT for error handling

session_id = str(uuid.uuid4())[:8]

# --- LOGGING ---
logging.basicConfig(
    filename='dealradar.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)
logger.info(f"\nSERVER RESTART | Session: {session_id}\n")

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

# --- HELPER: Handle Stale Sessions ---
def handle_db_error(e):
    """
    catches 'User ID not found' errors (Code 1452) caused by database resets.
    """
    if "1452" in str(e):
        session.clear()
        flash("Database was reset. Please login again.", "warning")
        return redirect(url_for('login'))
    
    # If it's a different error, crash normally so we can debug
    raise e

# --- ROUTES ---
@app.route('/')
def index():
    if 'user_email' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        users = db_manager.run_query("SELECT * FROM Users WHERE email=%s", (email,))
        if not users.empty:
            session['user_id'] = int(users.iloc[0]['uid'])
            session['user_email'] = users.iloc[0]['email']
            session['is_admin'] = (email == 'admin@dealradar.com')
            return redirect(url_for('dashboard'))
        flash("User not found!", "danger")
    users = db_manager.run_query("SELECT email FROM Users")
    return render_template('login.html', users=users['email'].tolist() if not users.empty else [])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    if session.get('is_admin'): return redirect(url_for('admin_panel'))

    uid = session['user_id']
    
    try:
        # 1. Watchlist Query
        sql = """
        SELECT c.cid, p.pname, p.p_description, p.p_category, c.cutoff, p.pid, p.tracking_url,
            (SELECT price FROM Seller_Prices sp WHERE sp.pid = p.pid ORDER BY sp.price_dt DESC LIMIT 1) as current_price,
            (SELECT price_dt FROM Seller_Prices sp WHERE sp.pid = p.pid ORDER BY sp.price_dt DESC LIMIT 1) as last_checked
        FROM Cart c JOIN Product p ON c.pid = p.pid WHERE c.uid = %s
        """
        df = db_manager.run_query(sql, (uid,))
        df['current_price'] = df['current_price'].fillna(0.00)
        all_items = df.to_dict('records')

        # 2. Filter Active Deals
        active_deals = [x for x in all_items if x['current_price'] > 0 and x['cutoff'] > 0 and x['current_price'] <= x['cutoff']]
        watchlist = [x for x in all_items if not (x['current_price'] > 0 and x['cutoff'] > 0 and x['current_price'] <= x['cutoff'])]

        # 3. History
        history_alerts = db_manager.run_query("""
            SELECT p.pname, sp.price, s.sname as vendor, a.createdat 
            FROM Alerts a JOIN Product p ON a.pid = p.pid 
            JOIN Seller_Prices sp ON a.spid = sp.spid JOIN Sellers s ON sp.sid = s.sid
            WHERE a.uid = %s ORDER BY a.createdat DESC LIMIT 30
        """, (uid,))
        
        return render_template('dashboard.html', active_deals=active_deals, watchlist=watchlist, history_alerts=history_alerts.to_dict('records'))
    
    except Exception as e:
        # Catch errors if user ID is missing from DB
        return handle_db_error(e)

@app.route('/update_target', methods=['POST'])
def update_target():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        cid = request.form.get('cid')
        new_cutoff = float(request.form.get('new_cutoff'))
        db_manager.update_cart_target(cid, new_cutoff)
        
        item = db_manager.run_query("""
            SELECT p.pid, (SELECT price FROM Seller_Prices sp WHERE sp.pid = p.pid ORDER BY sp.price_dt DESC LIMIT 1) as current_price,
                (SELECT spid FROM Seller_Prices sp WHERE sp.pid = p.pid ORDER BY sp.price_dt DESC LIMIT 1) as spid
            FROM Cart c JOIN Product p ON c.pid = p.pid WHERE c.cid = %s
        """, (cid,))
        
        if not item.empty:
            curr = item.iloc[0]['current_price']
            if curr and curr <= new_cutoff and new_cutoff > 0:
                # Basic check, trigger handles main logic, this is UI feedback
                existing = db_manager.run_query("SELECT aid FROM Alerts WHERE spid=%s AND uid=%s", (item.iloc[0]['spid'], session['user_id']))
                if existing.empty:
                    db_manager.execute_command("INSERT INTO Alerts (pid, spid, uid, active_status) VALUES (%s, %s, %s, TRUE)", (item.iloc[0]['pid'], item.iloc[0]['spid'], session['user_id']))
                    flash("Deal detected!", "success")
                else:
                    flash("Target updated.", "success")
            else:
                flash("Target updated.", "success")
    except Exception as e:
        return handle_db_error(e)
        
    return redirect(url_for('dashboard'))

@app.route('/delete_cart/<int:cid>')
def delete_cart(cid):
    db_manager.delete_from_cart(cid)
    return redirect(url_for('dashboard'))

@app.route('/trigger_scrape')
def trigger_scrape():
    msg = scraper.run_scraper_job(session_id)
    flash(msg, "info")
    return redirect(url_for('dashboard'))

@app.route('/user/create_product', methods=['POST'])
def user_create_product():
    if 'user_id' not in session: return redirect(url_for('login'))
    url = request.form.get('pname_or_link')
    
    logger.info(f"[{session_id}] User adding link...")

    try:
        details = scraper.auto_discover_from_url(url, session_id)
        if details:
            safe_pname = utils.safe_log(details['pname'])
            
            pid, created = db_manager.get_or_create_product(safe_pname, "Amazon Import", details['category'], details['msrp'], details['tracking_url'])
            
            amazon_seller = db_manager.run_query("SELECT sid FROM Sellers WHERE sname='Amazon'")
            if not amazon_seller.empty and details['msrp'] > 0:
                db_manager.call_insert_price_procedure(pid, int(amazon_seller.iloc[0]['sid']), float(details['msrp']), details['tracking_url'])

            # The risky line that caused the crash:
            db_manager.add_to_cart(session['user_id'], pid, 0.00)
            
            flash(f"Added '{safe_pname[:20]}...'", "success")
        else:
            flash("Could not read Amazon link. Ensure it is a product page.", "danger")
    
    except Exception as e:
        return handle_db_error(e)

    return redirect(url_for('dashboard'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        if db_manager.create_user(request.form['fname'], request.form['lname'], request.form['email'], request.form['password']):
            flash("Account created! Please login.", "success"); return redirect(url_for('login'))
        else: flash("Email exists.", "danger")
    return render_template('signup.html')

@app.route('/admin')
def admin_panel():
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    return render_template('admin.html', products=db_manager.run_query("SELECT * FROM Product").to_dict('records'))

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('is_admin'): return redirect(url_for('login'))
    db_manager.get_or_create_product(request.form['pname'], request.form['description'], request.form['category'], request.form['msrp'], "")
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_product/<int:pid>')
def delete_product_route(pid):
    if not session.get('is_admin'): return redirect(url_for('login'))
    db_manager.delete_product(pid)
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    print("\n[INFO] App Running at: http://127.0.0.1:5000\n")
    app.run(debug=True)