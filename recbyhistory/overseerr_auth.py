# recbyhistory/overseerr_auth.py
import requests
from plexapi.myplex import MyPlexAccount
from config import OVERSEERR_URL, OVERSEERR_API_TOKEN

class OverseerrPlexClient:
    """
    This client retrieves Plex connection details for a given user by calling
    the Overseerr endpoint /api/v1/settings/plex/users.
    
    The endpoint returns a list of Plex user objects. Each object should contain:
      - userId (or id): the Overseerr user ID
      - plexToken: the Plex authentication token for that user
      - servers: a list of Plex servers available for the user (each with a "name" field, etc.)
    
    This client finds the matching Plex settings for the provided user_id and uses the Plex token
    to create a MyPlexAccount, then connects to the listed Plex servers.
    """
    
    def __init__(self):
        self.base_url = OVERSEERR_URL.rstrip('/')
        self.headers = {"Authorization": f"Bearer {OVERSEERR_API_TOKEN}"}
    
    def get_plex_settings_for_user(self, user_id: str):
        """
        Retrieves Plex settings for a given user from Overseerr.
        Endpoint: GET /api/v1/settings/plex/users
        Returns the Plex settings object for the user.
        """
        url = f"{self.base_url}/api/v1/settings/plex/users"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            plex_users = response.json()  # expecting a list of Plex user objects
            # Find the object that matches the provided user_id.
            for plex_user in plex_users:
                # Depending on the API, the key might be "userId" or "id"
                if str(plex_user.get("userId") or plex_user.get("id")) == user_id:
                    return plex_user
            print(f"No Plex settings found for user {user_id}.")
            return None
        except Exception as e:
            print(f"Error fetching Plex settings from Overseerr for user {user_id}: {e}")
            return None

    def connect_to_plex(self, user_id: str):
        """
        Connects to Plex for the given user by using Overseerr's plex settings.
        Returns a list of connected Plex server objects.
        """
        plex_settings = self.get_plex_settings_for_user(user_id)
        if not plex_settings:
            print(f"No Plex settings found for user {user_id}.")
            return None

        plex_token = plex_settings.get("plexToken")
        servers_info = plex_settings.get("servers", [])
        if not plex_token:
            print(f"No Plex token found in settings for user {user_id}.")
            return None

        try:
            plex_account = MyPlexAccount(token=plex_token)
        except Exception as e:
            print(f"Error creating MyPlexAccount for user {user_id} with token {plex_token}: {e}")
            return None

        connected_servers = []
        for server_data in servers_info:
            server_name = server_data.get("name")
            try:
                server = plex_account.resource(server_name).connect(timeout=600)
                if server:
                    connected_servers.append(server)
            except Exception as e:
                print(f"Error connecting to server '{server_name}' for user {user_id}: {e}")
        return connected_servers

# Example usage:
if __name__ == "__main__":
    # Replace these with actual values from your configuration/environment
    test_user_id = "123"  # The Overseerr user ID for which you want to get Plex settings
    client = OverseerrPlexClient()
    servers = client.connect_to_plex(test_user_id)
    if servers:
        print(f"Connected to {len(servers)} Plex server(s) for user {test_user_id}.")
    else:
        print("Failed to connect to any Plex servers.")
