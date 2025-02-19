# watchlistdispatcher/app.py
import os
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
import requests

app = FastAPI(
    title="WatchlistDispatcher",
    description="Dispatcher for sending watchlist requests to Radarr/Sonarr via dynamic Overseerr configuration",
    version="1.0"
)

# Dependency to verify Overseerr token
async def verify_overseerr_token(x_overseerr_token: str = Header(...)):
    expected_token = os.environ.get("OVERSEERR_API_TOKEN")
    if not expected_token or x_overseerr_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid Overseerr Token")
    return x_overseerr_token

# Request model for watchlist addition
class WatchlistRequest(BaseModel):
    user_id: str
    imdb_id: str
    media_type: str  # "movie" or "series"
    plex_token: str

def get_servers_from_overseerr():
    """
    Fetches the Radarr/Sonarr servers from Overseerr using its API.
    Assumes Overseerr exposes a settings endpoint with keys 'radarr' and 'sonarr'.
    """
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://overseerr:5055")
    overseerr_api_token = os.environ.get("OVERSEERR_API_TOKEN")
    headers = {"Authorization": f"Bearer {overseerr_api_token}"}
    
    try:
        # לדוגמה, משתמשים ב־GET /api/v1/settings לשם קבלת ההגדרות
        response = requests.get(f"{overseerr_url}/api/v1/settings", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        servers = []
        # נניח שברשומת ההגדרות ישנם מפתחות "radarr" ו-"sonarr" המכילים מערכים של הגדרות
        for server in data.get("radarr", []):
            servers.append({
                "name": server.get("name", "Radarr Server"),
                "url": server.get("url"),
                "token": server.get("apiKey")  # או "token" בהתאם למבנה הנתונים
            })
        for server in data.get("sonarr", []):
            servers.append({
                "name": server.get("name", "Sonarr Server"),
                "url": server.get("url"),
                "token": server.get("apiKey")
            })
        return servers
    except Exception as e:
        print(f"Error fetching servers from Overseerr: {e}")
        return []

def send_request_to_server(server: dict, payload: dict) -> bool:
    """
    Sends the watchlist request to a given Radarr/Sonarr server.
    Assumes that each server expects a POST request to /api/v1/watchlist.
    """
    try:
        headers = {"Authorization": f"Bearer {server['token']}"}
        resp = requests.post(f"{server['url']}/api/v1/watchlist", json=payload, headers=headers, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Error sending request to {server.get('name', 'Unknown')}: {e}")
        return False

@app.post("/add_to_watchlist", dependencies=[Depends(verify_overseerr_token)])
def add_to_watchlist(request: WatchlistRequest):
    """
    Endpoint to add a media item to the watchlist.
    1. Verify with Overseerr.
    2. Fetch the list of servers dynamically from Overseerr.
    3. Dispatch the request to all the servers.
    """
    # Verify with Overseerr approval (במקרה זה ניתן להוסיף קריאה ל־API נוסף אם נדרש)
    # לדוגמה, אפשר להוסיף כאן קריאה ל־GET /api/v1/request/approval אם קיים כזה
    overseerr_url = os.environ.get("OVERSEERR_URL", "http://overseerr:5055")
    try:
        approval_resp = requests.get(
            f"{overseerr_url}/api/v1/request/approval",
            params={"user_id": request.user_id, "imdb_id": request.imdb_id},
            timeout=10
        )
        if approval_resp.status_code != 200:
            raise HTTPException(status_code=403, detail="Request not approved by Overseerr.")
    except Exception as e:
        raise HTTPException(status_code=403, detail=f"Approval check error: {e}")
    
    payload = {
        "user_id": request.user_id,
        "imdb_id": request.imdb_id,
        "media_type": request.media_type,
        "plex_token": request.plex_token
    }
    
    servers = get_servers_from_overseerr()
    if not servers:
        raise HTTPException(status_code=500, detail="Could not fetch server list from Overseerr.")
    
    success_servers = []
    for server in servers:
        if send_request_to_server(server, payload):
            success_servers.append(server["name"])
        else:
            print(f"Server {server.get('name', 'Unknown')} failed to process the request.")
    
    if not success_servers:
        raise HTTPException(status_code=500, detail="Failed to add to watchlist on all servers.")
    
    return {"status": "OK", "servers_processed": success_servers}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 6000)), reload=True)
