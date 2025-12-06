# DealRadar - Smart Price Tracker

DealRadar is a full-stack price tracking application designed to help users monitor Amazon product prices in real-time. It features an intelligent scraping engine, interactive price history visualization, and automated alerts triggered by database events.

## Features

* **Real-Time Amazon Scraping:** Uses a custom "Hunter-Seeker" scraper with CloudScraper to bypass bot detection and extract accurate pricing (handling deals, ranges, and hidden prices).
* **Interactive Dashboard:** Professional sidebar layout built with Bootstrap 5 and Inter font.
* **Price History Visualization:** Dynamic line charts powered by Chart.js to visualize price trends over time.
* **Automated Alerts:** Database triggers automatically detect when a price drops below a user's set target.
* **Secure Authentication:** User passwords are hashed using SHA-256 encryption.
* **News Aggregation:** Fetches relevant deal news from RSS feeds (Slickdeals, CNET) based on watchlist items.

---

## Technical Specifications

* **Backend:** Python 3.10+ (Flask)
* **Database:** MySQL 8.0
* **Frontend:** HTML5, CSS3, Bootstrap 5, JavaScript (Chart.js)
* **Scraping Engine:** CloudScraper, BeautifulSoup4
* **Data Handling:** Pandas, PyMySQL

---

## Prerequisites

Before running the project, ensure you have the following installed:

1.  **Python 3.10 or higher**: https://www.python.org/downloads/
2.  **MySQL Community Server**: https://dev.mysql.com/downloads/mysql/

---

## Installation & Setup

### 1. Extract Project Files
Unzip the project archive to your desired location:
cd DealRadar_v2

### 2. Install Python Dependencies
It is recommended to use a virtual environment.

# Create virtual environment (Optional)
python -m venv venv
# On Windows use: venv\Scripts\activate
# On Mac/Linux use: source venv/bin/activate

# Install libraries
pip install -r requirements.txt

*(If requirements.txt is missing, run: pip install flask pymysql pandas requests beautifulsoup4 feedparser python-dotenv cloudscraper)*

### 3. Configure Database Credentials
Create a file named .env in the root directory and add your MySQL credentials:

DB_HOST=localhost
DB_USER=root
DB_PASSWORD=YOUR_MYSQL_PASSWORD
DB_NAME=dealradar
FLASK_SECRET_KEY=your_secret_key

### 4. Initialize the Database
Run the setup script to create the schema, stored procedures, triggers, and seed the test user.

python setup_db.py

> Output: SETUP COMPLETE! Login: test / asd

---

## Running the Application

1.  Start the Flask server:
    python app.py

2.  You will see the following output:
    [INFO] App Running at: http://127.0.0.1:5000

3.  Open your web browser and navigate to http://127.0.0.1:5000.

---

## User Guide

### 1. Login
Use the pre-seeded test credentials to access the dashboard:
* **Username:** test
* **Password:** asd

### 2. Tracking a Product (CREATE)
1.  Go to Amazon and copy a product URL (e.g., a Laptop or Headphones).
2.  On the dashboard, click the "Track Product" button (top right).
3.  Paste the URL and click "Start Tracking".
4.  The system will scrape the name, category, and current price automatically.

### 3. Setting a Target Price (UPDATE)
1.  In the "Target" column of the watchlist table, type your desired price (e.g., 50.00).
2.  Click the "Save" (Checkmark) button.
3.  If the current price is lower than your target, a "DEAL ACTIVE" badge will appear.

### 4. Viewing History (READ)
1.  Click the "Analysis" button on any product row.
2.  A modal will appear displaying a line graph of price changes over time.

### 5. Removing a Product (DELETE)
1.  Click the "Trash" icon on the product row to remove it from your watchlist.

---

## Project Structure

DealRadar/
│
├── app.py                 # Main Flask Application Controller
├── db_manager.py          # Database Helper Functions (CRUD)
├── scraper.py             # Advanced Amazon Scraper Logic
├── setup_db.py            # Database Initialization Script
├── utils.py               # Utility functions (Logging/Sanitization)
├── requirements.txt       # Python Dependencies
├── .env                   # Environment Variables (DB Config)
│
└── templates/             # Frontend HTML Files
    ├── base.html          # Base layout (Sidebar, Toasts)
    ├── dashboard.html     # Main Dashboard UI
    ├── login.html         # Login Page
    └── signup.html        # Registration Page

---


## User Flow

1.  **Authenticate:** User logs in via /login using seeded credentials.
2.  **Dashboard:** User views the "My Radar" table populated with tracked items.
3.  **Track Item:** User clicks "Track Product" -> Pastes Amazon URL -> System scrapes data and adds to DB.
4.  **Set Alert:** User types a target price in the "Target" column -> Clicks "Save" -> DB updates Cart.cutoff.
5.  **Analyze:** User clicks "Analysis" -> Modal opens with Chart.js price history graph.
6.  **Refresh:** User clicks "Refresh Prices" -> System rescrapes all items -> Updates Seller_Prices.
7.  **Delete:** User clicks Trash icon -> Item removed from Cart.
