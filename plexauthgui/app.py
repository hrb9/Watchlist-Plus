# PlexAuthGUI/app.py

from flask import Flask, jsonify, render_template, request
import requests
import sqlite3
import os
from datetime import datetime
from urllib.parse import urlencode
from plexapi.myplex import MyPlexAccount

app = Flask(__name__)

# ---------------- DB FUNCTIONS ----------------
def get_db_path():
    """
    Compute the path to auth.db. Storing it in the same directory as the code,
    or use a subdirectory if you prefer.
    """
    return os.path.join(os.getcwd(), 'auth.db')

def init_db():
    """
    Create the auth_tokens table if it doesn't already exist.
    """
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

init_db()

def store_token_usage(token, user_id):
    """
    Insert or update the user's token in auth.db.
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
      INSERT OR REPLACE INTO auth_tokens (token, user_id, created_at, last_used_at)
      VALUES (?, ?, ?, ?)
    ''', (token, user_id, datetime.now(), datetime.now()))
    conn.commit()
    conn.close()

def get_token_for_user(user_id):
    """
    Return the Plex token for a given user_id, or None if not found.
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT token FROM auth_tokens WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_all_users():
    """
    Return a distinct list of user_id values from the auth_tokens table.
    """
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT DISTINCT user_id FROM auth_tokens')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


# ---------------- PLEX GUI PIN LOGIC ----------------
def get_plex_auth_token(app_name, unique_client_id):
    """
    Initialize the Plex authentication process via the pin flow.
    Returns pin_id and auth_url that user can open to authenticate in Plex.
    """
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

@app.route('/')
def index():
    """
    Render the main page (index.html) with a button to start the Plex auth flow.
    """
    return render_template('index.html')

@app.route('/activate_script', methods=['POST'])
def activate_script():
    """
    POST endpoint to initiate the PIN-based authentication with Plex.
    Returns JSON containing the pin_id and a URL the user can open to authenticate.
    """
    app_name = "PlexWatchListPlusByBaramFlix0099999"
    unique_client_id = "PlexWatchListPlusByBaramFlix0099999"
    pin_id, auth_url = get_plex_auth_token(app_name, unique_client_id)
    return jsonify({'auth_url': auth_url, 'pin_id': pin_id})

@app.route('/check_token/<pin_id>', methods=['GET'])
def check_token(pin_id):
    """
    GET endpoint to check if the pin_id has yielded an authToken from Plex.
    If so, store it in auth.db.
    """
    unique_client_id = "PlexWatchListPlusByBaramFlix0099999"
    r = requests.get(
        f"https://plex.tv/api/v2/pins/{pin_id}",
        headers={"Accept": "application/json"},
        params={"X-Plex-Client-Identifier": unique_client_id}
    )
    r_json = r.json()
    auth_token = r_json.get("authToken")
    if auth_token:
        # We'll create a user_id from the pin_id
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
    """
    GET endpoint to retrieve user_id, created_at, and last_used_at given a specific token.
    """
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


# ---------------- API for recbyhistory calls (like original auth_service) ----------------
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


if __name__ == '__main__':
    # Host=0.0.0.0 so that it is accessible from other containers (not just localhost).
    app.run(host='0.0.0.0', port=5332, debug=True)
