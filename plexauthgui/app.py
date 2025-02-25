from flask import Flask, jsonify, render_template, request
import requests
import sqlite3
import os
from datetime import datetime
from urllib.parse import urlencode
from plexapi.myplex import MyPlexAccount

app = Flask(__name__)

def get_db_path():
    return os.path.join(os.getcwd(), 'auth.db')

def init_db():
    path = get_db_path()
    conn = sqlite3.connect(path)
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

init_db()  # Initialize the database

def store_token_usage(token, user_id):
    path = get_db_path()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('''
      INSERT OR REPLACE INTO auth_tokens (token, user_id, created_at, last_used_at)
      VALUES (?, ?, ?, ?)
    ''', (token, user_id, datetime.now(), datetime.now()))
    conn.commit()
    conn.close()

def get_token_for_user(user_id):
    path = get_db_path()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('SELECT token FROM auth_tokens WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_all_users():
    path = get_db_path()
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('SELECT DISTINCT user_id FROM auth_tokens')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_plex_auth_token(app_name, unique_client_id):
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

# === Routes for PIN auth ===
@app.route('/')
def index():
    """Single-page with a button for authentication, a search bar, and recommendation carousels."""
    return render_template('index.html')

@app.route('/activate_script', methods=['POST'])
def activate_script():
    app_name = "PlexWatchListPlusByBaramFlix0099999"  #  Keep this consistent
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
        plex_account = MyPlexAccount(token=auth_token)
        user_id = plex_account.username  # Use Plex username
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
    path = get_db_path()
    conn = sqlite3.connect(path)
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

# === API for search & recbyhistory integration ===
@app.route('/search_ai', methods=['POST'])
def search_ai():
    """Calls recbyhistory's /ai_search endpoint with the query and user_id."""
    data = request.json
    query = data.get('query')
    user_id = data.get('user_id')
    if not query or not user_id:
        return jsonify({'error': 'Missing query or user_id'}), 400

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
    """Calls recbyhistory's /discovery_recommendations."""
    data = request.json
    user_id = data.get('user_id')
    num_movies = data.get('num_movies', 3)
    num_series = data.get('num_series', 2)
    extra = data.get('extra_elements', '')

    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    disc_url = f"{recbyhistory_url}/discovery_recommendations"

    payload = {
        'user_id': user_id,
        'gemini_api_key': os.environ.get("GEMINI_API_KEY", ""),
        'tmdb_api_key': os.environ.get("TMDB_API_KEY", ""),
        'num_movies': num_movies,
        'num_series': num_series,
        'extra_elements': extra
    }
    try:
        r = requests.post(disc_url, json=payload, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/monthly_recs', methods=['GET'])
def monthly_recs():
    """Get monthly recommendations from recbyhistory's /monthly_recommendations."""
    user_id = request.args.get('user_id', '')
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
    """Proxy to recbyhistory's /add_to_watchlist."""
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

# === API endpoint from File 1 (still useful for other clients) ===
@app.route('/users', methods=['GET'])
def list_users():
    """
    GET /users returns a JSON with a 'users' list: { 'users': [ 'user_...', ... ] }
    recbyhistory (or other clients) can call this to retrieve all user IDs.
    """
    try:
        users = get_all_users()
        return jsonify({'users': users})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# === API endpoint from file 1 ( /connect )
@app.route('/connect', methods=['POST'])
def connect():
    """
    POST /connect used by recbyhistory (or any other client) to retrieve:
      - 'users' => list of user_ids
      - 'account' => Plex token + username/email using plexapi
    JSON input: { 'user_id': <some_user>, 'type': 'users' or 'account' }
    """
    data = request.json
    user_id = data.get('user_id')
    connection_type = data.get('type')

    try:
        if connection_type == 'users':
            users = get_all_users()
            return jsonify({'users': users})

        token = get_token_for_user(user_id)
        if not token:
            return jsonify({'error': 'Token not found for user'}), 404

        if connection_type == 'account':
            # using plexapi to retrieve some account info if needed
            account = MyPlexAccount(token=token)
            return jsonify({
                'token': token,
                'account': {
                    'username': account.username,
                    'email': account.email
                }
            })
        else:
            return jsonify({'error': 'Invalid connection type'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500
   

if __name__ == '__main__':
    # Default port 5332
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5332)), debug=True)