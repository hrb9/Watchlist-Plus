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
from plexapi.myplex import MyPlexAccount

app = Flask(__name__)

# Global scheduler for periodic tasks
scheduler = None

# Cache for Plex connections and items
PLEX_ACCOUNTS = {}
PLEX_SERVERS = {}
PLEX_ITEMS_CACHE = {}

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
    
    # Set default value for enabled to 1 (true) to enable auto-approval by default
    c.execute('''
    CREATE TABLE IF NOT EXISTS auto_approvals (
        user_id TEXT PRIMARY KEY,
        enabled BOOLEAN DEFAULT 1
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
        
        # Schedule the pending request processor to run every 5 minutes
        scheduler.add_job(process_pending_requests, IntervalTrigger(minutes=5))
        
        # Run both immediately at startup
        threading.Thread(target=fetch_all_user_recommendations).start()
        threading.Thread(target=process_pending_requests).start()
        
        logging.info("Scheduler initialized with recommendation fetcher and request processor")

def fetch_all_user_recommendations():
    """Fetch all types of recommendations for all users"""
    logging.info("Starting recommendation fetcher for all users")
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    
    try:
        # Get all users
        r = requests.get(f"{plexauth_url}/users", timeout=10)
        r.raise_for_status()
        users = r.json().get('users', [])
        
        for user_id in users:
            logging.info(f"Processing recommendations for user {user_id}")
            
            # Get monthly recommendations
            fetch_user_recommendations(user_id, os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335"))
            
            # Get discovery recommendations
            fetch_user_discovery_recommendations(user_id)
            
            # Small delay to avoid overloading services
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
        
        # Check if user has an auto_approvals entry, create one with default enabled if not
        c.execute('SELECT enabled FROM auto_approvals WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        if result is None:
            # Insert with default enabled (1)
            c.execute('INSERT INTO auto_approvals (user_id, enabled) VALUES (?, 1)', (user_id,))
            auto_approve = True
        else:
            auto_approve = result[0] == 1
        
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
            
            status = 'auto_approved' if auto_approve else 'pending'
            
            # Add the recommendation as a request
            c.execute('''
            INSERT INTO requests (imdb_id, title, image_url, user_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (imdb_id, title, image_url, user_id, status, datetime.now()))
            
            # If auto-approved, add to Plex watchlist
            if auto_approve:
                conn.commit()  # Commit before calling external function
                request_media_from_overseer(imdb_id, 'movie' if 'movie' in title.lower() else 'tv')
                add_to_plex_watchlist(user_id, imdb_id)
                
            logging.info(f"Added recommendation {title} ({imdb_id}) for user {user_id} with status {status}")
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logging.error(f"Error fetching recommendations for user {user_id}: {e}")

def fetch_user_discovery_recommendations(user_id):
    """Fetch discovery recommendations from recbyhistory and add them to watchlist"""
    recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
    try:
        # Fetch discovery recommendations
        r = requests.get(f"{recbyhistory_url}/discovery_recommendations?user_id={user_id}", timeout=10)
        r.raise_for_status()
        recommendations = r.json().get('recommendations', [])
        
        if not recommendations:
            logging.info(f"No discovery recommendations found for user {user_id}")
            return
            
        # Process each recommendation (similar to fetch_user_recommendations)
        conn = sqlite3.connect('watchlist_requests.db')
        c = conn.cursor()
        
        # Check auto-approval setting
        c.execute('SELECT enabled FROM auto_approvals WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        auto_approve = result and result[0] == 1
        
        for rec in recommendations:
            imdb_id = rec.get('imdb_id')
            if not imdb_id:
                continue
                
            # Skip if already in requests
            c.execute('SELECT id FROM requests WHERE imdb_id = ? AND user_id = ?', (imdb_id, user_id))
            if c.fetchone():
                continue
                
            # Add as request (with discovery tag)
            c.execute('''
            INSERT INTO requests (imdb_id, title, image_url, user_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (imdb_id, rec.get('title', 'Unknown'), rec.get('image_url', ''), 
                 user_id, 'auto_approved' if auto_approve else 'pending', datetime.now()))
            
            # Process immediately if auto-approved
            if auto_approve:
                conn.commit()
                add_to_plex_watchlist(user_id, imdb_id)
                
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching discovery recs for {user_id}: {e}")

def process_pending_requests():
    """Process all pending requests and add them to watchlist every 5 minutes"""
    logging.info("Processing pending watchlist requests")
    
    try:
        # Connect to database
        conn = sqlite3.connect('watchlist_requests.db')
        c = conn.cursor()
        
        # Get all pending requests
        c.execute('SELECT id, imdb_id, user_id FROM requests WHERE status = "pending"')
        pending_requests = c.fetchall()
        
        if not pending_requests:
            logging.info("No pending requests to process")
            conn.close()
            return
            
        logging.info(f"Found {len(pending_requests)} pending requests to process")
        
        # Process each request
        for req_id, imdb_id, user_id in pending_requests:
            try:
                # Add to Plex watchlist
                success = add_to_plex_watchlist(user_id, imdb_id)
                
                if success:
                    # Update request status
                    c.execute('''
                    UPDATE requests 
                    SET status = 'auto_processed', 
                        approved_at = ?
                    WHERE id = ?
                    ''', (datetime.now(), req_id))
                    logging.info(f"Auto-processed request {req_id} for user {user_id}")
                else:
                    logging.error(f"Failed to add request {req_id} to watchlist for user {user_id}")
            except Exception as e:
                logging.error(f"Error processing request {req_id}: {e}")
                
        # Commit all changes
        conn.commit()
        conn.close()
        
    except Exception as e:
        logging.error(f"Error in process_pending_requests: {e}")

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
    result = c.fetchone()
    if result is None:
        # Insert with default enabled (1)
        c.execute('INSERT INTO auto_approvals (user_id, enabled) VALUES (?, 1)', (data['user_id'],))
        auto_approve = True
    else:
        auto_approve = result[0] == 1
    
    status = 'auto_approved' if auto_approve else 'pending'
    
    c.execute('''
    INSERT INTO requests (imdb_id, title, image_url, user_id, status)
    VALUES (?, ?, ?, ?, ?)
    ''', (data['imdb_id'], data['title'], data['image_url'], data['user_id'], status))
    
    last_id = c.lastrowid
    
    # If auto-approval is enabled, add to Plex watchlist
    if auto_approve:
        conn.commit()  # Commit before calling external function
        add_to_plex_watchlist(data['user_id'], data['imdb_id'])
        
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

def connect_to_plex(user_id):
    """Connect to a user's Plex account using their token"""
    if user_id in PLEX_ACCOUNTS:
        return PLEX_ACCOUNTS[user_id]
    
    token = get_plex_token(user_id)
    if not token:
        logging.error(f"No Plex token found for user {user_id}")
        return None
        
    try:
        account = MyPlexAccount(token=token)
        PLEX_ACCOUNTS[user_id] = account
        return account
    except Exception as e:
        logging.error(f"Error connecting to Plex for user {user_id}: {e}")
        return None

def get_plex_servers(user_id):
    """Get all available Plex servers for a user"""
    if user_id in PLEX_SERVERS:
        return PLEX_SERVERS[user_id]
        
    account = connect_to_plex(user_id)
    if not account:
        return []
        
    servers = []
    for resource in account.resources():
        try:
            server = resource.connect(timeout=10)
            if server:
                servers.append(server)
        except Exception as e:
            logging.error(f"Error connecting to server {resource.name}: {e}")
            
    PLEX_SERVERS[user_id] = servers
    return servers

def find_plex_item_by_imdb_id(user_id, imdb_id):
    """Find a Plex item by IMDB ID across all user's servers"""
    if user_id not in PLEX_ITEMS_CACHE:
        PLEX_ITEMS_CACHE[user_id] = {}
    
    # Check if we've already found this item
    if imdb_id in PLEX_ITEMS_CACHE[user_id]:
        return PLEX_ITEMS_CACHE[user_id][imdb_id]
    
    servers = get_plex_servers(user_id)
    for server in servers:
        try:
            # Search across all libraries
            for section in server.library.sections():
                for item in section.all():
                    # Check if item has IMDb ID matching
                    if hasattr(item, 'guids') and item.guids:
                        for guid in item.guids:
                            if guid.id == f'imdb://{imdb_id}':
                                # Cache the item for future use
                                PLEX_ITEMS_CACHE[user_id][imdb_id] = item
                                return item
        except Exception as e:
            logging.error(f"Error searching for item {imdb_id} on server: {e}")
    
    return None

def add_to_plex_watchlist(user_id, imdb_id):
    """Add content to user's Plex watchlist after approval"""
    account = connect_to_plex(user_id)
    if not account:
        logging.error(f"Could not connect to Plex for user {user_id}")
        return False
    
    try:
        # First try to find the item in user's libraries
        plex_item = find_plex_item_by_imdb_id(user_id, imdb_id)
        
        if plex_item:
            # Add using Plex item object
            account.addToWatchlist(plex_item)
            logging.info(f"Added Plex item {plex_item.title} to watchlist for user {user_id}")
        else:
            # Add using just the IMDb ID
            account.addToWatchlist(imdb_id)
            logging.info(f"Added IMDb ID {imdb_id} to watchlist for user {user_id}")
        
        return True
    except Exception as e:
        logging.error(f"Error adding to Plex watchlist for user {user_id}: {e}")
        
        # Fallback to the existing method using recbyhistory
        logging.info(f"Trying fallback method via recbyhistory for {imdb_id}")
        recbyhistory_url = os.environ.get("RECBYHISTORY_URL", "http://recbyhistory:5335")
        try:
            r = requests.post(f"{recbyhistory_url}/add_to_watchlist",
                             json={
                                 "user_id": user_id,
                                 "imdb_id": imdb_id,
                                 "media_type": "movie"  # Default to movie
                             })
            r.raise_for_status()
            return r.json().get("status") == "success"
        except Exception as e2:
            logging.error(f"Fallback method failed: {e2}")
            return False
        


@app.route('/api/approve/<int:request_id>', methods=['POST'])
def approve_request(request_id):
    """Approve a watchlist request and add to Plex watchlist"""
    data = request.json
    admin_id = data.get('admin_id')
    
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
    
    # Add detailed logging
    logging.info(f"Checking admin status for user_id: {user_id}")
    
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        # Log the request we're about to make
        logging.info(f"Making request to {plexauth_url}/connect for user {user_id}")
        
        r = requests.post(f"{plexauth_url}/connect", 
                         json={"user_id": user_id, "type": "account"})
        r.raise_for_status()
        data = r.json()
        
        # Log the response
        is_admin = data.get("is_admin", False)
        logging.info(f"Admin check for {user_id}: {is_admin}")
        
        return jsonify({"is_admin": is_admin})
    except Exception as e:
        logging.error(f"Error checking admin status for {user_id}: {e}")
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

import os
import requests
import logging


def get_media_details_from_imdb(imdb_id, media_type="movie"):
    """
    Converts an IMDb ID to media details (title and tvdbId) using the TMDb API.
    
    This function calls TMDb's "find" endpoint to get details by the provided IMDb ID.
    
    Args:
        imdb_id (str): The IMDb ID of the media.
        media_type (str): "movie" for movies or "tv" for TV shows.
        
    Returns:
        dict: A dictionary containing 'title' and 'tvdbId'.
        
    Raises:
        Exception: If no results are found or if TMDb API fails.
    """
    tmdb_api_key = os.environ.get("TMDB_API_KEY")
    if not tmdb_api_key:
        raise Exception("TMDB_API_KEY not set in environment variables.")
        
    # TMDb find endpoint for external IDs
    url = f"https://api.themoviedb.org/3/find/{imdb_id}"
    params = {
        "api_key": tmdb_api_key,
        "external_source": "imdb_id"
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    # For movies, the results are in 'movie_results'; for TV shows in 'tv_results'
    if media_type == "movie":
        results = data.get("movie_results", [])
    else:
        results = data.get("tv_results", [])
    
    if not results:
        raise Exception(f"No results found for IMDb ID: {imdb_id}")
    
    result = results[0]
    # For movies, the title field is 'title'; for TV shows it's 'name'
    title = result.get("title") if media_type == "movie" else result.get("name")
    # tvdb_id is provided in TV results; for movies, set it to 0
    tvdb_id = result.get("tvdb_id", 0) if media_type == "tv" else 0
    
    return {"title": title, "tvdbId": tvdb_id}

def request_media_from_overseer(imdb_id, media_type="movie"):
    """
    Converts an IMDb ID to media details using TMDb API, searches Overseerr by title,
    compares the tvdbId (if media_type is "tv") and sends a request to Overseerr.
    
    Steps:
      1. Convert the IMDb ID to media details (title and tvdbId).
      2. Search Overseerr using the media title.
      3. Iterate through search results to find a match:
         - For TV shows, compare the tvdbId.
         - For movies, compare the title.
      4. Build the payload and send a POST request to Overseerr.
    
    Args:
        imdb_id (str): The IMDb ID of the media.
        media_type (str): "movie" for movies or "tv" for TV shows (default: "movie").
        
    Returns:
        dict: The JSON response from Overseerr or error details.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    overseerr_api_key = os.environ.get("OVERSEERR_API_KEY")
    
    # Setup headers with the API key if available.
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    if overseerr_api_key:
        headers["X-Api-Key"] = overseerr_api_key

    # Step 1: Convert IMDb ID to media details.
    try:
        details = get_media_details_from_imdb(imdb_id, media_type)
    except Exception as e:
        logging.error(f"Error converting IMDb ID {imdb_id}: {e}")
        return {"error": str(e)}
    
    title = details["title"]
    expected_tvdb_id = details["tvdbId"]
    
    # Step 2: Search Overseerr by media title.
    search_endpoint = f"{overseerr_url}/api/v1/search"
    params = {"query": title}
    try:
        search_response = requests.get(search_endpoint, params=params, headers=headers)
        search_response.raise_for_status()
        search_results = search_response.json()
    except Exception as e:
        logging.error(f"Error searching Overseerr for title '{title}': {e}")
        return {"error": str(e)}
    
    # Step 3: Find the matching media in Overseerr.
    matched_media = None
    for item in search_results:
        if media_type == "tv":
            # Compare tvdbId for TV shows.
            if item.get("tvdbId") == expected_tvdb_id:
                matched_media = item
                break
        else:
            # For movies, compare titles (case-insensitive).
            if item.get("title", "").lower() == title.lower():
                matched_media = item
                break
    
    if not matched_media:
        error_msg = f"No matching media found in Overseerr for title '{title}' and tvdbId {expected_tvdb_id}"
        logging.error(error_msg)
        return {"error": error_msg}
    
    mediaId = matched_media.get("id")
    tvdbId = matched_media.get("tvdbId", 0)
    
    # Step 4: Build payload and send the request to Overseerr.
    payload = {
        "mediaType": media_type,          # "movie" or "tv"
        "mediaId": mediaId,               # Overseerr's internal media ID
        "tvdbId": tvdbId,                 # TVDB ID (0 for movies)
        "seasons": [1] if media_type.lower() == "tv" else [],
        "is4k": False,
        "serverId": 0,
        "profileId": 0,
        "rootFolder": "string",
        "languageProfileId": 0,
        "userId": 0
    }
    
    request_endpoint = f"{overseerr_url}/api/v1/request"
    try:
        request_response = requests.post(request_endpoint, json=payload, headers=headers)
        request_response.raise_for_status()
        logging.info(f"Successfully sent request for media '{title}' (Overseerr mediaId: {mediaId})")
        return request_response.json()
    except Exception as e:
        logging.error(f"Error sending request for media '{title}': {e}")
        return {"error": str(e)}

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

@app.route('/test_admin/<user_id>')
def test_admin(user_id):
    """Direct test endpoint for admin status"""
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.post(f"{plexauth_url}/connect", 
                         json={"user_id": user_id, "type": "account"})
        full_response = r.json()
        
        # Create a detailed response for debugging
        return jsonify({
            "user_id": user_id,
            "is_admin": full_response.get("is_admin", False),
            "full_response": full_response,
            "plexauth_url": plexauth_url
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5333)