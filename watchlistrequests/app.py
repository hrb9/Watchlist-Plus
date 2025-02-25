from flask import Flask, render_template, jsonify, request
import sqlite3
import os
from datetime import datetime
import requests

app = Flask(__name__)

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

init_db()

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
        return jsonify({'status': 'success'})
    else:
        return jsonify({'error': 'Failed to add to Plex watchlist'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5333)