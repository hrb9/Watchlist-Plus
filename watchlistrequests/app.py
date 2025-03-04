from flask import Flask, render_template, jsonify, request
import sqlite3
import os
from datetime import datetime, timedelta
import requests
import logging
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

app = Flask(__name__)

# Global scheduler for periodic tasks
scheduler = None

def init_db():
    conn = sqlite3.connect('watchlist_requests.db')
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imdb_id TEXT NOT NULL,
        title TEXT NOT NULL,
        image_url TEXT,
        user_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_at TIMESTAMP,
        approved_by TEXT
    )''')
    
    c.execute('''
    CREATE TABLE IF NOT EXISTS auto_approvals (
        user_id TEXT PRIMARY KEY,
        enabled BOOLEAN DEFAULT 0
    )''')
    conn.commit()
    conn.close()

def init_scheduler():
    """Initialize and start the background scheduler"""
    global scheduler
    if scheduler is None:
        scheduler = BackgroundScheduler()
        scheduler.start()
        # Schedule the recommendation fetcher to run every hour
        scheduler.add_job(fetch_all_user_recommendations, IntervalTrigger(hours=1))
        # Run once immediately at startup
        threading.Thread(target=fetch_all_user_recommendations).start()
        logging.info("Scheduler initialized and recommendation fetcher scheduled")

def fetch_all_user_recommendations():
    """Fetch recommendations for all users and add them to watchlist requests"""
    logging.info("Starting recommendation fetcher for all users")
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    
    try:
        # Get all users from plexauthgui
        r = requests.get(f"{plexauth_url}/users", timeout=10)
        r.raise_for_status()
        users = r.json().get('users', [])
        
        for user_id in users:
            logging.info(f"Fetching recommendations for user {user_id}")
            fetch_user_recommendations(user_id, recbyhistory_url)
            # Add a small delay to avoid overloading services
            time.sleep(2)
            
    except Exception as e:
        logging.error(f"Error in fetch_all_user_recommendations: {e}")

def fetch_user_recommendations(user_id, recbyhistory_url):
    """Fetch recommendations for a specific user and add them to watchlist requests"""
    try:
        # Fetch monthly recommendations from recbyhistory
        r = requests.get(f"{recbyhistory_url}/monthly_recommendations?user_id={user_id}", timeout=10)
        r.raise_for_status()
        recommendations = r.json().get('monthly_recommendations', [])
        
        if not recommendations:
            logging.info(f"No recommendations found for user {user_id}")
            return
            
        # Process each recommendation
        conn = sqlite3.connect('watchlist_requests.db')
        c = conn.cursor()
        
        for rec in recommendations:
            imdb_id = rec.get('imdb_id')
            title = rec.get('title')
            image_url = rec.get('image_url', '')
            
            if not imdb_id or not title:
                continue
                
            # Check if this recommendation already exists as a request
            c.execute('SELECT id FROM requests WHERE imdb_id = ? AND user_id = ?', (imdb_id, user_id))
            existing = c.fetchone()
            
            if existing:
                logging.info(f"Request for {imdb_id} already exists for user {user_id}")
                continue
                
            # Check if user has auto-approval
            c.execute('SELECT enabled FROM auto_approvals WHERE user_id = ?', (user_id,))
            result = c.fetchone()
            auto_approve = result and result[0]
            
            status = 'auto_approved' if auto_approve else 'pending'
            
            # Add the recommendation as a request
            c.execute('''
            INSERT INTO requests (imdb_id, title, image_url, user_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (imdb_id, title, image_url, user_id, status, datetime.now()))
            
            # If auto-approved, add to Plex watchlist
            last_id = c.lastrowid
            if auto_approve:
                conn.commit()  # Commit before calling external function
                add_to_plex_watchlist(user_id, imdb_id)
                
            logging.info(f"Added recommendation {title} ({imdb_id}) for user {user_id} with status {status}")
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logging.error(f"Error fetching recommendations for user {user_id}: {e}")

init_db()
init_scheduler()

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/requests', methods=['GET'])
def get_requests():
    conn = sqlite3.connect('watchlist_requests.db')
    c = conn.cursor()
    c.execute('SELECT * FROM requests ORDER BY created_at DESC')
    requests = [dict(zip(['id', 'imdb_id', 'title', 'image_url', 'user_id', 'status', 'created_at', 'approved_at', 'approved_by'], row)) 
                for row in c.fetchall()]
    conn.close()
    return jsonify(requests)

