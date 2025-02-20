# recbyhistory/app.py

import os
import uvicorn
import requests
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta

# Import modules from the project
from db import Database
from get_history import PlexHistory
from rec import (
    print_history_groups,
    generate_discovery_recommendations,
    get_ai_search_results
)
from auth_client import PlexAuthClient  # עדיין נדרש אם add_to_watchlist משתמש בזה

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

app = FastAPI(
    title="RecByHistory",
    description="A recommendation engine based on user watch history using PlexAuthClient for Plex authentication.",
    version="1.0"
)

# ----------------- Request Models -----------------
class InitRequest(BaseModel):
    user_id: str
    gemini_api_key: str
    tmdb_api_key: str
    monthly_movies: int
    monthly_series: int

class DiscoveryRequest(BaseModel):
    user_id: str
    gemini_api_key: str
    tmdb_api_key: str
    num_movies: int
    num_series: int
    extra_elements: str

class AISearchRequest(BaseModel):
    user_id: str
    gemini_api_key: str
    tmdb_api_key: str
    query: str

class NotificationRequest(BaseModel):
    user_id: str
    title: str
    message: str
    image_url: str

class WatchlistRequest(BaseModel):
    user_id: str
    imdb_id: str
    media_type: str  # e.g., "movie" or "series"


# ----------------- Scheduled Task Functions -----------------
def run_history_task(user_id: str):
    """
    Runs the daily watch history update for the given user ID.
    """
    try:
        db = Database(user_id)
        plex = PlexHistory(user_id)
        plex.get_watch_history(db)
        logging.info(f"History task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in history task for user {user_id}: {e}")

def run_taste_task(user_id: str):
    """
    Runs the weekly taste update for the given user ID.
    """
    try:
        db = Database(user_id)
        print_history_groups(db)
        logging.info(f"Taste task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in taste task for user {user_id}: {e}")

def run_monthly_task(user_id: str):
    """
    Runs the monthly recommendations generation for the given user ID.
    """
    try:
        db = Database(user_id)
        print_history_groups(db)
        logging.info(f"Monthly recommendations task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in monthly task for user {user_id}: {e}")


# גלובלי: נזכור מתי בפעם האחרונה הרצנו משימות לכל user_id
USER_SCHEDULE = {}

