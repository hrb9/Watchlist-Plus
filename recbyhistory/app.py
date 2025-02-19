# recbyhistory/app.py
import os
import uvicorn
import requests
from fastapi import FastAPI, Body, Query, Header, HTTPException, Depends
from pydantic import BaseModel

# Import modules from the project
from db import Database
from get_history import PlexHistory
from rec import print_history_groups, generate_discovery_recommendations

# Import APScheduler for scheduling tasks
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Middleware dependency to verify Overseerr token from header "x-overseerr-token"
async def verify_overseerr_token(x_overseerr_token: str = Header(...)):
    expected_token = os.environ.get("OVERSEERR_API_TOKEN")
    if not expected_token or x_overseerr_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid Overseerr Token")
    return x_overseerr_token

app = FastAPI(
    title="RecByHistory",
    description="A recommendation system based on user watch history integrated with Overseerr and AI search.",
    version="1.0"
)

# Request models
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

@app.post("/init", dependencies=[Depends(verify_overseerr_token)])
def init_data(request: InitRequest):
    """
    Initialize the DB, load watch history, and generate monthly recommendations.
    """
    # Set API keys in environment
    os.environ["GEMINI_API_KEY"] = request.gemini_api_key
    os.environ["TMDB_API_KEY"] = request.tmdb_api_key
    
    db = Database(request.user_id)
    
    # Retrieve Plex watch history
    plex = PlexHistory(request.user_id)
    history = plex.get_watch_history(db)
    
    # Save each history item to DB
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
    
    # Temporarily set the number of recommendations for monthly suggestions
    from rec import NUM_MOVIES, NUM_SERIES
    NUM_MOVIES = request.monthly_movies
    NUM_SERIES = request.monthly_series

    # Generate monthly recommendations and update DB
    print_history_groups(db)
    
    return {"status": "OK", "message": "DB, history, and monthly recommendations created."}

@app.get("/taste", dependencies=[Depends(verify_overseerr_token)])
def get_user_taste_endpoint(user_id: str):
    """
    Returns the user's taste profile.
    """
    db = Database(user_id)
    taste = db.get_latest_user_taste(user_id)
    return {"user_id": user_id, "taste": taste}

@app.get("/history", dependencies=[Depends(verify_overseerr_token)])
def get_user_history(user_id: str):
    """
    Returns the watch history.
    """
    db = Database(user_id)
    rows = db.get_all_items()  # watch_history
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
    """
    Returns the monthly recommendations (group_id='all').
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
    return {"user_id": user_id, "monthly_recommendations": recs}

@app.post("/discovery_recommendations", dependencies=[Depends(verify_overseerr_token)])
def post_discovery_recommendations(request: DiscoveryRequest):
    """
    Generates Discovery recommendations by calling generate_discovery_recommendations from rec.py.
    """
    final_recs = generate_discovery_recommendations(
        user_id=request.user_id,
        gemini_api_key=request.gemini_api_key,
        tmdb_api_key=request.tmdb_api_key,
        num_movies=request.num_movies,
        num_series=request.num_series,
        extra_elements=request.extra_elements
    )
    return {"discovery_recommendations": final_recs}

@app.get("/discovery_recommendations", dependencies=[Depends(verify_overseerr_token)])
def get_discovery_recommendations(user_id: str):
    """
    Returns Discovery recommendations (group_id='discovery') from the DB.
    """
    db = Database(user_id)
    cursor = db.conn.cursor()
    cursor.execute('SELECT * FROM ai_recommendations WHERE group_id="discovery"')
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
    return {"user_id": user_id, "discovery_recommendations": recs}

@app.post("/ai_search", dependencies=[Depends(verify_overseerr_token)])
def ai_search(request: AISearchRequest):
    """
    Performs AI-based search based on the user's taste.
    Returns search results similar to the current Overseerr recommendations interface.
    """
    db = Database(request.user_id)
    user_taste = db.get_latest_user_taste(request.user_id) or "No user taste available."
    
    system_instruction = (
        "You will perform an open search based on the following query and user taste.\n"
        "Query: " + request.query + "\n"
        "User Taste: " + user_taste + "\n\n"
        "Return the search results in the same format as the current Overseerr recommendations interface."
    )
    
    from rec import google_search_tool
    from google.genai.types import GenerateContentConfig
    from google import genai
    client = genai.Client(api_key=request.gemini_api_key)
    config = GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=1024,
        response_mime_type="text/plain",
        tools=[google_search_tool],
    )
    response = client.models.generate_content(
        contents="",
        model="gemini-2.0-flash-exp",
        config=config,
    )
    return {"search_results": response.text.strip()}

@app.post("/send_notification", dependencies=[Depends(verify_overseerr_token)])
def send_notification(notification: NotificationRequest):
    """
    Sends a notification using Overseerr's notification system including an image.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL")
    if not overseerr_url:
        raise HTTPException(status_code=500, detail="Overseerr URL is not configured.")
    
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

