from flask import Flask, jsonify, render_template, request
import requests
import sqlite3
import os
from datetime import datetime
from urllib.parse import urlencode
from plexapi.myplex import MyPlexAccount
import logging

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

def add_discover_slider(title, imdb_ids_str, slider_type=4): # imdb_ids_str is comma-separated
    """
    Adds a new discover slider setting to Overseerr using TMDB IDs converted from IMDb IDs.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    overseerr_api_key = os.environ.get("OVERSEERR_API_KEY")
    getimdbid_url = os.environ.get("GETIMDBID_URL", "http://getimdbid:5331")

    if not overseerr_api_key:
        app.logger.error("add_discover_slider: OVERSEERR_API_KEY not found in environment variables.")
        return {"error": "No Overseerr API key found"}
    if not getimdbid_url:
        app.logger.error("add_discover_slider: GETIMDBID_URL not configured.")
        return {"error": "GETIMDBID service URL not configured."}

    imdb_id_list = [id.strip() for id in imdb_ids_str.split(',') if id.strip()]
    if not imdb_id_list:
        app.logger.warning("add_discover_slider: No IMDb IDs provided to create slider.")
        return {"error": "No IMDb IDs provided for the slider."}
        
    tmdb_ids = []
    app.logger.info(f"add_discover_slider: Attempting to convert IMDb IDs: {imdb_id_list} to TMDB IDs for slider '{title}'.")

    for imdb_id in imdb_id_list:
        try:
            # Assuming movies for sliders, adjust media_type if necessary.
            # For this task, "movie" is assumed.
            payload = {"imdb_id": imdb_id, "media_type": "movie"} 
            convert_response = requests.post(
                f"{getimdbid_url}/convert_ids",
                json=payload,
                timeout=10
            )
            convert_response.raise_for_status()
            result = convert_response.json()
            if result.get("tmdb_id"):
                tmdb_ids.append(str(result["tmdb_id"]))
                app.logger.info(f"add_discover_slider: Converted IMDb ID {imdb_id} to TMDB ID {result['tmdb_id']}.")
            else:
                app.logger.warning(f"add_discover_slider: Could not convert IMDb ID {imdb_id} to TMDB ID. Response: {result}")
        except requests.exceptions.RequestException as e:
            app.logger.error(f"add_discover_slider: RequestException converting IMDb ID {imdb_id} to TMDB ID: {e}")
        except Exception as e:
            app.logger.error(f"add_discover_slider: Unexpected error converting IMDb ID {imdb_id} to TMDB ID: {e}")
    
    if not tmdb_ids:
        app.logger.error(f"add_discover_slider: No TMDB IDs could be obtained from IMDb IDs: {imdb_ids_str} for slider '{title}'.")
        return {"error": "Could not convert any IMDb IDs to TMDB IDs for the slider."}

    # Setup headers for Overseerr
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Key": overseerr_api_key
    }

    # Build the payload for Overseerr with TMDB IDs in the data field
    # For custom sliders from a list of IDs, isBuiltIn should be false.
    # Type 4 is "TMDB Movie Recommendations", which seems suitable if we are passing TMDB movie IDs.
    # The caller (add_monthly_to_overseerr) passes slider_type=1 ("Upcoming Movies"), 
    # which might not be ideal for a custom list of TMDB IDs. 
    # However, changing the caller's choice of slider_type is outside this function's direct responsibility.
    # This function will use the slider_type provided by the caller.
    overseerr_payload = {
        "title": title,
        "type": slider_type, 
        "order": 1, # Default order to 1, can be made configurable
        "enabled": True, 
        "isBuiltIn": False, # Custom sliders are not built-in
        "data": ",".join(tmdb_ids), # Comma-separated TMDB IDs
    }
    
    endpoint = f"{overseerr_url}/api/v1/settings/discover/add"
    try:
        app.logger.info(f"add_discover_slider: Sending request to Overseerr: {endpoint} with payload: {overseerr_payload}")
        response = requests.post(endpoint, json=overseerr_payload, headers=headers)
        response.raise_for_status()
        app.logger.info(f"add_discover_slider: Successfully added discover slider: {title} with TMDB IDs: {tmdb_ids}")
        return response.json()
    except requests.exceptions.RequestException as e:
        app.logger.error(f"add_discover_slider: RequestException adding discover slider '{title}' to Overseerr: {e}. Response: {e.response.text if e.response else 'No response'}")
        return {"error": f"Overseerr API error: {e}"}
    except Exception as e:
        app.logger.error(f"add_discover_slider: Unexpected error adding discover slider '{title}' to Overseerr: {e}")
        return {"error": str(e)}

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
    if (auth_token):
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
    data = request.json
    query = data.get('query')
    user_id = data.get('user_id')

    gemini_api_key_env = os.environ.get("GEMINI_API_KEY")
    tmdb_api_key_env = os.environ.get("TMDB_API_KEY")

    gemini_api_key_req = data.get('gemini_api_key')
    tmdb_api_key_req = data.get('tmdb_api_key')

    final_gemini_key = gemini_api_key_env if gemini_api_key_env else gemini_api_key_req
    final_tmdb_key = tmdb_api_key_env if tmdb_api_key_env else tmdb_api_key_req
    
    if final_gemini_key and final_tmdb_key:
        key_source_log = "environment variables" if gemini_api_key_env and tmdb_api_key_env \
            else ("client request" if gemini_api_key_req and tmdb_api_key_req else "mixed sources or missing")
        # More precise logging for which specific keys came from where
        gemini_source = "environment" if gemini_api_key_env else "client request"
        tmdb_source = "environment" if tmdb_api_key_env else "client request"
        app.logger.info(f"Using Gemini API key from {gemini_source} and TMDB API key from {tmdb_source} for AI search.")
    else:
        app.logger.warning("One or both API keys are missing after checking environment and request.")

    if not query or not user_id:
        app.logger.warning("AI Search: Query or user_id missing from request.")
        return jsonify({'search_results': []}), 200

    if not final_gemini_key or not final_tmdb_key:
        app.logger.error("AI Search: API keys for Gemini or TMDB are missing from both environment and request.")
        return jsonify({'error': 'API keys are not configured correctly. Please check server logs or provide them in the UI.', 'search_results': []}), 500

    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    ai_search_url = f"{recbyhistory_url}/ai_search"

    payload = {
        'user_id': user_id,
        'gemini_api_key': final_gemini_key,
        'tmdb_api_key': final_tmdb_key,
        'query': query
    }
    try:
        app.logger.info(f"Sending AI search request for user {user_id} to {ai_search_url}")
        r = requests.post(ai_search_url, json=payload, timeout=15) # Increased timeout slightly
        r.raise_for_status()
        response_data = r.json()
        
        # Ensure search_results is a valid array with proper fields
        search_results = []
        if 'search_results' in response_data and isinstance(response_data['search_results'], list):
            for item in response_data['search_results']:
                if isinstance(item, dict):
                    # Ensure required fields exist
                    clean_item = {
                        'title': item.get('title', 'Unknown Title'),
                        'imdb_id': item.get('imdb_id', ''),
                        'image_url': item.get('image_url', '')
                    }
                    search_results.append(clean_item)
        
        return jsonify({'search_results': search_results})
    except requests.exceptions.Timeout:
        app.logger.error(f"AI Search: Timeout when calling {ai_search_url} for user {user_id}.")
        return jsonify({'error': 'AI search request timed out.', 'search_results': []}), 504 # Gateway Timeout
    except requests.exceptions.RequestException as e:
        app.logger.error(f"AI Search: Error calling {ai_search_url} for user {user_id}: {e}")
        return jsonify({'error': f'Failed to connect to recommendation service: {e}', 'search_results': []}), 502 # Bad Gateway
    except Exception as e:
        app.logger.error(f"AI Search: Unexpected error for user {user_id}: {e}")
        return jsonify({'error': 'An unexpected error occurred during AI search.', 'search_results': []}), 500

@app.route('/discovery', methods=['POST'])
def discovery():
    """Calls recbyhistory's /discovery_recommendations."""
    data = request.json
    user_id = data.get('user_id')
    num_movies = data.get('num_movies', 3) # Default values
    num_series = data.get('num_series', 2)
    extra = data.get('extra_elements', '')

    gemini_api_key_env = os.environ.get("GEMINI_API_KEY")
    tmdb_api_key_env = os.environ.get("TMDB_API_KEY")

    # Client-provided keys (though not typically sent for discovery from current UI)
    gemini_api_key_req = data.get('gemini_api_key') 
    tmdb_api_key_req = data.get('tmdb_api_key')

    final_gemini_key = gemini_api_key_env if gemini_api_key_env else gemini_api_key_req
    final_tmdb_key = tmdb_api_key_env if tmdb_api_key_env else tmdb_api_key_req

    if final_gemini_key and final_tmdb_key:
        gemini_source = "environment" if gemini_api_key_env else "client request"
        tmdb_source = "environment" if tmdb_api_key_env else "client request"
        app.logger.info(f"Using Gemini API key from {gemini_source} and TMDB API key from {tmdb_source} for Discovery.")
    else:
        app.logger.warning("Discovery: One or both API keys are missing after checking environment and request.")

    if not user_id:
        app.logger.warning("Discovery: user_id missing from request.")
        # Fallback recommendations defined below will be used.
    
    if not final_gemini_key or not final_tmdb_key:
        app.logger.error("Discovery: API keys for Gemini or TMDB are missing. Using fallback recommendations.")
        # Fallback recommendations will be returned by the generic error handler or specific logic below.
        # No need to return 500 here as discovery has fallbacks.

    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    disc_url = f"{recbyhistory_url}/discovery_recommendations"

    payload = {
        'user_id': user_id,
        'gemini_api_key': final_gemini_key, # Will be None if not found, recbyhistory should handle
        'tmdb_api_key': final_tmdb_key,     # Will be None if not found
        'num_movies': num_movies,
        'num_series': num_series,
        'extra_elements': extra
    }
    
    # Fallback data in case of any error or missing keys
    fallback_recommendations = {
        'discovery_recommendations': [
            {"title": "The Shawshank Redemption", "imdb_id": "tt0111161", 
             "image_url": "https://image.tmdb.org/t/p/w500/q6y0Go1tsGEsmtFryDOJo3dEmqu.jpg"},
            {"title": "The Godfather", "imdb_id": "tt0068646", 
             "image_url": "https://image.tmdb.org/t/p/w500/3bhkrj58Vtu7enYsRolD1fZdja1.jpg"},
            {"title": "Breaking Bad", "imdb_id": "tt0903747", 
             "image_url": "https://image.tmdb.org/t/p/w500/ggFHVNu6YYI5L9pCfOacjizRGt.jpg"}
        ]
    }

    # If keys are absolutely necessary and missing, return fallback immediately
    if not final_gemini_key or not final_tmdb_key:
        app.logger.warning("Discovery: Returning fallback recommendations due to missing API keys.")
        return jsonify(fallback_recommendations)

    try:
        app.logger.info(f"Sending Discovery request for user {user_id} to {disc_url}")
        r = requests.post(disc_url, json=payload, timeout=30)
        
        if r.status_code != 200:
            app.logger.error(f"Discovery: Error from recbyhistory service (status {r.status_code}): {r.text}")
            return jsonify(fallback_recommendations)
            
        response_data = r.json()
        app.logger.info(f"Discovery: Successfully received recommendations for user {user_id}.")
        # Ensure the key in the response is 'discovery_recommendations'
        if 'recommendations' in response_data and 'discovery_recommendations' not in response_data:
             response_data['discovery_recommendations'] = response_data.pop('recommendations')
        return jsonify(response_data)
    except requests.exceptions.Timeout:
        app.logger.error(f"Discovery: Timeout when calling {disc_url} for user {user_id}. Returning fallback.")
        return jsonify(fallback_recommendations)
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Discovery: Error calling {disc_url} for user {user_id}: {e}. Returning fallback.")
        return jsonify(fallback_recommendations)
    except Exception as e:
        app.logger.error(f"Discovery: Unexpected error for user {user_id}: {e}. Returning fallback.")
        return jsonify(fallback_recommendations)

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
            app.logger.error(f"Monthly Recs: Error from recbyhistory service (status {r.status_code}): {r.text}")
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
            
        app.logger.info(f"Monthly Recs: Successfully received recommendations for user {user_id}.")
        return jsonify(r.json())
    except requests.exceptions.Timeout:
        app.logger.error(f"Monthly Recs: Timeout when calling {monthly_url} for user {user_id}. Returning fallback.")
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
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Monthly Recs: Error calling {monthly_url} for user {user_id}: {e}. Returning fallback.")
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
    except Exception as e:
        app.logger.error(f"Monthly Recs: Unexpected error for user {user_id}: {e}. Returning fallback.")
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
        app.logger.info(f"Forwarding watchlist request for user {data.get('user_id')} to {watchlist_url}")
        r = requests.post(f"{watchlist_url}/api/request", json=data, timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.exceptions.Timeout:
        app.logger.error(f"Add to Watchlist GUI: Timeout when calling {watchlist_url}.")
        return jsonify({'error': 'Request to watchlist service timed out.'}), 504
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Add to Watchlist GUI: Error calling {watchlist_url}: {e}")
        return jsonify({'error': f'Failed to connect to watchlist service: {e}'}), 502
    except Exception as e:
        app.logger.error(f"Add to Watchlist GUI: Unexpected error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/add_monthly_to_overseerr', methods=['POST'])
def add_monthly_to_overseerr():
    """Add monthly recommendations to Overseerr discover sliders"""
    data = request.json
    user_id = data.get('user_id')
    
    # Get monthly recommendations
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    monthly_url = f"{recbyhistory_url}/monthly_recommendations?user_id={user_id}"
    
    try:
        r = requests.get(monthly_url, timeout=30)
        r.raise_for_status()
        
        recommendations = r.json().get('monthly_recommendations', [])
        if not recommendations:
            return jsonify({'error': 'No monthly recommendations found'}), 404
        
        # Need to convert IMDB IDs to Overseerr media IDs
        getimdbid_url = os.environ.get("GETIMDBID_URL", "http://getimdbid:5331")
        overseerr_ids = []
        
        for rec in recommendations:
            imdb_id = rec.get('imdb_id')
            title = rec.get('title')
            
            if not imdb_id or not title:
                app.logger.warning(f"Add Monthly to Overseerr: Skipping recommendation with missing imdb_id or title: {rec}")
                continue
                
            # Call convert_ids API to get Overseerr ID
            try:
                payload = {
                    "imdb_id": imdb_id,
                    "media_type": "movie",  # Default to movie, you might want to detect this
                    "title": title
                }
                app.logger.info(f"Add Monthly to Overseerr: Converting IMDb ID {imdb_id} for title '{title}' using {getimdbid_url}")
                convert_response = requests.post(
                    f"{getimdbid_url}/convert_ids", 
                    json=payload, 
                    timeout=10
                )
                
                if convert_response.status_code == 200:
                    result = convert_response.json()
                    overseerr_id = result.get('overseerr_id')
                    if overseerr_id:
                        overseerr_ids.append(str(overseerr_id))
                        app.logger.info(f"Add Monthly to Overseerr: Converted {imdb_id} ({title}) to Overseerr ID: {overseerr_id}")
                    else:
                        app.logger.warning(f"Add Monthly to Overseerr: No Overseerr ID found for {imdb_id} ({title}) in convert_ids response.")
                else:
                    app.logger.error(f"Add Monthly to Overseerr: Failed to convert {imdb_id} ({title}). Status: {convert_response.status_code}, Response: {convert_response.text}")
            except Exception as e:
                app.logger.error(f"Add Monthly to Overseerr: Error converting ID for {title} ({imdb_id}): {e}")
        
        if not overseerr_ids:
            app.logger.error("Add Monthly to Overseerr: Could not convert any IMDb IDs to Overseerr IDs for user {user_id}.")
            return jsonify({'error': 'Could not convert any IMDb IDs to Overseerr IDs'}), 400
            
        # Create a slider title with user ID and date
        today = datetime.now().strftime("%Y-%m-%d")
        slider_title = f"Monthly Picks for {user_id} ({today})"
        
        # Format for Overseerr - extract IMDb IDs
        imdb_ids = [rec.get('imdb_id') for rec in recommendations if rec.get('imdb_id')]
        # Call add_discover_slider with comma-separated IMDb IDs
        result = add_discover_slider(slider_title, ",".join(imdb_ids), 1) # type 1 is 'IMDb List'
        
        if 'error' not in result:
            app.logger.info(f"Add Monthly to Overseerr: Successfully added slider '{slider_title}' for user {user_id}.")
            status_msg = 'success'
            message = 'Added recommendations to Overseerr discover sliders.'
        else:
            app.logger.error(f"Add Monthly to Overseerr: Failed to add slider '{slider_title}' for user {user_id}. Error: {result.get('error')}")
            status_msg = 'error'
            message = result.get('error', 'Failed to add slider to Overseerr.')

        return jsonify({
            'status': status_msg,
            'message': message,
            'details': result,
            'converted_ids': overseerr_ids 
        })
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Add Monthly to Overseerr: RequestException for user {user_id}: {e}")
        return jsonify({'error': f'Failed to connect to dependent service: {e}'}), 502
    except Exception as e:
        app.logger.error(f"Add Monthly to Overseerr: Unexpected error for user {user_id}: {e}")
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
        app.logger.info(f"Listing all users: {len(users)} found.")
        return jsonify({'users': users})
    except Exception as e:
        app.logger.error(f"Error listing users: {e}")
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
            app.logger.warning(f"Connect: Token not found for user_id: {user_id}")
            return jsonify({'error': 'Token not found for user'}), 404

        if connection_type == 'account':
            # Get admin status from database
            conn = sqlite3.connect(get_db_path())
            c = conn.cursor()
            c.execute('SELECT is_admin FROM auth_tokens WHERE user_id = ? AND token = ?', (user_id, token)) # Ensure token matches user
            row = c.fetchone()
            is_admin = bool(row and row[0])
            conn.close()
            
            # using plexapi to retrieve some account info if needed
            app.logger.info(f"Connect: Account details requested for user_id: {user_id}. Admin status: {is_admin}")
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
            app.logger.warning(f"Connect: Invalid connection type '{connection_type}' for user_id: {user_id}")
            return jsonify({'error': 'Invalid connection type'}), 400

    except Exception as e:
        app.logger.error(f"Connect: Error for user_id {user_id} with type {connection_type}: {e}")
        return jsonify({'error': str(e)}), 500
   

if __name__ == '__main__':
    # Configure basic logging for the app if not already configured
    if not app.debug: # Don't setup logging when debug is true, Flask does it.
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')
    # Default port 5332
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5332)), debug=True)
    #auth
