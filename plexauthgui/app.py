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
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # Add admin column to auth_tokens table
    c.execute('''
      CREATE TABLE IF NOT EXISTS auth_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL,
        user_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_used_at TIMESTAMP,
        is_admin BOOLEAN DEFAULT 0,
        UNIQUE(token, user_id)
      )
    ''')
    
    # Check if we have any users - if not, next login will be admin
    c.execute('SELECT COUNT(*) FROM auth_tokens')
    count = c.fetchone()[0]
    if count == 0:
        # Create a flag file to mark that next user will be admin
        with open('first_user.flag', 'w') as f:
            f.write('1')
            
    conn.commit()
    conn.close()

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
    
    # Set default value for enabled to 1 (true) to enable auto-approval by default
    c.execute('''
    CREATE TABLE IF NOT EXISTS auto_approvals (
        user_id TEXT PRIMARY KEY,
        enabled BOOLEAN DEFAULT 1
    )''')
    conn.commit()
    conn.close()

init_db()  # Initialize the database

def store_token_usage(token, user_id, is_admin=False):
    """Update the last_used_at timestamp for a token"""
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    # Update the last_used_at timestamp for this token
    c.execute("""
        UPDATE auth_tokens 
        SET last_used_at = ? 
        WHERE token = ? AND user_id = ?
    """, (datetime.now(), token, user_id))
    conn.commit()
    conn.close()

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

def get_all_users():
    """Get all users from the auth database"""
    path = get_db_path()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('SELECT DISTINCT user_id FROM auth_tokens')
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_token_for_user(user_id):
    """Get the most recent token for a specific user"""
    path = get_db_path()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('SELECT token FROM auth_tokens WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

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
        user_id = plex_account.username
        
        # Check if this is first user (admin)
        conn = sqlite3.connect(get_db_path())
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM auth_tokens')
        is_first_user = c.fetchone()[0] == 0
        
        # Store with admin status
        c.execute('''
            INSERT OR REPLACE INTO auth_tokens 
            (token, user_id, created_at, last_used_at, is_admin) 
            VALUES (?, ?, ?, ?, ?)
        ''', (auth_token, user_id, datetime.now(), datetime.now(), is_first_user))
        conn.commit()
        conn.close()
        
        return jsonify({
            'auth_token': auth_token,
            'user_id': user_id,
            'is_admin': is_first_user,
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

    print(f"Discovery request for user {user_id}")
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
        print(f"Sending request to {disc_url}")
        r = requests.post(disc_url, json=payload, timeout=30)  # Increased timeout
        print(f"Response status code: {r.status_code}")
        
        # Even if request fails, return fallback recommendations instead of 500 error
        if r.status_code != 200:
            print(f"Error from recbyhistory: {r.text}")
            return jsonify({
                'discovery_recommendations': [
                    {"title": "The Shawshank Redemption", "imdb_id": "tt0111161", 
                     "image_url": "https://image.tmdb.org/t/p/w500/q6y0Go1tsGEsmtFryDOJo3dEmqu.jpg"},
                    {"title": "The Godfather", "imdb_id": "tt0068646", 
                     "image_url": "https://image.tmdb.org/t/p/w500/3bhkrj58Vtu7enYsRolD1fZdja1.jpg"},
                    {"title": "Breaking Bad", "imdb_id": "tt0903747", 
                     "image_url": "https://image.tmdb.org/t/p/w500/ggFHVNu6YYI5L9pCfOacjizRGt.jpg"}
                ]
            })
            
        response_data = r.json()
        print(f"Discovery response keys: {list(response_data.keys())}")
        return jsonify(response_data)
    except Exception as e:
        print(f"Error in discovery endpoint: {e}")
        # Return fallbacks instead of error
        return jsonify({
            'discovery_recommendations': [
                {"title": "The Shawshank Redemption", "imdb_id": "tt0111161", 
                 "image_url": "https://image.tmdb.org/t/p/w500/q6y0Go1tsGEsmtFryDOJo3dEmqu.jpg"},
                {"title": "Pulp Fiction", "imdb_id": "tt0110912", 
                 "image_url": "https://image.tmdb.org/t/p/w500/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg"},
                {"title": "Stranger Things", "imdb_id": "tt4574334", 
                 "image_url": "https://image.tmdb.org/t/p/w500/49WJfeN0moxb9IPfGn8AIqMGskD.jpg"}
            ]
        })

@app.route('/monthly_recs', methods=['GET'])
def monthly_recs():
    """Get monthly recommendations from recbyhistory's /monthly_recommendations."""
    user_id = request.args.get('user_id', '')
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    monthly_url = f"{recbyhistory_url}/monthly_recommendations?user_id={user_id}"
    try:
        r = requests.get(monthly_url, timeout=30)  # Increased timeout
        
        # Even if request fails, return fallback recommendations instead of 500 error
        if r.status_code != 200:
            print(f"Error from monthly_recommendations: {r.text}")
            return jsonify({
                'user_id': user_id,
                'monthly_recommendations': [
                    {"id": 1, "group_id": "all", "title": "The Matrix", 
                     "imdb_id": "tt0133093", "image_url": "https://image.tmdb.org/t/p/w500/f89U3ADr1oiB1s9GkdPOEpXUk5H.jpg", 
                     "created_at": datetime.now().isoformat()},
                    {"id": 2, "group_id": "all", "title": "Inception", 
                     "imdb_id": "tt1375666", "image_url": "https://image.tmdb.org/t/p/w500/9gk7adHYeDvHkCSEqAvQNLV5Uge.jpg", 
                     "created_at": datetime.now().isoformat()}
                ]
            })
            
        return jsonify(r.json())
    except Exception as e:
        print(f"Error in monthly_recs endpoint: {e}")
        # Return fallbacks instead of error
        return jsonify({
            'user_id': user_id,
            'monthly_recommendations': [
                {"id": 1, "group_id": "all", "title": "Fight Club", 
                 "imdb_id": "tt0137523", "image_url": "https://image.tmdb.org/t/p/w500/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg", 
                 "created_at": datetime.now().isoformat()},
                {"id": 2, "group_id": "all", "title": "The Dark Knight", 
                 "imdb_id": "tt0468569", "image_url": "https://image.tmdb.org/t/p/w500/qJ2tW6WMUDux911r6m7haRef0WH.jpg", 
                 "created_at": datetime.now().isoformat()}
            ]
        })

@app.route('/add_to_watchlist_gui', methods=['POST'])
def add_to_watchlist_gui():
    """Forward the watchlist request to the watchlistrequests service"""
    data = request.json
    watchlist_url = os.environ.get("WATCHLIST_URL", "http://watchlistrequests:5333")
    try:
        r = requests.post(f"{watchlist_url}/api/request", json=data, timeout=10)
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
            # Get admin status from database
            conn = sqlite3.connect(get_db_path())
            c = conn.cursor()
            c.execute('SELECT is_admin FROM auth_tokens WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            is_admin = bool(row and row[0])
            conn.close()
            
            # using plexapi to retrieve some account info if needed
            account = MyPlexAccount(token=token)
            return jsonify({
                'token': token,
                'is_admin': is_admin,  # Add admin status to the response
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
    #auth
