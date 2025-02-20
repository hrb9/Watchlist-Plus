from flask import Flask, request, jsonify
from plexapi.myplex import MyPlexAccount
import sqlite3
import os

app = Flask(__name__)

def get_db_path():
    return os.path.join('/app/db', 'auth.db')

def get_token_for_user(user_id):
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    c.execute('SELECT token FROM auth_tokens WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_all_users():
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    c.execute('SELECT DISTINCT user_id FROM auth_tokens')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_plex_account(user_id):
    token = get_token_for_user(user_id)
    if not token:
        return None, None
    return MyPlexAccount(token=token), token

@app.route('/connect', methods=['POST'])
def connect():
    data = request.json
    user_id = data.get('user_id')
    connection_type = data.get('type')

    try:
        if connection_type == 'users':
            users = get_all_users()
            return jsonify({'users': users})

        account, token = get_plex_account(user_id)
        if not account:
            return jsonify({'error': 'Token not found for user'}), 404

        if connection_type == 'account':
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
    """Return list of user_ids from auth.db"""
    try:
        users = get_all_users()
        return jsonify({'users': users})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(port=5333, host='0.0.0.0')