def get_all_users_from_plexauth():
    """
    Calls the plexauthgui service (which also acts like auth_service) to retrieve all users.
    נניח שיש אנדפוינט /users שמחזיר JSON כמו: { 'users': [ 'user_xxx', 'user_yyy' ] }
    """
    plexauth_url = os.environ.get("PLEXAUTH_URL", "http://plexauthgui:5332")
    try:
        r = requests.get(f"{plexauth_url}/users", timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get('users', [])
    except Exception as e:
        logging.error(f"Error fetching user list from plexauthgui: {e}")
        return []

def process_all_users():
    """
    רץ בכל דקה (IntervalTrigger). שולף את רשימת המשתמשים מ־plexauthgui, 
    ומריץ לכל משתמש את המשימות (היסטוריה, טעם, המלצות) על פי לוחות זמנים: יום, שבוע, חודש.
    """
    global USER_SCHEDULE

    user_list = get_all_users_from_plexauth()
    if not user_list:
        logging.info("No users returned from plexauthgui; skipping tasks.")
        return

    now = datetime.utcnow()
    for user_id in user_list:
        if user_id not in USER_SCHEDULE:
            logging.info(f"New user {user_id} detected; running tasks immediately.")
            USER_SCHEDULE[user_id] = {
                'last_history': now,
                'last_taste': now,
                'last_monthly': now
            }
            run_history_task(user_id)
            run_taste_task(user_id)
            run_monthly_task(user_id)
        else:
            times = USER_SCHEDULE[user_id]
            # History: daily
            if now - times['last_history'] >= timedelta(days=1):
                logging.info(f"Daily interval for user {user_id}. Running history task.")
                run_history_task(user_id)
                USER_SCHEDULE[user_id]['last_history'] = now
            # Taste: weekly
            if now - times['last_taste'] >= timedelta(days=7):
                logging.info(f"Weekly interval for user {user_id}. Running taste task.")
                run_taste_task(user_id)
                USER_SCHEDULE[user_id]['last_taste'] = now
            # Monthly
            if now - times['last_monthly'] >= timedelta(days=30):
                logging.info(f"Monthly interval for user {user_id}. Running monthly recs.")
                run_monthly_task(user_id)
                USER_SCHEDULE[user_id]['last_monthly'] = now


scheduler = BackgroundScheduler()
scheduler.add_job(process_all_users, IntervalTrigger(minutes=1))
scheduler.start()

@app.on_event("startup")
def startup_event():
    logging.info("Application startup: running initial tasks for all users from plexauthgui.")
    # run once
    process_all_users()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()
    logging.info("Application shutdown: scheduler stopped.")

# ----------------- Endpoints -----------------
@app.post("/init")
def init_data(request: InitRequest):
    """
    Initialize DB, load Plex watch history, and generate monthly recommendations for the user.
    """
    logging.info(f"Received init request for user {request.user_id}")
    os.environ["GEMINI_API_KEY"] = request.gemini_api_key
    os.environ["TMDB_API_KEY"] = request.tmdb_api_key

    db = Database(request.user_id)
    plex = PlexHistory(request.user_id)
    history = plex.get_watch_history(db)

    for item in history:
        if 'episodes' not in item:
            db.add_item(
                title=item['title'],
                imdb_id=item['imdbID'],
                user_rating=item['userRating'],
                resolution=item.get('resolution', "Unknown")
            )
        else:
            db.add_item(
                title=item['title'],
                imdb_id=item['imdbID'],
                user_rating=item['userRating'],
                resolution=item.get('resolution', "Unknown")
            )
            for ep in item['episodes']:
                db.add_item(
                    title=ep['title'],
                    imdb_id=ep['imdbID'],
                    user_rating=ep['userRating'],
                    resolution=ep.get('resolution', "Unknown")
                )

    from rec import NUM_MOVIES, NUM_SERIES
    NUM_MOVIES = request.monthly_movies
    NUM_SERIES = request.monthly_series

    print_history_groups(db)
    logging.info("Init process completed successfully.")
    return {"status": "OK", "message": "DB, history, and monthly recommendations created."}

@app.get("/taste")
def get_user_taste_endpoint(user_id: str):
    db = Database(user_id)
    taste = db.get_latest_user_taste(user_id)
    logging.info(f"Retrieved taste for user {user_id}: {taste}")
    return {"user_id": user_id, "taste": taste}

@app.get("/history")
def get_user_history(user_id: str):
    db = Database(user_id)
    rows = db.get_all_items()
    results = []
    for row in rows:
        results.append({
            "id": row[0],
            "title": row[1],
            "imdb_id": row[2],
            "user_rating": row[3],
            "resolution": row[4],
            "added_at": row[5]
        })
    logging.info(f"Returning history for user {user_id} with {len(results)} items.")
    return {"user_id": user_id, "history": results}

@app.get("/monthly_recommendations")
def get_monthly_recommendations(user_id: str):
    db = Database(user_id)
    cursor = db.conn.cursor()
    cursor.execute('SELECT * FROM ai_recommendations WHERE group_id="all"')
    rows = cursor.fetchall()
    recs = []
    for row in rows:
        recs.append({
            "id": row[0],
            "group_id": row[1],
            "title": row[2],
            "imdb_id": row[3],
            "image_url": row[4],
            "created_at": row[5]
        })
    logging.info(f"Returning monthly recommendations for user {user_id}.")
    return {"user_id": user_id, "monthly_recommendations": recs}

@app.post("/discovery_recommendations")
def post_discovery_recommendations(request: DiscoveryRequest):
    logging.info(f"Received discovery recommendations request for user {request.user_id}")
    final_recs = generate_discovery_recommendations(
        user_id=request.user_id,
        gemini_api_key=request.gemini_api_key,
        tmdb_api_key=request.tmdb_api_key,
        num_movies=request.num_movies,
        num_series=request.num_series,
        extra_elements=request.extra_elements
    )
    logging.info("Discovery recommendations generated.")
    return {"discovery_recommendations": final_recs}

@app.post("/ai_search")
def ai_search(request: AISearchRequest):
    db = Database(request.user_id)
    user_taste = db.get_latest_user_taste(request.user_id) or "No user taste available."
    system_instruction = (
        "Perform a search based on the following query and user taste.\n"
        "Query: " + request.query + "\n"
        "User Taste: " + user_taste + "\n\n"
        "Return results in JSON format with keys: 'title', 'imdb_id', 'image_url'."
    )
    results = get_ai_search_results(request.query, system_instruction)
    logging.info(f"AI search executed for user {request.user_id}.")
    return {"user_id": request.user_id, "search_results": results}


# דוגמה לפונקציה שמבצעת Add to watchlist דרך PlexAuthClient (אם זה נחוץ)
def add_to_plex_watchlist(user_id: str, imdb_id: str, media_type: str):
    """
    Connects to Plex using PlexAuthClient to obtain a token and calls a custom Plex endpoint
    to add the specified content to the user's watchlist.
    """
    auth_client = PlexAuthClient()  # PlexAuthClient defined in auth_client.py (using requests)
    # Request connection to Plex to obtain token (this mimics previous logic)
    data = {"user_id": user_id, "type": "account"}
    response = requests.post(f"{auth_client.base_url}/connect", json=data)
    if response.status_code != 200:
        logging.error(f"Failed to obtain Plex token for user {user_id}")
        return {"status": "Failed", "message": "Could not obtain Plex token."}
    token = response.json().get("token")
    if not token:
        logging.error(f"No token received for user {user_id}")
        return {"status": "Failed", "message": "No Plex token received."}

    plex_server_url = os.environ.get("PLEX_SERVER_URL")
    if not plex_server_url:
        logging.error("PLEX_SERVER_URL not configured.")
        return {"status": "Failed", "message": "PLEX_SERVER_URL not configured."}
    # Construct the custom endpoint URL to add to watchlist
    url = f"{plex_server_url}/api/watchlist/add?X-Plex-Token={token}"
    payload = {
         "imdb_id": imdb_id,
         "media_type": media_type
    }
    try:
         r = requests.post(url, json=payload, timeout=10)
         r.raise_for_status()
         logging.info(f"Successfully added imdb_id {imdb_id} to watchlist for user {user_id} on Plex server.")
         return {"status": "OK", "response": r.json()}
    except Exception as e:
         logging.error(f"Error adding to Plex watchlist for user {user_id}: {e}")
         return {"status": "Failed", "message": str(e)}

@app.post("/add_to_watchlist")
def add_to_watchlist(request: WatchlistRequest):
    logging.info(f"Adding content {request.imdb_id} to Plex watchlist for user {request.user_id}")
    result = add_to_plex_watchlist(request.user_id, request.imdb_id, request.media_type)
    if result.get("status") == "OK":
        return {"status": "OK", "message": f"Content {request.imdb_id} added to Plex watchlist for user {request.user_id}."}
    else:
        logging.error(f"Failed to add content {request.imdb_id} to Plex watchlist: {result.get('message')}")
        raise HTTPException(status_code=500, detail=f"Error adding to Plex watchlist: {result.get('message')}")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 5335)), reload=True)
