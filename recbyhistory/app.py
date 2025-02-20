# recbyhistory/app.py
import os
import uvicorn
import requests
import logging
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta

# Import modules from the project.
from db import Database
from get_history import PlexHistory
from rec import print_history_groups, generate_discovery_recommendations, get_ai_search_results
from config import OVERSEERR_URL, OVERSEERR_API_TOKEN

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

app = FastAPI(
    title="RecByHistory",
    description="A recommendation engine based on user watch history integrated with Overseerr and AI search.",
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

# ----------------- Middleware for Overseerr Token -----------------
async def verify_overseerr_token(x_overseerr_token: str = Header(...)):
    expected_token = os.environ.get("OVERSEERR_API_TOKEN")
    if not expected_token or x_overseerr_token != expected_token:
        logging.error("Invalid Overseerr token provided.")
        raise HTTPException(status_code=401, detail="Invalid Overseerr Token")
    return x_overseerr_token

# ----------------- Global Variables -----------------
CURRENT_USER = None  # dict with keys: id, last_history, last_taste, last_monthly

# ----------------- Helper Functions -----------------
def check_auth_me():
    """
    Calls the /auth/me endpoint using the X-Api-Key header.
    """
    url = f"{OVERSEERR_URL}/api/v1/auth/me"
    headers = {
        "Accept": "application/json",
        "X-Api-Key": os.environ.get("OVERSEERR_API_TOKEN")
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        user = response.json()
        logging.info(f"/auth/me returned user: {user.get('id')} - {user.get('displayName')}")
        return user
    except Exception as e:
        logging.error(f"Error calling /auth/me: {e}. Response content: {response.text if response else 'No response'}")
        return None
    
def run_history_task(user_id):
    try:
        db = Database(user_id)
        plex = PlexHistory(user_id)
        plex.get_watch_history(db)
        logging.info(f"History task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in history task for user {user_id}: {e}")

def run_taste_task(user_id):
    try:
        db = Database(user_id)
        print_history_groups(db)
        logging.info(f"Taste task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in taste task for user {user_id}: {e}")

def run_monthly_task(user_id):
    try:
        db = Database(user_id)
        print_history_groups(db)
        logging.info(f"Monthly recommendations task executed for user {user_id}.")
    except Exception as e:
        logging.error(f"Error in monthly task for user {user_id}: {e}")

def process_current_user_tasks():
    """
    Checks the currently authenticated user via /auth/me and, based on last-run timestamps,
    executes the tasks for history, taste, and monthly recommendations.
    """
    global CURRENT_USER
    user = check_auth_me()
    if not user:
        logging.info("No authenticated user returned from /auth/me.")
        return

    user_id = str(user.get("id"))
    now = datetime.utcnow()

    if CURRENT_USER is None or CURRENT_USER.get("id") != user_id:
        logging.info(f"New user detected: {user_id}. Running all tasks immediately.")
        CURRENT_USER = {
            "id": user_id,
            "last_history": now,
            "last_taste": now,
            "last_monthly": now
        }
        run_history_task(user_id)
        run_taste_task(user_id)
        run_monthly_task(user_id)
    else:
        # Check if sufficient time has passed for each task
        if now - CURRENT_USER.get("last_history", now) >= timedelta(days=1):
            logging.info(f"Daily interval reached for user {user_id}. Running history task.")
            run_history_task(user_id)
            CURRENT_USER["last_history"] = now
        if now - CURRENT_USER.get("last_taste", now) >= timedelta(days=7):
            logging.info(f"Weekly interval reached for user {user_id}. Running taste task.")
            run_taste_task(user_id)
            CURRENT_USER["last_taste"] = now
        if now - CURRENT_USER.get("last_monthly", now) >= timedelta(days=30):
            logging.info(f"Monthly interval reached for user {user_id}. Running monthly recommendations task.")
            run_monthly_task(user_id)
            CURRENT_USER["last_monthly"] = now

# ----------------- Scheduler -----------------
scheduler = BackgroundScheduler()
# Schedule the check_auth_me task every minute
scheduler.add_job(process_current_user_tasks, IntervalTrigger(minutes=1))
scheduler.start()

# ----------------- Startup and Shutdown Events -----------------
@app.on_event("startup")
def startup_event():
    logging.info("Application startup: running initial /auth/me check and tasks...")
    process_current_user_tasks()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()
    logging.info("Application shutdown: scheduler stopped.")

# ----------------- Endpoints -----------------
@app.post("/init", dependencies=[Depends(verify_overseerr_token)])
def init_data(request: InitRequest):
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

@app.get("/taste", dependencies=[Depends(verify_overseerr_token)])
def get_user_taste_endpoint(user_id: str):
    db = Database(user_id)
    taste = db.get_latest_user_taste(user_id)
    logging.info(f"Retrieved taste for user {user_id}: {taste}")
    return {"user_id": user_id, "taste": taste}

@app.get("/history", dependencies=[Depends(verify_overseerr_token)])
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

@app.get("/monthly_recommendations", dependencies=[Depends(verify_overseerr_token)])
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

@app.post("/discovery_recommendations", dependencies=[Depends(verify_overseerr_token)])
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
    logging.info("Discovery recommendations generated and pushed to Overseerr.")
    return {"discovery_recommendations": final_recs}

@app.post("/ai_search", dependencies=[Depends(verify_overseerr_token)])
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

@app.post("/send_notification", dependencies=[Depends(verify_overseerr_token)])
def send_notification(notification: NotificationRequest):
    logging.info(f"Sending notification for user {notification.user_id}")
    overseerr_url = os.environ.get("OVERSEERR_URL")
    if not overseerr_url:
        logging.error("Overseerr URL not configured.")
        raise HTTPException(status_code=500, detail="Overseerr URL not configured.")
    
    payload = {
        "user_id": notification.user_id,
        "title": notification.title,
        "message": notification.message,
        "image_url": notification.image_url
    }
    try:
        resp = requests.post(f"{overseerr_url}/api/v1/notifications", json=payload, timeout=10)
        resp.raise_for_status()
        logging.info(f"Notification sent for user {notification.user_id}")
        return {"status": "OK", "response": resp.json()}
    except Exception as e:
        logging.error(f"Notification error for user {notification.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Notification error: {e}")

@app.post("/add_to_watchlist", dependencies=[Depends(verify_overseerr_token)])
def add_to_watchlist(request: WatchlistRequest):
    logging.info(f"Adding content {request.imdb_id} to watchlist for user {request.user_id}")
    overseerr_url = os.environ.get("OVERSEERR_URL")
    if not overseerr_url:
        logging.error("Overseerr URL not configured.")
        raise HTTPException(status_code=500, detail="Overseerr URL not configured.")
    
    payload = {
        "user_id": request.user_id,
        "imdb_id": request.imdb_id,
        "media_type": request.media_type
    }
    try:
        resp = requests.post(f"{overseerr_url}/api/v1/plex/watchlist", json=payload, timeout=10)
        resp.raise_for_status()
        logging.info(f"Content {request.imdb_id} added to watchlist for user {request.user_id}")
        return {"status": "OK", "response": resp.json()}
    except Exception as e:
        logging.error(f"Error adding to watchlist for user {request.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error adding to watchlist: {e}")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 5335)), reload=True)