@app.route('/api/request', methods=['POST'])
def add_request():
    data = request.json
    conn = sqlite3.connect('watchlist_requests.db')
    c = conn.cursor()
    
    # Check if user has auto-approval
    c.execute('SELECT enabled FROM auto_approvals WHERE user_id = ?', (data['user_id'],))
    auto_approve = c.fetchone() and c.fetchone()[0]
    
    status = 'auto_approved' if auto_approve else 'pending'
    
    c.execute('''
    INSERT INTO requests (imdb_id, title, image_url, user_id, status)
    VALUES (?, ?, ?, ?, ?)
    ''', (data['imdb_id'], data['title'], data['image_url'], data['user_id'], status))
    
    if auto_approve:
        # Call Plex API to add to watchlist
        approve_request(c.lastrowid)
        
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

def get_plex_token(user_id):
    """Get Plex token from plexauthgui service"""
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.post(f"{plexauth_url}/connect", 
                         json={"user_id": user_id, "type": "account"})
        r.raise_for_status()
        return r.json().get("token")
    except Exception as e:
        logging.error(f"Error getting Plex token: {e}")
        return None

def add_to_plex_watchlist(user_id, imdb_id):
    """Add content to user's Plex watchlist after approval"""
    token = get_plex_token(user_id)
    if not token:
        return False
        
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    try:
        r = requests.post(f"{recbyhistory_url}/add_to_watchlist",
                         json={
                             "user_id": user_id,
                             "imdb_id": imdb_id,
                             "media_type": "movie"  # Default to movie
                         })
        r.raise_for_status()
        return r.json().get("status") == "OK"
    except Exception as e:
        logging.error(f"Error adding to Plex watchlist: {e}")
        return False

@app.route('/api/approve/<int:request_id>', methods=['POST'])
def approve_request(request_id):
    """Approve a watchlist request and add to Plex"""
    data = request.json
    admin_id = data['admin_id']
    
    conn = sqlite3.connect('watchlist_requests.db')
    c = conn.cursor()
    
    # Get request details
    c.execute('SELECT imdb_id, user_id FROM requests WHERE id = ?', (request_id,))
    req = c.fetchone()
    if not req:
        return jsonify({'error': 'Request not found'}), 404
        
    imdb_id, user_id = req
    
    # Add to Plex watchlist
    success = add_to_plex_watchlist(user_id, imdb_id)
    if success:
        c.execute('''
        UPDATE requests 
        SET status = 'approved', 
            approved_at = ?, 
            approved_by = ?
        WHERE id = ?
        ''', (datetime.now(), admin_id, request_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
    else:
        conn.close()
        return jsonify({'error': 'Failed to add to Plex watchlist'}), 500

@app.route('/api/check_admin', methods=['POST'])
def check_admin():
    """Check if user is admin"""
    data = request.json
    user_id = data.get('user_id')
    
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.post(f"{plexauth_url}/connect", 
                         json={"user_id": user_id, "type": "account"})
        r.raise_for_status()
        data = r.json()
        return jsonify({"is_admin": data.get("is_admin", False)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auto_approval', methods=['POST'])
def set_auto_approval():
    """Set auto-approval status for a user (admin only)"""
    data = request.json
    admin_id = data.get('admin_id')
    target_user = data.get('user_id')
    enabled = data.get('enabled', False)
    
    # Verify admin status
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.post(f"{plexauth_url}/connect", 
                         json={"user_id": admin_id, "type": "account"})
        r.raise_for_status()
        if not r.json().get("is_admin", False):
            return jsonify({"error": "Not authorized"}), 403
            
        # Update auto-approval status
        conn = sqlite3.connect('watchlist_requests.db')
        c = conn.cursor()
        c.execute('''
        INSERT OR REPLACE INTO auto_approvals (user_id, enabled)
        VALUES (?, ?)
        ''', (target_user, enabled))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/users', methods=['GET'])
def get_users():
    """Get list of users from plexauthgui"""
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.get(f"{plexauth_url}/users")
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def check_new_users():
    """Checks only for new users, runs frequently"""
    global USER_SCHEDULE
    
    plexauthgui_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.get(f"{plexauthgui_url}/users")
        r.raise_for_status()
        user_list = r.json().get('users', [])
        
        if not user_list:
            logging.info("No users found in plexauthgui")
            return
            
        now = datetime.utcnow()
        for user_id in user_list:
            if user_id not in USER_SCHEDULE:
                logging.info(f"New user detected: {user_id}")
                USER_SCHEDULE[user_id] = {
                    'last_history': now - timedelta(days=1),
                    'last_taste': now - timedelta(days=7),
                    'last_monthly': now - timedelta(days=30)
                }
                # Run initial tasks immediately for new user
                try:
                    run_history_task(user_id)
                    run_taste_task(user_id)
                    run_monthly_task(user_id)
                    logging.info(f"Successfully initialized tasks for new user {user_id}")
                except Exception as e:
                    logging.error(f"Error initializing tasks for new user {user_id}: {e}")
                    
    except Exception as e:
        logging.error(f"Error in check_new_users: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5333)