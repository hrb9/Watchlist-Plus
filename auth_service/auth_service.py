# auth_service.py
from flask import Flask, request, jsonify
from plexapi.myplex import MyPlexAccount
import sqlite3
import os

app = Flask(__name__)

def get_db_path():
    """Get auth.db path from db directory"""
    return os.path.join('db', 'auth.db')

def get_token_for_user(user_id):
    """Retrieve token for specific user from auth.db"""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    cursor.execute('SELECT token FROM auth_tokens WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_all_users():
    """Get list of all users from auth.db"""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM auth_tokens')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def get_plex_account(user_id):
    """Get Plex account object for user"""
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

if __name__ == '__main__':
    # רץ על פורט 5333
    app.run(port=5333)
