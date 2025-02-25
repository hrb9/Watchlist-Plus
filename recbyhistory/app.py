# recbyhistory/app.py

import os
import uvicorn
import requests
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Import modules from the project
from db import Database
from get_history import PlexHistory
from rec import (
    print_history_groups,
    generate_discovery_recommendations,
    get_ai_search_results
)
from auth_client import PlexAuthClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
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
    מריץ עדכון היסטוריית צפייה יומית למשתמש (כתיבה חדשה אם נמצאים פריטים isWatched).
    """
    try:
        db = Database(user_id)
        plex = PlexHistory(user_id)
        plex.get_watch_history(db)  # בפנים מבוצעת כתיבה לטבלת watch_history
        logging.info(f"History task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in history task for user {user_id}: {e}")

def run_taste_task(user_id: str):
    """
    מריץ עדכון 'טעם' שבועי למשתמש.
    אינו כותב היסטוריה חדשה, רק מנתח הטבלה ומפיק taste.
    """
    try:
        db = Database(user_id)
        print_history_groups(db)
        logging.info(f"Taste task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in taste task for user {user_id}: {e}")

def run_monthly_task(user_id: str):
    """
    מריץ יצירת המלצות חודשיות למשתמש.
    אינו כותב היסטוריה חדשה, רק משתמש בנתונים שכבר קיימים בטבלה.
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
    שולף את רשימת המשתמשים משירות plexauthgui (אנדפוינט /users).
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
    global USER_SCHEDULE
    
    user_list = get_all_users_from_plexauth()
    if not user_list:
        logging.info("No users returned from plexauthgui; skipping tasks.")
        return

    now = datetime.utcnow()
    
    for user_id in user_list:
        if user_id not in USER_SCHEDULE:
            # Initialize new user schedule with staggered times
            logging.info(f"New user {user_id} detected; initializing schedule.")
            USER_SCHEDULE[user_id] = {
                'last_history': now - timedelta(days=1),  # Run history immediately 
                'last_taste': now - timedelta(days=7),    # Run taste immediately
                'last_monthly': now - timedelta(days=30)  # Run monthly immediately
            }
        
        times = USER_SCHEDULE[user_id]
        
        # Add buffer time to avoid multiple runs in same day
        buffer_hours = 2
        
        # Daily history check with buffer
        if now - times['last_history'] >= timedelta(days=1, hours=buffer_hours):
            logging.info(f"Running daily history task for user {user_id}")
            run_history_task(user_id)
            USER_SCHEDULE[user_id]['last_history'] = now
            
        # Weekly taste check with buffer
        if now - times['last_taste'] >= timedelta(days=7, hours=buffer_hours):
            logging.info(f"Running weekly taste task for user {user_id}")
            run_taste_task(user_id)
            USER_SCHEDULE[user_id]['last_taste'] = now
            
        # Monthly recommendations check with buffer
        if now - times['last_monthly'] >= timedelta(days=30, hours=buffer_hours):
            logging.info(f"Running monthly recommendations task for user {user_id}")
            run_monthly_task(user_id)
            USER_SCHEDULE[user_id]['last_monthly'] = now

# ----------------- Lifespan Event Handler -----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    # Change from every minute to every hour
    scheduler.add_job(process_all_users, IntervalTrigger(hours=1))
    scheduler.start()
    logging.info("Application startup: initializing scheduled tasks.")
    process_all_users()  # Initial run
    try:
        yield
    finally:
        scheduler.shutdown()
        logging.info("Application shutdown: scheduler stopped.")

app = FastAPI(
    title="RecByHistory",
    description="A recommendation engine based on user watch history, with scheduled & initial writes only.",
    version="1.0",
    lifespan=lifespan
)

# ----------------- Endpoints -----------------
@app.post("/init")
def init_data(request: InitRequest):
    """
    הפעלה ראשונית: כותבת היסטוריית צפייה + המלצות חדשות,
    ורק בפעולה זו (או במשימות מתוזמנות) מתרחשת כתיבה חדשה של history.
    """
    logging.info(f"Received init request for user {request.user_id}")

    # הגדרת מפתחות בסביבה
    os.environ["GEMINI_API_KEY"] = request.gemini_api_key
    os.environ["TMDB_API_KEY"] = request.tmdb_api_key

    db = Database(request.user_id)
    plex = PlexHistory(request.user_id)
    # get_watch_history - כותב לתוך db.watch_history
    history = plex.get_watch_history(db)

    # הוספת הפריטים שהוחזרו ב-history גם לטבלת watch_history (אם עוד לא הוספת בפונקציה עצמה)
    # במידה וכבר כותבים בתוך get_watch_history, אין צורך בלולאה זו
    for item in history:
        db.add_item(
            title=item['title'],
            imdb_id=item['imdbID'],
            user_rating=item['userRating'],
            resolution=item.get('resolution', "Unknown")
        )
        if 'episodes' in item:
            for ep in item['episodes']:
                db.add_item(
                    title=ep['title'],
                    imdb_id=ep['imdbID'],
                    user_rating=ep['userRating'],
                    resolution=ep.get('resolution', "Unknown")
                )

    # הגדרת מספר הסרטים/סדרות להמלצות חודשיות
    from rec import NUM_MOVIES, NUM_SERIES
    NUM_MOVIES = request.monthly_movies
    NUM_SERIES = request.monthly_series

    # הפקת המלצות
    print_history_groups(db)
    logging.info("Init process completed successfully.")
    return {"status": "OK", "message": "DB, history, and monthly recommendations created."}

@app.get("/taste")
def get_user_taste_endpoint(user_id: str):
    """
    מחזיר את טבלת taste האחרונה, *ללא* כתיבה חדשה ל-history.
    """
    db = Database(user_id)
    taste = db.get_latest_user_taste(user_id)
    logging.info(f"Retrieved taste for user {user_id}: {taste}")
    return {"user_id": user_id, "taste": taste}

@app.get("/history")
def get_user_history(user_id: str):
    """
    מחזיר מידע מטבלת all_items, ללא עדכון היסטוריה חדש.
    """
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
    """
    מחזיר המלצות חודשיות (מקראיות מתוך ai_recommendations),
    ללא כתיבת היסטוריה חדשה.
    """
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
    """
    מבצע המלצות Discovery בלי עדכון היסטוריה.
    """
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
    """
    מבצע חיפוש AI בהתבסס על taste והיסטוריה קיימת, ללא עדכון היסטוריה חדש.
    """
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

@app.post("/add_to_watchlist")
def add_to_watchlist(request: WatchlistRequest):
    """Forward the request to watchlistrequests service"""
    watchlist_url = os.environ.get("WATCHLIST_URL", "http://watchlistrequests:5333")
    try:
        r = requests.post(f"{watchlist_url}/api/request", json=request.dict(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error(f"Error forwarding watchlist request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5335)),
        reload=True
    )
