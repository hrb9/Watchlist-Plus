# recbyhistory/overseerr_auth.py
import requests
from plexapi.myplex import MyPlexAccount

class OverseerrPlexClient:
    """
    This client replaces the traditional auth_client by using Overseerr's API to retrieve
    Plex connection details for a given user.
    It assumes that Overseerr exposes an endpoint (e.g., GET /api/v1/plex)
    which returns a JSON object with keys "token" and "servers".
    
    Example response:
    {
        "token": "plex_token_here",
        "servers": [
            {"name": "Plex Server", "url": "http://plex_server:32400"}
        ]
    }
    """
    
    def __init__(self, overseerr_url: str, overseerr_api_token: str):
        self.overseerr_url = overseerr_url.rstrip('/')
        self.headers = {"Authorization": f"Bearer {overseerr_api_token}"}
    
    def connect_to_plex(self, user_id: str):
        """
        Connects to Plex for the given user by calling Overseerr's API to retrieve
        Plex connection details. Returns a list of connected Plex server objects.
        """
        try:
            response = requests.get(
                f"{self.overseerr_url}/api/v1/plex",
                params={"userId": user_id},
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            plex_token = data.get("token")
            servers_info = data.get("servers", [])
            if not plex_token:
                print("No Plex token found in Overseerr response for user:", user_id)
                return None
            
            # Create a Plex account using the token
            plex_account = MyPlexAccount(token=plex_token)
            connected_servers = []
            for server_data in servers_info:
                server_name = server_data.get("name")
                try:
                    server = plex_account.resource(server_name).connect(timeout=600)
                    if server:
                        connected_servers.append(server)
                except Exception as e:
                    print(f"Error connecting to server '{server_name}': {e}")
            return connected_servers
        except Exception as e:
            print(f"Error fetching Plex connection details from Overseerr for user '{user_id}': {e}")
            return None

# Example usage:
if __name__ == "__main__":
    overseerr_url = "http://your-overseerr-url:5055"
    overseerr_api_token = "your_overseerr_api_token_here"
    user_id = "example_user_id"
    
    client = OverseerrPlexClient(overseerr_url, overseerr_api_token)
    plex_servers = client.connect_to_plex(user_id)
    if plex_servers:
        print(f"Connected to {len(plex_servers)} Plex servers for user {user_id}.")
    else:
        print("Failed to connect to any Plex servers.")