# ----------------- SCHEDULING FUNCTIONS -----------------
def get_all_users_from_overseerr():
    """
    Fetches all users from Overseerr using the API.
    Assumes the endpoint GET /api/v1/users returns a list of user objects.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    token = os.environ.get("OVERSEERR_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{overseerr_url}/api/v1/users", headers=headers, timeout=10)
        response.raise_for_status()
        users = response.json()  # expecting a list of user objects
        return users
    except Exception as e:
        print(f"Error fetching users from Overseerr: {e}")
        return []

def scheduled_update_history():
    """Updates watch history for all users from Overseerr (runs daily)."""
    users = get_all_users_from_overseerr()
    for user in users:
        # Assuming each user object has an 'id' field; adjust if necessary.
        user_id = user.get("id")
        if user_id:
            db = Database(str(user_id))
            plex = PlexHistory(str(user_id))
            plex.get_watch_history(db)
            print(f"Updated history for user {user_id}")
        else:
            print("User missing id; skipping.")
    print("Scheduled watch history update completed for all users.")

def scheduled_update_user_taste():
    """Updates user taste for all users from Overseerr (runs weekly)."""
    users = get_all_users_from_overseerr()
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    tmdb_api_key = os.environ.get("TMDB_API_KEY")
    for user in users:
        user_id = user.get("id")
        if user_id and gemini_api_key and tmdb_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            os.environ["TMDB_API_KEY"] = tmdb_api_key
            db = Database(str(user_id))
            print_history_groups(db)
            print(f"Updated user taste for user {user_id}")
        else:
            print(f"Skipping user {user_id} due to missing credentials or id.")
    print("Scheduled user taste update completed for all users.")

def scheduled_generate_monthly_recs():
    """Generates monthly recommendations for all users from Overseerr (runs monthly)."""
    users = get_all_users_from_overseerr()
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    tmdb_api_key = os.environ.get("TMDB_API_KEY")
    for user in users:
        user_id = user.get("id")
        if user_id and gemini_api_key and tmdb_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            os.environ["TMDB_API_KEY"] = tmdb_api_key
            db = Database(str(user_id))
            print_history_groups(db)
            print(f"Generated monthly recommendations for user {user_id}")
        else:
            print(f"Skipping user {user_id} due to missing credentials or id.")
    print("Scheduled monthly recommendations generation completed for all users.")

# Initialize scheduler
scheduler = BackgroundScheduler()

# Schedule tasks:
# - Update watch history daily at 02:00
scheduler.add_job(scheduled_update_history, CronTrigger(hour=2, minute=0))
# - Update user taste weekly on Sunday at 03:00
scheduler.add_job(scheduled_update_user_taste, CronTrigger(day_of_week='sun', hour=3, minute=0))
# - Generate monthly recommendations on the 1st day of the month at 04:00
scheduler.add_job(scheduled_generate_monthly_recs, CronTrigger(day=1, hour=4, minute=0))

scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()
# ----------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 5335)), reload=True)
