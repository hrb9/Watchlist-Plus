import requests
from plexapi.myplex import MyPlexAccount

class PlexAuthClient:
    def __init__(self, base_url="http://localhost:5333"):
        """
        :param base_url: The base URL of the service providing the /connect endpoint.
                        For example, 'http://auth_service:5333' or 'http://plexauthgui:5332'
        """
        self.base_url = base_url
    
    def connect_to_plex(self, user_id, connection_type='account'):
        """
        Connect to Plex using user_id.
        
        :param user_id: The identifier of the user in your auth_service (or plexauthgui).
        :param connection_type: One of 'servers', 'account', or 'users'.
          - 'users': to retrieve a list of all Plex users (if your /connect endpoint returns them).
          - 'account': to retrieve the Plex token and connect to MyPlexAccount.
          - 'servers': (if relevant) might also be used, though the code below typically is for 'account'.
        
        The /connect endpoint is expected to return JSON in one of these forms:
          {
            "token": <plex_token>,
            ... possibly other fields ...
          }
          or
          {
            "users": [...]
          }
        
        If 'users', it returns the list of user_ids from the JSON.
        Otherwise, for 'account' or 'servers', we take the 'token' and create MyPlexAccount,
        then attempt to connect to all resources (servers).
        
        :return:
          - If connection_type='users': a list of user identifiers from the JSON
          - Otherwise, a list of connected Plex servers (MyPlexServer objects)
            If unsuccessful, returns None
        """
        data = {
            'user_id': user_id,
            'type': connection_type
        }
        
        response = requests.post(f"{self.base_url}/connect", json=data)
        if response.status_code == 200:
            result = response.json()
            
            # If we're asking for 'users', presumably the service returns { "users": [...] }
            if connection_type == 'users':
                return result.get('users', [])
            
            # Otherwise, we interpret 'account' or 'servers'
            plex_token = result.get('token')
            if not plex_token:
                # no token found, cannot proceed
                return None
            
            # Build a MyPlexAccount using the token
            plexuser = MyPlexAccount(token=plex_token)
            
            # Attempt to connect to servers
            servers = []
            for resource in plexuser.resources():
                try:
                    server = resource.connect(timeout=600)  # connect to each resource
                    if server:
                        servers.append(server)
                except Exception as e:
                    print(f"Error connecting to server {resource.name}: {e}")
            return servers
        
        # If not status_code 200, or some error
        return None

    def get_all_users(self):
        """
        Convenience method to retrieve all user IDs from the service
        by calling connect_to_plex(..., connection_type='users').
        Returns a list of user IDs or an empty list if not found.
        """
        return self.connect_to_plex(None, 'users')
