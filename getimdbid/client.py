import requests

class IMDBServiceClient:
    def __init__(self, base_url="http:/getimdbid:5331"):
        self.base_url = base_url
    
    def get_imdb_id(self, plex_item):
        """Get IMDB ID for a Plex item using the microservice"""
        data = {
            "title": plex_item.title,
            "type": plex_item.type,
            "guids": [guid.id for guid in plex_item.guids] if hasattr(plex_item, 'guids') else []
        }
        
        response = requests.post(f"{self.base_url}/getimdbid", json=data)
        if response.status_code == 200:
            return response.json()["imdb_id"]
        return None
