# PlexAuthGUI/app.py
from flask import Flask, jsonify, render_template, request
import requests
import sqlite3
import os
from datetime import datetime
from urllib.parse import urlencode

app = Flask(__name__)

# ===================== DB Setup =====================
def get_db_path():
    db_dir = 'db'
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, 'auth.db')

def create_db():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            UNIQUE(token, user_id)
        )
    ''')
    conn.commit()
    conn.close()

create_db()

# ===================== Plex Auth Logic =====================
def get_plex_auth_token(app_name, unique_client_id):
    """Initialize Plex authentication process via pins."""
    r = requests.post(
        "https://plex.tv/api/v2/pins",
        headers={"Accept": "application/json"},
        data={
            "strong": "true",
            "X-Plex-Product": app_name,
            "X-Plex-Client-Identifier": unique_client_id,
        },
    )
    r_json = r.json()
    pin_id, pin_code = r_json["id"], r_json["code"]

    encoded_params = urlencode({
        "clientID": unique_client_id,
        "code": pin_code,
        "context[device][product]": app_name,
    })
    auth_url = f"https://app.plex.tv/auth#?{encoded_params}"

    return pin_id, auth_url

def store_token_usage(token, user_id):
    """Insert or update token usage in DB."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO auth_tokens (token, user_id, created_at, last_used_at)
        VALUES (?, ?, ?, ?)
    ''', (token, user_id, datetime.now(), datetime.now()))
    conn.commit()
    conn.close()

def get_user_token(user_id):
    """Retrieve the token for a user if it exists."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT token FROM auth_tokens WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

# ===================== Routes =====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/activate_script', methods=['POST'])
def activate_script():
    app_name = "PlexWatchListPlusByBaramFlix0099999"
    unique_client_id = "PlexWatchListPlusByBaramFlix0099999"
    pin_id, auth_url = get_plex_auth_token(app_name, unique_client_id)
    return jsonify({'auth_url': auth_url, 'pin_id': pin_id})

@app.route('/check_token/<pin_id>', methods=['GET'])
def check_token(pin_id):
    unique_client_id = "PlexWatchListPlusByBaramFlix0099999"
    
    r = requests.get(
        f"https://plex.tv/api/v2/pins/{pin_id}",
        headers={"Accept": "application/json"},
        params={"X-Plex-Client-Identifier": unique_client_id}
    )

    r_json = r.json()
    auth_token = r_json.get("authToken")

    if auth_token:
        # (Optional) verify user data
        # For example, we might fetch username/email from plex (like in "myplexaccount" if needed)
        # But here we just store the token with a placeholder user_id
        user_id = f"user_{pin_id}"  
        store_token_usage(auth_token, user_id)

        return jsonify({
            'auth_token': auth_token,
            'user_id': user_id,
            'status': 'success'
        })
    else:
        return jsonify({
            'auth_token': None,
            'status': 'pending'
        })

@app.route('/get_user_info/<token>', methods=['GET'])
def get_user_info(token):
    """Get user information for a given token"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT user_id, created_at, last_used_at FROM auth_tokens WHERE token = ?', (token,))
    result = c.fetchone()
    conn.close()

    if result:
        return jsonify({
            'user_id': result[0],
            'created_at': result[1],
            'last_used_at': result[2]
        })
    return jsonify({'error': 'Token not found'}), 404

# ---------------- New Routes for Searching / Discovery from recbyhistory ----------------
@app.route('/search_ai', methods=['POST'])
def search_ai():
    """
    Calls recbyhistory's /ai_search endpoint with the query and the gemini+tmdb tokens if needed.
    We'll assume we have a user_id or token that maps to a user_id in recbyhistory.
    """
    data = request.json
    query = data.get('query')
    user_id = data.get('user_id')

    if not query or not user_id:
        return jsonify({'error': 'Missing query or user_id'}), 400

    # Send request to recbyhistory
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    ai_search_url = f"{recbyhistory_url}/ai_search"

    payload = {
        'user_id': user_id,
        'gemini_api_key': os.environ.get("GEMINI_API_KEY", ""),
        'tmdb_api_key': os.environ.get("TMDB_API_KEY", ""),
        'query': query
    }
    try:
        r = requests.post(ai_search_url, json=payload, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/discovery', methods=['POST'])
def discovery():
    """
    Calls recbyhistory's /discovery_recommendations to get discovery results
    """
    data = request.json
    user_id = data.get('user_id')
    num_movies = data.get('num_movies', 3)
    num_series = data.get('num_series', 2)
    extra_elements = data.get('extra_elements', "")

    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    disc_url = f"{recbyhistory_url}/discovery_recommendations"

    payload = {
        'user_id': user_id,
        'gemini_api_key': os.environ.get("GEMINI_API_KEY", ""),
        'tmdb_api_key': os.environ.get("TMDB_API_KEY", ""),
        'num_movies': num_movies,
        'num_series': num_series,
        'extra_elements': extra_elements
    }
    try:
        r = requests.post(disc_url, json=payload, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/monthly_recs', methods=['GET'])
def monthly_recs():
    """
    Get monthly recommendations from recbyhistory's /monthly_recommendations
    """
    user_id = request.args.get('user_id', 'default_user')
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    monthly_url = f"{recbyhistory_url}/monthly_recommendations?user_id={user_id}"
    try:
        r = requests.get(monthly_url, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/add_to_watchlist_gui', methods=['POST'])
def add_to_watchlist_gui():
    """
    Calls recbyhistory's /add_to_watchlist to add content to watchlist
    """
    data = request.json
    user_id = data.get('user_id')
    imdb_id = data.get('imdb_id')
    media_type = data.get('media_type', 'movie')

    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    add_url = f"{recbyhistory_url}/add_to_watchlist"

    payload = {
        'user_id': user_id,
        'imdb_id': imdb_id,
        'media_type': media_type
    }
    try:
        r = requests.post(add_url, json=payload, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ HTML GUI Routes ============
@app.route('/search', methods=['GET'])
def search_page():
    """
    Returns a modern search page with a search bar, results in a carousel, etc.
    """
    return render_template('search_results.html')

if __name__ == '__main__':
    # Default port 5332
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5332)), debug=True)
