import requests
from plexapi.myplex import MyPlexAccount


class PlexAuthClient:
    def __init__(self, base_url="http://localhost:5333"):
        self.base_url = base_url
    
    def connect_to_plex(self, user_id, connection_type='account'):
        """
        Connect to Plex using user_id
        connection_type: 'servers', 'account', or 'users'
        """
        data = {
            'user_id': user_id,
            'type': connection_type
        }
        
        response = requests.post(f"{self.base_url}/connect", json=data)
        if response.status_code == 200:
            data = response.json()
            plexuser = MyPlexAccount(token=data['token'])
            servers = []
            for resource in plexuser.resources():
                try:
                    server = resource.connect(timeout=600)
                    if server:
                        servers.append(server)
                except Exception as e:
                    print(f"Error connecting to server {resource.name}: {e}")
            return servers           
        return None
       
        
        """         response = requests.post(f"{self.base_url}/connect", json=data)
        if response.status_code == 200:
            data = response.json()
            plexuser = MyPlexAccount(token=data['token'])
  
            return plexuser
            
                return None """

    def get_all_users(self):
        """Get list of all Plex users"""
        return self.connect_to_plex(None, 'users')
