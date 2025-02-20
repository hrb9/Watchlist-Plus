# recbyhistory/app.py
import os
import uvicorn
import requests
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from rec import print_history_groups, generate_discovery_recommendations, get_ai_search_results


# Import existing modules from the project
from db import Database
from get_history import PlexHistory
from rec import print_history_groups, generate_discovery_recommendations, get_ai_search_results
from config import OVERSEERR_URL, OVERSEERR_API_TOKEN

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

# ----------------- Middleware -----------------
async def verify_overseerr_token(x_overseerr_token: str = Header(...)):
    expected_token = os.environ.get("OVERSEERR_API_TOKEN")
    if not expected_token or x_overseerr_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid Overseerr Token")
    return x_overseerr_token

# ----------------- Endpoints -----------------

@app.post("/init", dependencies=[Depends(verify_overseerr_token)])
def init_data(request: InitRequest):
    """
    Initialize DB, import Plex watch history, and generate monthly recommendations.
    """
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

    # Temporarily set recommendation numbers
    from rec import NUM_MOVIES, NUM_SERIES
    NUM_MOVIES = request.monthly_movies
    NUM_SERIES = request.monthly_series

    # Generate monthly recommendations and update DB
    print_history_groups(db)
    
    return {"status": "OK", "message": "DB, history, and monthly recommendations created."}

@app.get("/taste", dependencies=[Depends(verify_overseerr_token)])
def get_user_taste_endpoint(user_id: str):
    db = Database(user_id)
    taste = db.get_latest_user_taste(user_id)
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
    return {"user_id": user_id, "monthly_recommendations": recs}

@app.post("/discovery_recommendations", dependencies=[Depends(verify_overseerr_token)])
def post_discovery_recommendations(request: DiscoveryRequest):
    final_recs = generate_discovery_recommendations(
        user_id=request.user_id,
        gemini_api_key=request.gemini_api_key,
        tmdb_api_key=request.tmdb_api_key,
        num_movies=request.num_movies,
        num_series=request.num_series,
        extra_elements=request.extra_elements
    )
    return {"discovery_recommendations": final_recs}

@app.get("/for_me_recommendations", dependencies=[Depends(verify_overseerr_token)])
def for_me_recommendations(user_id: str, gemini_api_key: str, tmdb_api_key: str):
    """
    Generates "Recommended for me" results using an open prompt based on the user's taste.
    """
    os.environ["GEMINI_API_KEY"] = gemini_api_key
    os.environ["TMDB_API_KEY"] = tmdb_api_key
    db = Database(user_id)
    user_taste = db.get_latest_user_taste(user_id) or "No user taste available."
    
    # Define an "open" prompt for AI-based search
    open_prompt = (
        "Perform an open search based on the user's taste.\n"
        "User Taste: " + user_taste + "\n"
        "Return recommendations in JSON format with keys: 'title', 'imdb_id', 'image_url'."
    )
    
    results = get_ai_search_results("", open_prompt)
    return {"user_id": user_id, "for_me_recommendations": results}

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
    return {"user_id": request.user_id, "search_results": results}

@app.post("/send_notification", dependencies=[Depends(verify_overseerr_token)])
def send_notification(notification: NotificationRequest):
    overseerr_url = os.environ.get("OVERSEERR_URL")
    if not overseerr_url:
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
        return {"status": "OK", "response": resp.json()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Notification error: {e}")

@app.post("/add_to_watchlist", dependencies=[Depends(verify_overseerr_token)])
def add_to_watchlist(request: WatchlistRequest):
    """
    Adds a content item to the Plex watchlist for the given user using Overseerr's built-in mechanism.
    Assumes Overseerr provides an endpoint (e.g., POST /api/v1/plex/watchlist) for this purpose.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL")
    if not overseerr_url:
        raise HTTPException(status_code=500, detail="Overseerr URL not configured.")
    
    payload = {
        "user_id": request.user_id,
        "imdb_id": request.imdb_id,
        "media_type": request.media_type
    }
    try:
        resp = requests.post(f"{overseerr_url}/api/v1/plex/watchlist", json=payload, timeout=10)
        resp.raise_for_status()
        return {"status": "OK", "response": resp.json()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding to watchlist: {e}")

# ----------------- Scheduled Tasks -----------------
def scheduled_update_history():
    """
    Updates watch history for all users from Overseerr (daily).
    """
    from config import OVERSEERR_URL, OVERSEERR_API_TOKEN
    try:
        response = requests.get(f"{OVERSEERR_URL}/api/v1/users", headers={"Authorization": f"Bearer {OVERSEERR_API_TOKEN}"}, timeout=10)
        response.raise_for_status()
        users = response.json()  # Expecting a list of user objects with an 'id' field.
    except Exception as e:
        print(f"Error fetching users from Overseerr: {e}")
        return

    for user in users:
        user_id = str(user.get("id"))
        if user_id:
            db = Database(user_id)
            plex = PlexHistory(user_id)
            plex.get_watch_history(db)
            print(f"Updated history for user {user_id}")
        else:
            print("User missing id; skipping.")
    print("Daily watch history update completed.")

def scheduled_update_user_taste():
    """
    Updates user taste for all users from Overseerr (weekly).
    """
    from config import OVERSEERR_URL, OVERSEERR_API_TOKEN
    try:
        response = requests.get(f"{OVERSEERR_URL}/api/v1/users", headers={"Authorization": f"Bearer {OVERSEERR_API_TOKEN}"}, timeout=10)
        response.raise_for_status()
        users = response.json()
    except Exception as e:
        print(f"Error fetching users from Overseerr: {e}")
        return

    for user in users:
        user_id = str(user.get("id"))
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        tmdb_api_key = os.environ.get("TMDB_API_KEY")
        if user_id and gemini_api_key and tmdb_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            os.environ["TMDB_API_KEY"] = tmdb_api_key
            db = Database(user_id)
            print_history_groups(db)
            print(f"Updated user taste for user {user_id}")
        else:
            print(f"Skipping user {user_id} due to missing credentials or id.")
    print("Weekly user taste update completed.")

def scheduled_generate_monthly_recs():
    """
    Generates monthly recommendations for all users from Overseerr (monthly).
    """
    from config import OVERSEERR_URL, OVERSEERR_API_TOKEN
    try:
        response = requests.get(f"{OVERSEERR_URL}/api/v1/users", headers={"Authorization": f"Bearer {OVERSEERR_API_TOKEN}"}, timeout=10)
        response.raise_for_status()
        users = response.json()
    except Exception as e:
        print(f"Error fetching users from Overseerr: {e}")
        return

    for user in users:
        user_id = str(user.get("id"))
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        tmdb_api_key = os.environ.get("TMDB_API_KEY")
        if user_id and gemini_api_key and tmdb_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            os.environ["TMDB_API_KEY"] = tmdb_api_key
            db = Database(user_id)
            print_history_groups(db)
            print(f"Generated monthly recommendations for user {user_id}")
        else:
            print(f"Skipping user {user_id} due to missing credentials or id.")
    print("Monthly recommendations generation completed.")

scheduler = BackgroundScheduler()

# Schedule tasks:
scheduler.add_job(scheduled_update_history, CronTrigger(hour=2, minute=0))  # Daily at 02:00
scheduler.add_job(scheduled_update_user_taste, CronTrigger(day_of_week='sun', hour=3, minute=0))  # Weekly on Sunday at 03:00
scheduler.add_job(scheduled_generate_monthly_recs, CronTrigger(day=1, hour=4, minute=0))  # Monthly on the 1st at 04:00

scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()
# ----------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 5335)), reload=True)
